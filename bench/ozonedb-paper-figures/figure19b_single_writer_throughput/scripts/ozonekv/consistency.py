#!/usr/bin/env python3
"""Consistency experiments for OzoneDB. They mirror the cr-sqlite bench.

This script runs the same three measurements that `bench.py` in the cr-sqlite
repo provides (probe-staleness / check-lost-updates / check-convergence). It
runs them through the real client path of OzoneDB: ConsistencyProbe, then
OzoneDBJNI, then libozonedb.so, then the shared Corfu log. The output files
(commits.csv, reads.csv, summary.json) follow the exact same contract. So
crsqlite/bench/scripts/plot/plot_consistency.py can plot both engines side by side.

Run this ON a client node. It needs OZONEDB_HOME, a built libozonedb.so, and a
reachable Corfu server. All probe processes run on that one node, so
System.nanoTime() is a single shared clock. The measured consistency path
still crosses the network, because every write and every remote-apply goes
through the Corfu log node either way.

Each experiment uses its own fresh Corfu stream, with a timestamp suffix. So
it does not disturb the benchmark stream, and it does not replay it.

  python3 consistency.py probe-staleness --write-rate 20 --duration 30
  python3 consistency.py check-lost-updates --workers 2 --increments 2000
  python3 consistency.py check-visibility --rate 20 --duration 15
  python3 consistency.py check-convergence --record-count 1000000 --sample 1000
"""

import argparse
import bisect
import json
import fcntl
import os
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "local"))
from load_local_ycsb_multiproc import (
    _java_binary,
    _make_corfu_config_per_writer,
    _resolve_ycsb_classpath,
    linearizable_corfu_settings,
    partition_records,
)
from visibility_analysis import analyze as _analyze_visibility
from ycsb_config import derive as _derive_addresses

OZONEDB_HOME = os.environ.get("OZONEDB_HOME")
PROBE_CLASS = "site.ycsb.db.ConsistencyProbe"
ENGINE = "ozonedb-corfu"
# Config indices far above any benchmark writer count. So the per-writer
# shared_config_w{i}.json files written here never clobber the files of a sweep.
CFG_IDX_BASE = 900


def _require_home():
    if not OZONEDB_HOME:
        raise EnvironmentError("OZONEDB_HOME environment variable is not set.")


def _outdir(name):
    d = os.path.join(
        OZONEDB_HOME, "bench", "results", "consistency",
        f"{name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
    )
    os.makedirs(d, exist_ok=True)
    return d


def _fresh_dir(path):
    subprocess.run(["rm", "-rf", path])
    os.makedirs(path, exist_ok=True)
    return path


def _load_cfg(path):
    import yaml

    with open(path) as f:
        return _derive_addresses(yaml.safe_load(f))


def _corfu_settings(cfg, stream, linearizable=False):
    s = dict(cfg["corfu"])
    s["stream_name"] = stream
    if linearizable:
        # The same override that run_local_ycsb_multiproc.py --linearizable
        # applies (linearizable_reads=true and trust_background_tail=false).
        # _make_corfu_config_per_writer passes it through to the per-writer
        # shared_config. It makes every get fence on the global log tail
        # (see src/db/db.cpp).
        s = linearizable_corfu_settings(s)
    return s


def _kill_stale_probes():
    """An interrupted driver orphans its probe JVMs. They keep their corfu
    connections and can poison the shared log for every later experiment.
    The entries of their stream raise the global tail that other instances
    fence on. Experiments never overlap by design. So any ConsistencyProbe
    that already runs on this node is such a leftover. Reap them."""
    r = subprocess.run(["pgrep", "-f", PROBE_CLASS], capture_output=True, text=True)
    pids = [p for p in r.stdout.split() if p.strip()]
    if pids:
        print(f"[cleanup] killing stale probe processes: {' '.join(pids)}")
        subprocess.run(["kill", "-9", *pids])
        time.sleep(1)


def _make_configs(cfg, stream, count, name, idx_base=CFG_IDX_BASE,
                  linearizable=False):
    """One shared_config JSON and a fresh scratch db_path per probe process.
    The Corfu endpoint and the stream are shared. That is the point. The
    db_path must be private per process, exactly as in the multiproc runners."""
    data_root = cfg["local"]["run"]["ycsb_data_path"]
    settings = _corfu_settings(cfg, stream, linearizable=linearizable)
    paths = []
    for i in range(count):
        db_path = _fresh_dir(os.path.join(data_root, "consistency", f"{name}-w{i}"))
        paths.append(
            _make_corfu_config_per_writer(idx_base + i, db_path, settings, None)
        )
    return paths


CLASSPATH_CACHE = os.path.join(
    os.environ.get("OZONEDB_HOME") or "", "ycsb", ".consistency-classpath"
)


def _read_cached_classpath():
    """The cached classpath string, or None when it is absent or stale."""
    if not os.path.exists(CLASSPATH_CACHE):
        return None
    with open(CLASSPATH_CACHE) as f:
        cached = f.read().strip()
    first_jar = next(
        (p for p in cached.split(os.pathsep) if p.endswith(".jar")), None
    )
    if cached and (first_jar is None or os.path.exists(first_jar)):
        return cached
    return None


def _classpath(rebuild=False):
    """Persistently cached classpath. The multiproc runners run Maven again
    on every invocation. For a 15-second probe, that Maven pass dominates the
    wall time. So cache the resolved string across invocations, and refresh
    it only on --rebuild or when a cached jar has vanished.

    A file lock guards the cache. run_visibility_cross_node.sh fans out N
    writer PROCESSES over ssh. On the first run after a build, every one of
    them misses the cache and runs Maven at the same moment in the same
    checkout. They then race on ycsb/core/target/, and one loses with
    "Unable to read Checkstyle results xml: ... not \\u0". That writer
    never connects. The reader waits for a connection that never comes,
    until its accept timeout expires. The in-process memo in
    _resolve_ycsb_classpath cannot see other processes. The lock can.
    """
    if not rebuild:
        cached = _read_cached_classpath()
        if cached:
            return cached

    lock_path = CLASSPATH_CACHE + ".lock"
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        # Check again under the lock. The previous holder has very likely
        # written the cache a moment ago. A second Maven run would
        # bring back the race that the lock prevents.
        if not rebuild:
            cached = _read_cached_classpath()
            if cached:
                return cached
        ycsb_path = os.path.join(OZONEDB_HOME, "ycsb")
        cp = _resolve_ycsb_classpath(ycsb_path, "ozonedb")
        with open(CLASSPATH_CACHE, "w") as f:
            f.write(cp)
        return cp


def _probe_cmd(cp, mode, flags):
    cmd = [_java_binary(), "-cp", cp, PROBE_CLASS, mode]
    for k, v in flags.items():
        cmd += [f"--{k}", str(v)]
    return cmd


def _spawn(cmd, log_path):
    log = open(log_path, "w")
    proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT)
    return {"proc": proc, "log": log, "log_path": log_path}


def _wait_ready_then_go(out, ready_files, procs, timeout_s=300):
    """File barrier. Wait for every probe process to finish its openDB(), then
    release them all at once. The JVM start, the Corfu connect and the stream
    replay can take seconds, and the time varies."""
    deadline = time.time() + timeout_s
    while not all(os.path.exists(f) for f in ready_files):
        for p in procs:
            if p["proc"].poll() is not None and p["proc"].returncode != 0:
                raise RuntimeError(
                    f"probe process died before ready (rc={p['proc'].returncode}); "
                    f"see {p['log_path']}"
                )
        if time.time() > deadline:
            raise TimeoutError(
                f"probe processes not ready within {timeout_s}s. If their logs "
                "show the corfu tailer polling but openDB never returning, the "
                "corfu server has entries from other streams in its global log "
                "and the engine's fenced reads block forever on a fresh stream "
                "-- restart corfu_server first (the YCSB sweep restarts it "
                "before every run for the same reason)."
            )
        time.sleep(0.2)
    go = os.path.join(out, "go")
    with open(go, "w"):
        pass
    return time.time()


def _wait_all(procs):
    rcs = []
    for p in procs:
        rcs.append(p["proc"].wait())
        if rcs[-1] != 0:
            print(f"[warning] probe process rc={rcs[-1]}, see {p['log_path']}")
    return rcs


def _reap(procs):
    for p in procs:
        if p["proc"].poll() is None:
            p["proc"].kill()
        p["log"].close()


def _last_json_line(log_path):
    last = None
    with open(log_path, errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                last = line
    if last is None:
        raise RuntimeError(f"no JSON output line found in {log_path}")
    return json.loads(last)


def _pct(xs, p):
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(p / 100.0 * len(xs)))]


# ================================================================== staleness


def cmd_probe_staleness(cfg, args):
    """One process bumps a sequence key through the shared log at a fixed
    rate. A second process, with its own DB instance and its own Corfu tailer,
    polls the key. Staleness on this engine is the log apply lag, in
    milliseconds. On cr-sqlite it is the anti-entropy interval. Same files,
    same arithmetic."""
    out = _outdir("staleness")
    tag = os.path.basename(out).split("-", 1)[1]
    stream = args.stream or f"{cfg['corfu']['stream_name']}-probe-{tag}"
    _kill_stale_probes()
    cfgs = _make_configs(cfg, stream, 2, f"staleness-{tag}",
                         linearizable=args.linearizable)
    cp = _classpath(args.rebuild)

    ready_w = os.path.join(out, "ready-writer")
    ready_r = os.path.join(out, "ready-reader")
    go = os.path.join(out, "go")
    common = {"out-dir": out, "go-file": go, "key": "__probe__"}
    writer_cmd = _probe_cmd(cp, "write-probe", {
        **common, "config": cfgs[0], "ready-file": ready_w,
        "rate": args.write_rate, "duration-s": args.duration,
    })
    reader_cmd = _probe_cmd(cp, "read-probe", {
        **common, "config": cfgs[1], "ready-file": ready_r,
        "duration-s": args.duration + args.settle,
        "read-interval-ms": args.read_interval_ms,
    })

    procs = [
        _spawn(writer_cmd, os.path.join(out, "writer.log")),
        _spawn(reader_cmd, os.path.join(out, "reader.log")),
    ]
    try:
        _wait_ready_then_go(out, [ready_w, ready_r], procs)
        _wait_all(procs)
    finally:
        _reap(procs)

    commits = []
    with open(os.path.join(out, "commits.csv")) as f:
        next(f)
        for line in f:
            s, t = line.strip().split(",")
            commits.append((int(s), int(t)))
    reads = []
    with open(os.path.join(out, "reads.csv")) as f:
        next(f)
        for line in f:
            parts = line.strip().split(",")
            # The 3rd column (t_start_ns) was added for the linearizability
            # check. Tolerate 2-column files from older probes.
            reads.append((int(parts[0]), int(parts[1]),
                          int(parts[2]) if len(parts) > 2 else int(parts[0])))

    commit_times = [t for _, t in commits]
    stale = 0
    version_lags = []
    staleness_ms = []
    violations = 0
    for t_read, seen, t_start in reads:
        latest = bisect.bisect_right(commit_times, t_read) - 1
        lag = max(0, latest - seen)
        version_lags.append(lag)
        if lag > 0:
            stale += 1
            staleness_ms.append((t_read - commit_times[seen + 1]) / 1e6)
        # Real-time (linearizability) check, judged against the START of the
        # get. A get that overlaps a commit may correctly return the older
        # value. Only a miss of a write that committed before the get began
        # violates linearizability. Strict mode should drive this to zero.
        # stale_read_fraction (against t_read, the cr-sqlite-compatible
        # metric) stays nonzero from reads that overlap their write.
        latest_at_start = bisect.bisect_right(commit_times, t_start) - 1
        if latest_at_start - seen > 0:
            violations += 1

    summary = {
        "engine": ENGINE + ("-linearizable" if args.linearizable else ""),
        "linearizable_reads": args.linearizable,
        "writes": commits[-1][0] if commits else 0,
        "reads": len(reads),
        "stale_reads": stale,
        "stale_read_fraction": (stale / len(reads)) if reads else 0.0,
        "version_lag_p50": _pct(version_lags, 50),
        "version_lag_p95": _pct(version_lags, 95),
        "version_lag_max": max(version_lags) if version_lags else 0,
        "staleness_ms_p50": round(_pct(staleness_ms, 50), 2),
        "staleness_ms_p95": round(_pct(staleness_ms, 95), 2),
        "staleness_ms_max": round(max(staleness_ms), 2) if staleness_ms else 0.0,
        "linearizability_violations": violations,
        "linearizability_violation_fraction":
            (violations / len(reads)) if reads else 0.0,
        # This engine has no anti-entropy interval. 0 keeps the type of the
        # field for the plot scripts that read it.
        "sync_interval_ms": 0,
        "write_rate_per_s": args.write_rate,
        "duration_s": args.duration,
        "read_interval_ms": args.read_interval_ms,
        "stream": stream,
    }
    with open(os.path.join(out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))
    print(
        f"\n=> {stale} of {len(reads)} reads on the second instance were stale "
        f"({summary['stale_read_fraction']:.1%}); staleness here is Corfu apply "
        f"lag, not an anti-entropy interval. Results in {out}"
    )


# ================================================================= visibility


def cmd_check_visibility(cfg, args):
    """Two benchmarks from one run over YCSB-keyspace inserts (PBS-style).
    A writer inserts brand-new keys and acks each key into a notify file.
    A reader polls every acked key until it appears. B2 (miss rate): did the
    FIRST get of the reader find the key? That get starts after the ack by
    construction, so no clocks are involved. B1 (t-visibility): how long
    after the ack did some get find the key? New keys make "not found" an
    unambiguous staleness, because no older value can pass as success."""
    out = _outdir("visibility")
    tag = os.path.basename(out).split("-", 1)[1]
    stream = args.stream or f"{cfg['corfu']['stream_name']}-vis-{tag}"
    n = max(1, args.writers)
    _kill_stale_probes()
    # Writers are 0..n-1, the reader is instance n. Every writer shares the
    # one Corfu stream. That is the point of the scaling experiment. Disjoint
    # --key-base ranges keep the keyspaces apart.
    cfgs = _make_configs(cfg, stream, n + 1, f"visibility-{tag}",
                         linearizable=args.linearizable)
    cp = _classpath(args.rebuild)

    go = os.path.join(out, "go")
    done = os.path.join(out, "done")
    ready_r = os.path.join(out, "ready-reader")
    ready_ws = [os.path.join(out, f"ready-writer-{w}") for w in range(n)]
    notify_files = [os.path.join(out, f"notify-{w}.csv") for w in range(n)]

    writer_procs = []
    for w in range(n):
        writer_procs.append(_spawn(_probe_cmd(cp, "insert-probe", {
            "config": cfgs[w], "ready-file": ready_ws[w], "go-file": go,
            "out-dir": out, "notify-file": notify_files[w],
            "inserts-file": f"inserts-w{w}.csv",
            "key-base": w * 10_000_000,
            "rate": args.rate, "duration-s": args.duration,
            "value-size": args.value_size,
        }), os.path.join(out, f"writer-{w}.log")))
    reader_proc = _spawn(_probe_cmd(cp, "visibility-probe", {
        "config": cfgs[n], "ready-file": ready_r, "go-file": go,
        "out-dir": out, "notify-file": ",".join(notify_files),
        "done-file": done,
        # Strict runs fence once per tick and share the fence across the
        # whole pending batch (DB::sync). This is the analog of the cr-sqlite
        # /barrier. Without it, each get fences on its own, and the ~ms
        # service time of the reader queues at high writer counts. That
        # inflates the tail with probe-side waits. Misses are the same either way.
        "tick-fence": "true" if args.linearizable else "false",
        "poll-ms": args.poll_ms, "key-timeout-s": args.key_timeout_s,
        "run-timeout-s": args.duration + args.key_timeout_s + 120,
    }), os.path.join(out, "reader.log"))
    procs = writer_procs + [reader_proc]
    try:
        _wait_ready_then_go(out, ready_ws + [ready_r], procs)
        # The writers do not touch the done file themselves. With n of
        # them, "done" means that ALL writers have exited, and only the
        # orchestrator can see that. The reader then drains every notify
        # file to EOF before it finishes.
        _wait_all(writer_procs)
        with open(done, "w"):
            pass
        _wait_all([reader_proc])
    finally:
        _reap(procs)

    _analyze_visibility(out, ENGINE, args.linearizable, cross_node=False, params={
        "rate_per_s": args.rate,
        "writers": n,
        "aggregate_rate_per_s": n * args.rate,
        "tick_fence": bool(args.linearizable),
        "duration_s": args.duration,
        "value_size": args.value_size,
        "poll_ms": args.poll_ms,
        "stream": stream,
    })


def cmd_visibility_reader(cfg, args):
    """Reader half of the cross-node visibility experiment. Listen for the
    TCP ack notifications of the writer, and poll every notified key.
    run_visibility_cross_node.sh starts this on clients[1]. The connect of
    the writer succeeds only when this probe listens, that is, after its
    openDB. So the socket handshake is the start barrier. The probe leaves
    visibility.csv in --out-dir for the laptop to pull. Home dirs are NOT
    shared between nodes."""
    out = os.path.join(OZONEDB_HOME, args.out_dir)
    os.makedirs(out, exist_ok=True)
    _kill_stale_probes()
    cfgs = _make_configs(cfg, args.stream, 1,
                         f"visx-reader-{os.path.basename(out)}",
                         idx_base=990, linearizable=args.linearizable)
    cp = _classpath(args.rebuild)
    cmd = _probe_cmd(cp, "visibility-probe", {
        "config": cfgs[0], "out-dir": out, "notify-listen": args.port,
        "notify-writers": args.writers,
        "tick-fence": "true" if args.linearizable else "false",
        "poll-ms": args.poll_ms, "key-timeout-s": args.key_timeout_s,
        "run-timeout-s": args.run_timeout_s,
    })
    proc = _spawn(cmd, os.path.join(out, "reader.log"))
    try:
        rc = proc["proc"].wait()
    finally:
        _reap([proc])
    if rc != 0:
        raise RuntimeError(f"visibility-probe rc={rc}, see {proc['log_path']}")
    print(f"reader done, visibility.csv in {out}")


def cmd_visibility_writer(cfg, args):
    """Writer half of the cross-node visibility experiment. Insert new keys,
    and ack each key over TCP to the reader at --connect (the LAN address
    of the reader). The call blocks until the insert phase ends. The socket
    close is the done signal that the reader drains against.

    This function does NOT call _kill_stale_probes(), on purpose. In the
    multi-writer flow, N writer invocations run at the same time on this
    node. Each later one would SIGKILL its siblings that already run,
    because the kill matches every ConsistencyProbe JVM. The laptop driver
    performs ONE stale-probe kill over ssh before it starts the writer fleet."""
    out = os.path.join(OZONEDB_HOME, args.out_dir)
    os.makedirs(out, exist_ok=True)
    # Per-worker config index, db_path and logs. N writer invocations share
    # this node and this out-dir. So the worker index must key everything
    # they create (930+w stays clear of the 990 of the reader).
    cfgs = _make_configs(cfg, args.stream, 1,
                         f"visx-writer{args.worker}-{os.path.basename(out)}",
                         idx_base=930 + args.worker,
                         linearizable=args.linearizable)
    cp = _classpath(args.rebuild)
    cmd = _probe_cmd(cp, "insert-probe", {
        "config": cfgs[0], "out-dir": out, "notify-connect": args.connect,
        "key-base": args.worker * 10_000_000,
        "inserts-file": f"inserts-w{args.worker}.csv",
        "rate": args.rate, "duration-s": args.duration,
        "value-size": args.value_size,
    })
    proc = _spawn(cmd, os.path.join(out, f"writer-{args.worker}.log"))
    try:
        rc = proc["proc"].wait()
    finally:
        _reap([proc])
    if rc != 0:
        raise RuntimeError(f"insert-probe rc={rc}, see {proc['log_path']}")
    print(f"writer done, inserts.csv in {out}")


# =============================================================== lost updates


def cmd_check_lost_updates(cfg, args):
    """K read-modify-write increments of one counter, split across N writer
    processes on the shared log. The KV surface of OzoneDB is get/put, with
    no CAS and no transactions. So the RMW race window is one log append plus
    the apply latency, in milliseconds. On cr-sqlite it is the sync interval.
    Unlike cr-sqlite, the log never discards an acknowledged put. Appends are
    totally ordered, and put returns only after the record is sequenced.
    --rate paces each worker to sweep the conflict window."""
    out = _outdir("lost-updates")
    tag = os.path.basename(out).split("-", 2)[2]
    stream = args.stream or f"{cfg['corfu']['stream_name']}-lostupd-{tag}"
    n = args.workers
    _kill_stale_probes()
    # The workers, the seed and the final reader each get their own instance
    cfgs = _make_configs(cfg, stream, n + 2, f"lost-updates-{tag}",
                         linearizable=args.linearizable)
    cp = _classpath(args.rebuild)

    seed_cmd = _probe_cmd(cp, "put-long", {
        "config": cfgs[n], "key": "__counter__", "value": 0,
    })
    with open(os.path.join(out, "seed.log"), "w") as log:
        subprocess.run(seed_cmd, stdout=log, stderr=subprocess.STDOUT, check=True)

    per_worker = partition_records(args.increments, n)
    go = os.path.join(out, "go")
    ready_files = []
    procs = []
    for i, (_, cnt) in enumerate(per_worker):
        ready = os.path.join(out, f"ready-{i}")
        ready_files.append(ready)
        cmd = _probe_cmd(cp, "counter-worker", {
            "config": cfgs[i], "key": "__counter__", "increments": cnt,
            "rate": args.rate, "worker": i, "out-dir": out,
            "ready-file": ready, "go-file": go,
        })
        procs.append(_spawn(cmd, os.path.join(out, f"worker-{i}.log")))

    try:
        t_go = _wait_ready_then_go(out, ready_files, procs)
        _wait_all(procs)
        elapsed = time.time() - t_go
    finally:
        _reap(procs)

    workers = []
    for i in range(n):
        with open(os.path.join(out, f"worker-{i}.json")) as f:
            workers.append(json.load(f))
    acked = sum(w["acked"] for w in workers)

    time.sleep(args.settle)
    final_cmd = _probe_cmd(cp, "get-long", {
        "config": cfgs[n + 1], "key": "__counter__",
        "wait-stable": "true", "poll-ms": 500, "timeout-s": 120,
    })
    final_log = os.path.join(out, "final.log")
    with open(final_log, "w") as log:
        subprocess.run(final_cmd, stdout=log, stderr=subprocess.STDOUT, check=True)
    f_val = _last_json_line(final_log)["value"]

    k = args.increments
    summary = {
        "engine": ENGINE + ("-linearizable" if args.linearizable else ""),
        "linearizable_reads": args.linearizable,
        "mode": "ozonedb writers (shared Corfu log)",
        "replicas": n,
        "workers": n,
        "increments_issued": k,
        "increments_acked": acked,
        "final_counter": f_val,
        "final_per_replica": [f_val],
        "last_written_per_worker": [w["last_written"] for w in workers],
        "lost_updates": k - f_val,
        "lost_fraction": (k - f_val) / k,
        "elapsed_s": round(elapsed, 2),
        "convergence_s": round(args.settle, 2),
        "sync_interval_ms": 0,
        "rate_per_worker_per_s": args.rate,
        "stream": stream,
    }
    with open(os.path.join(out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))
    verdict = (
        "no updates lost"
        if k == f_val
        else f"{k - f_val} of {k} increments lost ({(k - f_val) / k:.1%}) -- "
        "get+put RMW races within the ~ms apply window; no acknowledged put "
        "was discarded by the log itself"
    )
    print(f"\n=> final counter {f_val} after {k} increments: {verdict}. Results in {out}")


# ================================================================ convergence


def cmd_check_convergence(cfg, args):
    """Open N fresh instances of the same stream (default: the benchmark
    stream). On each instance, md5 a sample of the YCSB keyspace until the
    digest is stable. Equal digests mean that every instance materialized
    the same totally-ordered state. stable_after_s includes the replay time
    of the fresh instance."""
    out = _outdir("convergence")
    tag = os.path.basename(out).split("-", 1)[1]
    stream = args.stream or cfg["corfu"]["stream_name"]
    record_cnt = args.record_count or int(cfg["local"]["run"]["record_cnt"])
    sample_every = max(1, record_cnt // args.sample)
    _kill_stale_probes()
    cfgs = _make_configs(cfg, stream, args.instances, f"convergence-{tag}",
                         linearizable=args.linearizable)
    cp = _classpath(args.rebuild)

    procs = []
    logs = []
    for i in range(args.instances):
        cmd = _probe_cmd(cp, "hash", {
            "config": cfgs[i], "record-count": record_cnt,
            "sample-every": sample_every, "wait-stable": "true",
            "poll-ms": args.poll_ms, "timeout-s": args.timeout_s,
        })
        log_path = os.path.join(out, f"hash-{i}.log")
        logs.append(log_path)
        procs.append(_spawn(cmd, log_path))
    _wait_all(procs)
    _reap(procs)

    instances = [_last_json_line(lp) for lp in logs]
    for i, inst in enumerate(instances):
        print(
            f"instance {i}: checked={inst['checked']} present={inst['present']} "
            f"md5={inst['md5']} stable_after_s={inst['stable_after_s']}"
        )
    equal = len({inst["md5"] for inst in instances}) == 1
    summary = {
        "engine": ENGINE + ("-linearizable" if args.linearizable else ""),
        "linearizable_reads": args.linearizable,
        "stream": stream,
        "record_count": record_cnt,
        "sample_every": sample_every,
        "instances": instances,
        "hashes_equal": equal,
    }
    with open(os.path.join(out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(
        f"\n=> {'CONVERGED: all' if equal else 'DIVERGED: not all'} "
        f"{args.instances} instances materialize identical state. Results in {out}"
    )
    if not equal:
        sys.exit(1)


# ======================================================================= main


def main():
    _require_home()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--linearizable", action="store_true",
                   help="set linearizable_reads=true in the shared_config "
                        "(strict cross-writer reads)")
    p.add_argument("--rebuild", action="store_true",
                   help="re-run the Maven classpath resolution instead of using the cache")
    p.add_argument(
        "--config",
        default=os.path.join(OZONEDB_HOME, "bench/scripts/config/ycsb.yaml"),
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("probe-staleness", help="stale reads across two instances")
    sp.add_argument("--write-rate", type=float, default=20.0, help="probe writes/s")
    sp.add_argument("--duration", type=int, default=30, help="write phase seconds")
    sp.add_argument("--read-interval-ms", type=int, default=5)
    sp.add_argument("--settle", type=int, default=5,
                    help="extra reader seconds after the writer stops")
    sp.add_argument("--stream", default=None, help="override the probe stream name")

    sp = sub.add_parser("check-lost-updates",
                        help="concurrent RMW increments across writer processes")
    sp.add_argument("--workers", type=int, default=2)
    sp.add_argument("--increments", type=int, default=2000, help="total across workers")
    sp.add_argument("--rate", type=float, default=0.0,
                    help="increments/s per worker (0 = as fast as possible)")
    sp.add_argument("--settle", type=float, default=3.0,
                    help="seconds to wait before the final read")
    sp.add_argument("--stream", default=None)

    sp = sub.add_parser("check-visibility",
                        help="insert visibility: retry-until-found latency "
                             "+ first-read miss rate (PBS-style)")
    sp.add_argument("--rate", type=float, default=20.0,
                    help="inserts/s PER WRITER (aggregate = writers x rate)")
    sp.add_argument("--writers", type=int, default=1,
                    help="concurrent writer processes, all on the one shared "
                         "stream, disjoint key ranges (scaling experiments)")
    sp.add_argument("--duration", type=int, default=15, help="insert phase seconds")
    sp.add_argument("--value-size", type=int, default=1000, help="value bytes")
    sp.add_argument("--poll-ms", type=int, default=1,
                    help="reader poll interval (bounds B1 resolution)")
    sp.add_argument("--key-timeout-s", type=int, default=30,
                    help="give up on a key after this long (>> sync interval)")
    sp.add_argument("--stream", default=None)

    sp = sub.add_parser("visibility-reader",
                        help="cross-node reader role (driven by run_visibility_cross_node.sh)")
    sp.add_argument("--out-dir", required=True,
                    help="run dir relative to OZONEDB_HOME")
    sp.add_argument("--stream", required=True)
    sp.add_argument("--port", type=int, default=7911)
    sp.add_argument("--writers", type=int, default=1,
                    help="writer connections to accept before the go barrier")
    sp.add_argument("--poll-ms", type=int, default=1)
    sp.add_argument("--key-timeout-s", type=int, default=30)
    sp.add_argument("--run-timeout-s", type=int, default=600)

    sp = sub.add_parser("visibility-writer",
                        help="cross-node writer role (driven by run_visibility_cross_node.sh)")
    sp.add_argument("--out-dir", required=True)
    sp.add_argument("--stream", required=True)
    sp.add_argument("--connect", required=True, help="reader LAN host:port")
    sp.add_argument("--worker", type=int, default=0,
                    help="writer index: disjoint key range, config slot, logs")
    sp.add_argument("--rate", type=float, default=20.0)
    sp.add_argument("--duration", type=int, default=15)
    sp.add_argument("--value-size", type=int, default=1000)

    sp = sub.add_parser("check-convergence",
                        help="hash a key sample on N fresh instances of one stream")
    sp.add_argument("--record-count", type=int, default=None,
                    help="YCSB recordcount of the stream (default: local.run.record_cnt)")
    sp.add_argument("--sample", type=int, default=1000, help="approx keys to check")
    sp.add_argument("--instances", type=int, default=2)
    sp.add_argument("--poll-ms", type=int, default=2000)
    sp.add_argument("--timeout-s", type=int, default=900)
    sp.add_argument("--stream", default=None,
                    help="stream to check (default: the benchmark stream)")

    args = p.parse_args()
    cfg = _load_cfg(args.config)
    if args.cmd == "probe-staleness":
        cmd_probe_staleness(cfg, args)
    elif args.cmd == "check-lost-updates":
        cmd_check_lost_updates(cfg, args)
    elif args.cmd == "check-visibility":
        cmd_check_visibility(cfg, args)
    elif args.cmd == "visibility-reader":
        cmd_visibility_reader(cfg, args)
    elif args.cmd == "visibility-writer":
        cmd_visibility_writer(cfg, args)
    elif args.cmd == "check-convergence":
        cmd_check_convergence(cfg, args)


if __name__ == "__main__":
    main()
