#!/usr/bin/env python3
"""The cr-sqlite YCSB benchmark driver.

There is one entry point, with subcommands instead of per-phase script copies:

  performance (result files byte-compatible with the ozonedb harness):
    bench.py load   [--num_writers N] [--offset K --total_writers M]
    bench.py run    [--num_writers N] [--workloads a,c] [--trial T]
                    [--offset K --total_writers M] [--max_exec_time S]
                    [--read-barrier]
    bench.py sweep  [--workloads "a b c d f"] [--writers-list "1 2 4 8"]
                    [--trials N] [--duration S] [--load]
    bench.py multinode [--phase load|run] [--writers_per_host N] ...

  consistency (the "cr-sqlite is not strongly consistent" experiments):
    bench.py probe-staleness     [--duration 60] [--write-rate 100]
                                 [--num-replicas 2] [--interval-ms ...]
    bench.py check-lost-updates  [--increments 10000] [--num-replicas 2]
                                 [--control] [--read-barrier]
    bench.py check-visibility    [--rate 20] [--duration 15]
                                 [--interval-ms ...] [--read-barrier]
    bench.py check-convergence   --dbs a.db b.db | --endpoints h:p,h:p

Writer model: writer j owns replica file j. A sync daemon per replica
exchanges crsql_changes on sync.interval_ms (see ycsb.yaml). Global writer
indices span hosts: host i runs writers [i*wph, (i+1)*wph).

All knobs come from bench/scripts/config/ycsb.yaml. Flags override them.
Run/sweep results land in bench/results/local/$CRSQLITE_RUN_TAG/.
"""

import argparse
import json
import os
import queue
import socket
import struct
import subprocess
import sys
import threading
import time
from datetime import datetime
from urllib.request import urlopen

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

import ycsb_config
from generate_workload import generate_workload
from bench_lib import replicas, results, sync
from bench_lib import ycsb as ylib

RUN_TAG_ENV = "CRSQLITE_RUN_TAG"
# In multi-node runs every host performs its own convergence wait. Daemons
# stay up this long after local convergence, so that slower hosts can finish
# their confirmation /hash round against us.
POST_CONVERGENCE_GRACE_S = 30


def crsqlite_home():
    home = os.environ.get("CRSQLITE_HOME")
    if not home:
        raise EnvironmentError("CRSQLITE_HOME environment variable is not set.")
    return home


def results_root():
    return os.path.join(crsqlite_home(), "bench", "results", "local")


def load_cfg(path):
    return ycsb_config.load(path)


def _only_crsqlite(db_names):
    for db in db_names:
        if db != "crsqlite":
            raise ValueError(f"this harness only benchmarks crsqlite (got db_name={db})")
    return db_names


def _convergence_knobs(cfg):
    c = cfg.get("convergence") or {}
    return int(c.get("poll_ms", 1000)), int(c.get("timeout_s", 900))


def _workload_path(wl, ks, opcnt, rc):
    generate_workload(wl, ks, opcnt, rc)
    return os.path.join(
        crsqlite_home(), "ycsb", "workloads", "generated_workloads",
        f"workload{wl}_{ks}_{opcnt}_{rc}",
    )


def _start_local_daemons(cfg, local_indices, db_paths, all_eps, syncd_dir, extension,
                         assume_converged=False):
    """Start one daemon per local writer. Wait until the local ones respond.

    assume_converged is for the run phase only. There the replicas start as
    converged caches of one load. The daemons stamp their cursors at the peer
    frontiers instead of a re-pull of the history. Then we must gate on the
    WHOLE mesh being ready, not only on the local daemons. A writer here must
    not start before a remote daemon has stamped its cursor for this host.
    Otherwise that daemon would skip the write."""
    synchronous = (cfg.get("crsqlite") or {}).get("synchronous", "FULL")
    procs = []
    for j, db_path in zip(local_indices, db_paths):
        procs.append(
            sync.start_daemon(
                cfg, j, db_path, all_eps, extension,
                log_path=os.path.join(syncd_dir, f"w{j}.log"),
                metrics_path=os.path.join(syncd_dir, f"w{j}.csv"),
                synchronous=synchronous,
                assume_converged=assume_converged,
            )
        )
    if assume_converged:
        sync.wait_until_up(list(all_eps), timeout_s=240)
    else:
        sync.wait_until_up([all_eps[j] for j in local_indices])
    return procs


# ======================================================================= load

def cmd_load(cfg, num_writers, offset, total_writers):
    load_cfg_ = cfg["local"]["load"]
    record_cnts = load_cfg_["record_cnt"]
    key_sizes = load_cfg_["key_size"]
    db_names = _only_crsqlite(load_cfg_["db_name"])
    data_path = load_cfg_["ycsb_data_path"]
    threads = load_cfg_["threads"]
    repeated = load_cfg_["repeated"]

    if total_writers is None:
        total_writers = num_writers
    if total_writers < offset + num_writers:
        raise ValueError("total_writers must be >= offset + num_writers")
    multinode = total_writers != num_writers or offset != 0

    os.makedirs(data_path, exist_ok=True)
    result_path = results_root()
    os.makedirs(result_path, exist_ok=True)
    extension = ycsb_config.crsqlite_extension_path(cfg)
    syncing = sync.sync_settings(cfg)["enabled"]
    poll_ms, timeout_s = _convergence_knobs(cfg)
    classpath = ylib.resolve_classpath()
    os.chdir(os.path.join(crsqlite_home(), "ycsb"))

    for rc in record_cnts:
        rc = int(rc)
        for ks in key_sizes:
            workload_path = _workload_path("a", ks, rc, rc)
            partitions = ylib.partition_records(rc, total_writers)
            local = list(range(offset, offset + num_writers))

            for db_name in db_names:
                for _round in range(repeated):
                    for thread in [str(t) for t in threads]:
                        # Create fresh replica dirs for this machine's writers.
                        db_paths = []
                        for j in local:
                            d = replicas.cached_dir(data_path, db_name, ks, rc, j)
                            replicas.fresh_dir(d)
                            db_paths.append(replicas.replica_db(d))

                        all_eps = sync.endpoints(cfg, total_writers, num_writers, multinode)
                        procs = []
                        syncd_dir = os.path.join(
                            result_path, f"_syncd-load-{db_name}-{ks}-{rc}"
                        )
                        if syncing:
                            procs = _start_local_daemons(
                                cfg, local, db_paths, all_eps, syncd_dir, extension
                            )

                        jobs = []
                        per_writer_files = []
                        try:
                            for li, j in enumerate(local):
                                start, count = partitions[j]
                                props = ylib.crsqlite_props(cfg, db_paths[li], extension)
                                props += [
                                    "-p", f"recordcount={rc}",
                                    "-p", f"insertstart={start}",
                                    "-p", f"insertcount={count}",
                                    "-p", f"operationcount={count}",
                                    "-p", "status.interval=1",
                                ]
                                cmd = ylib.build_cmd(
                                    classpath, workload_path, thread, props, ["-load"]
                                )
                                rf = os.path.join(
                                    result_path,
                                    results.load_writer_file(
                                        ks, rc, db_name, j, total_writers, thread
                                    ),
                                )
                                jobs.append((cmd, rf))
                                per_writer_files.append(rf)

                            _rcs, wall_ms = ylib.spawn_parallel(jobs)

                            extra = [f"[AGGREGATE], SyncEnabled, {syncing}"]
                            if syncing:
                                print(
                                    f"[load] writers done in {wall_ms/1000.0:.1f}s; "
                                    f"waiting for {total_writers} replicas to converge "
                                    f"on {rc} rows"
                                )
                                conv_s = sync.wait_for_convergence(
                                    all_eps, expected_rows=rc,
                                    poll_ms=poll_ms, timeout_s=timeout_s,
                                )
                                print(f"[load] converged in {conv_s:.1f}s")
                                extra.append(
                                    f"[AGGREGATE], LoadConvergenceTime(ms), {int(conv_s*1000)}"
                                )
                                # This is the achieved round period while
                                # the mesh drained the load. At 64 replicas
                                # this is the number behind LoadConvergenceTime.
                                try:
                                    extra.extend(sync.round_stat_lines(all_eps, local))
                                except Exception as e:
                                    print(f"[sync] WARNING: round_stats scrape failed: {e}")
                                if multinode:
                                    time.sleep(POST_CONVERGENCE_GRACE_S)
                        finally:
                            sync.stop_daemons(procs)

                        agg = os.path.join(
                            result_path,
                            results.load_agg_file(ks, rc, db_name, total_writers, thread)
                            if not multinode
                            else results.load_agg_file(
                                ks, rc, db_name,
                                f"{num_writers}of{total_writers}_off{offset}", thread,
                            ),
                        )
                        results.write_aggregate(
                            agg, per_writer_files, wall_ms, num_writers, extra_lines=extra
                        )


# ======================================================================== run

def cmd_run(cfg, num_writers, workloads, offset, total_writers, trial, max_exec_time,
            read_barrier=False):
    run_cfg = cfg["local"]["run"]
    rc = int(run_cfg["record_cnt"])  # scalar, deliberately (see ycsb.yaml)
    operation_cnts = run_cfg["operation_cnt"]
    key_sizes = run_cfg["key_size"]
    db_names = _only_crsqlite(run_cfg["db_name"])
    data_path = run_cfg["ycsb_data_path"]
    threads = run_cfg["threads"]
    repeated = run_cfg["repeated"]
    workload_names = workloads or run_cfg["workload_name"]

    if total_writers is None:
        total_writers = num_writers
    if total_writers < offset + num_writers:
        raise ValueError("total_writers must be >= offset + num_writers")
    if trial < 1:
        raise ValueError("trial must be >= 1")
    multinode = total_writers != num_writers or offset != 0

    run_tag = os.environ.get(RUN_TAG_ENV) or datetime.now().strftime("%Y%m%d-%H%M%S")
    result_path = os.path.join(results_root(), run_tag)
    os.makedirs(result_path, exist_ok=True)
    print(f"[results] writing to {result_path}")

    extension = ycsb_config.crsqlite_extension_path(cfg)
    syncing = sync.sync_settings(cfg)["enabled"]
    interval_ms = sync.sync_settings(cfg)["interval_ms"]
    if read_barrier and not syncing:
        raise ValueError("read_barrier requires sync.enabled (the barrier IS a sync round)")
    # Fenced-read runs get their own engine label, so that async and syncread
    # numbers can never mix in a results dir or a plot. The replica cache/run
    # dirs keep the plain db_name. The data is identical either way.
    label_suffix = "-syncread" if read_barrier else ""
    poll_ms, timeout_s = _convergence_knobs(cfg)
    classpath = ylib.resolve_classpath()
    os.chdir(os.path.join(crsqlite_home(), "ycsb"))

    for opcnt in operation_cnts:
        opcnt = int(opcnt)
        op_partitions = ylib.partition_records(opcnt, total_writers)
        local = list(range(offset, offset + num_writers))

        for ks in key_sizes:
            for wl in workload_names:
                workload_path = _workload_path(wl, ks, opcnt, rc)
                for db_name in db_names:
                    for _round in range(repeated):
                        for thread in [str(t) for t in threads]:
                            print(f"[trial {trial}] workload={wl} db={db_name} thread={thread}")

                            run_db_paths = []
                            live = []  # local writer indices with cached data
                            for j in local:
                                cached = replicas.cached_dir(data_path, db_name, ks, rc, j)
                                rdir = replicas.run_dir(data_path, db_name, ks, wl, opcnt, j)
                                if not os.path.exists(cached):
                                    print(f"cached_data {cached} missing, skipping writer {j}...")
                                    continue
                                replicas.copy_cached_to_run(cached, rdir)
                                live.append(j)
                                run_db_paths.append(replicas.replica_db(rdir))
                            if not live:
                                continue

                            all_eps = sync.endpoints(cfg, total_writers, num_writers, multinode)
                            procs = []
                            syncd_dir = os.path.join(result_path, f"_syncd-workload{wl}-trial{trial}")
                            if syncing:
                                # Run replicas are converged caches of one
                                # load. Skip the history re-pull (see
                                # _start_local_daemons).
                                procs = _start_local_daemons(
                                    cfg, live, run_db_paths, all_eps, syncd_dir,
                                    extension, assume_converged=True,
                                )

                            jobs = []
                            per_writer_files = []
                            extra = []
                            try:
                                for li, j in enumerate(live):
                                    _, writer_ops = op_partitions[j]
                                    props = ylib.crsqlite_props(
                                        cfg, run_db_paths[li], extension,
                                        sync_endpoint=all_eps[j] if read_barrier else None,
                                    )
                                    props += [
                                        "-p", f"recordcount={rc}",
                                        "-p", f"operationcount={writer_ops}",
                                        "-p", "status.interval=1",
                                    ]
                                    if max_exec_time:
                                        props += ["-p", f"maxexecutiontime={int(max_exec_time)}"]
                                    cmd = ylib.build_cmd(
                                        classpath, workload_path, thread, props, ["-t"]
                                    )
                                    rf = os.path.join(
                                        result_path,
                                        results.run_writer_file(
                                            ks, opcnt, rc, wl, db_name + label_suffix, j,
                                            total_writers, thread, trial,
                                        ),
                                    )
                                    jobs.append((cmd, rf))
                                    per_writer_files.append(rf)

                                if syncing:
                                    # Restart the daemons' round-stats window.
                                    # Then the achieved-interval numbers cover
                                    # the writer window only, not the ready
                                    # gate or the idle rounds before it.
                                    for j in live:
                                        try:
                                            sync.get_round_stats(all_eps[j], reset=True)
                                        except Exception as e:
                                            print(f"[sync] WARNING: round_stats reset "
                                                  f"failed for w{j}: {e}")

                                _rcs, wall_ms = ylib.spawn_parallel(jobs)

                                extra.append(f"[CONSISTENCY], SyncEnabled, {syncing}")
                                extra.append(f"[CONSISTENCY], SyncIntervalMs, {interval_ms}")
                                extra.append(f"[CONSISTENCY], ReadBarrier, {read_barrier}")
                                if syncing:
                                    # This is the achieved anti-entropy
                                    # period against the nominal interval.
                                    # The loop sleeps only the remainder, so
                                    # at many peers the nominal value is
                                    # fiction. Scrape it BEFORE the post-run
                                    # convergence wait, so that idle rounds
                                    # do not dilute it.
                                    try:
                                        extra.extend(sync.round_stat_lines(all_eps, live))
                                    except Exception as e:
                                        print(f"[sync] WARNING: round_stats scrape failed: {e}")
                                if read_barrier:
                                    # Scrape while the daemons are still up.
                                    # This separates the sync cost from the
                                    # SQLite cost. YCSB's READ latency
                                    # includes both.
                                    try:
                                        stats = [
                                            (j, sync.get_barrier_stats(all_eps[j]))
                                            for j in live
                                        ]
                                        extra.append(
                                            "[SYNCREAD], BarrierCount, "
                                            f"{sum(s['count'] for _, s in stats)}"
                                        )
                                        for j, s in stats:
                                            extra.append(
                                                f"[SYNCREAD], Writer{j}BarrierP50(ms), "
                                                f"{s['p50_ms']}"
                                            )
                                            extra.append(
                                                f"[SYNCREAD], Writer{j}BarrierP99(ms), "
                                                f"{s['p99_ms']}"
                                            )
                                    except Exception as e:
                                        print(f"[syncread] WARNING: stats scrape failed: {e}")
                                if syncing:
                                    # Freeze the local daemons, so that the
                                    # snapshot is not a moving target. Count
                                    # the keys that differ across this host's
                                    # replicas. Then let anti-entropy finish
                                    # and time it.
                                    sync.pause_daemons(procs)
                                    div = replicas.max_divergence(run_db_paths)
                                    sync.resume_daemons(procs)
                                    extra.append(f"[CONSISTENCY], DivergentKeysAtRunEnd, {div}")
                                    extra.append(
                                        f"[CONSISTENCY], DivergentKeyFraction, {div / rc:.6f}"
                                    )
                                    conv_s = sync.wait_for_convergence(
                                        all_eps, expected_rows=None,
                                        poll_ms=poll_ms, timeout_s=timeout_s,
                                    )
                                    extra.append(
                                        f"[CONSISTENCY], ConvergenceTime(ms), {int(conv_s*1000)}"
                                    )
                                    print(
                                        f"[consistency] divergent_keys={div} "
                                        f"({div / rc:.4%} of {rc}); converged in {conv_s:.1f}s"
                                    )
                                    if multinode:
                                        time.sleep(POST_CONVERGENCE_GRACE_S)
                            except TimeoutError as e:
                                extra.append(f"[CONSISTENCY], ConvergenceTimedOut, {e}")
                                print(f"[consistency] WARNING: {e}")
                            finally:
                                sync.stop_daemons(procs)

                            agg = os.path.join(
                                result_path,
                                results.run_agg_file(
                                    ks, opcnt, rc, wl, db_name + label_suffix,
                                    num_writers, total_writers, offset, thread, trial,
                                ),
                            )
                            results.write_aggregate(
                                agg, per_writer_files, wall_ms, num_writers,
                                extra_lines=extra,
                            )

                            for j in live:
                                subprocess.run(
                                    ["rm", "-rf",
                                     replicas.run_dir(data_path, db_name, ks, wl, opcnt, j)]
                                )


# ====================================================================== sweep

def cmd_sweep(cfg, args):
    workloads = args.workloads.split()
    writers_list = [int(w) for w in args.writers_list.split()]
    if not workloads or not writers_list:
        raise ValueError("workloads and writers-list must each contain at least one value")
    read_barrier = args.read_barrier or sync.sync_settings(cfg)["read_barrier"]

    os.environ.setdefault(RUN_TAG_ENV, datetime.now().strftime("%Y%m%d-%H%M%S"))
    print(
        f"=== sweep: workloads=({' '.join(workloads)}) writers=({args.writers_list}) "
        f"trials={args.trials} read_barrier={read_barrier} "
        f"run_tag={os.environ[RUN_TAG_ENV]} ==="
    )

    if args.load:
        # A converged replica holds the full dataset, so one load at the
        # largest writer count serves every smaller run.
        max_w = max(writers_list)
        load_c = cfg["local"]["load"]
        data_path = load_c["ycsb_data_path"]
        rc = int(load_c["record_cnt"][0])
        ks = load_c["key_size"][0]
        missing = [
            j for j in range(max_w)
            if not os.path.exists(replicas.cached_dir(data_path, "crsqlite", ks, rc, j))
        ]
        if missing:
            print(f"[sweep] cached replicas missing for writers {missing}; loading w={max_w}")
            cmd_load(cfg, max_w, 0, max_w)
        else:
            print(f"[sweep] cached replicas present for writers 0..{max_w - 1}")

    outcomes = []
    for trial in range(1, args.trials + 1):
        for wl in workloads:
            for nw in writers_list:
                print(f"\n### iteration: trial={trial}/{args.trials} workload={wl} writers={nw} ###")
                try:
                    cmd_run(cfg, nw, [wl], 0, nw, trial, args.duration,
                            read_barrier=read_barrier)
                    rc_ = 0
                except Exception as e:
                    print(f"[sweep] iteration failed: {e}")
                    rc_ = 1
                outcomes.append((trial, wl, nw, rc_))

    print("\n=== sweep summary ===")
    for trial, wl, nw, rc_ in outcomes:
        print(f"  trial={trial} workload={wl} writers={nw} rc={rc_}")
    if any(rc_ != 0 for *_x, rc_ in outcomes):
        sys.exit(1)


# ================================================================== multinode

def _ssh_base(target, ssh_key):
    return [
        "ssh", "-i", ssh_key,
        "-oStrictHostKeyChecking=no",
        "-oUserKnownHostsFile=/dev/null",
        "-oLogLevel=ERROR",
        target,
    ]


def _remote_home(target, ssh_key):
    out = subprocess.run(
        _ssh_base(target, ssh_key) + ["bash -lc 'echo $CRSQLITE_HOME'"],
        capture_output=True, text=True, check=True,
    )
    lines = (out.stdout or "").strip().splitlines()
    home = lines[-1] if lines else ""
    if not home:
        raise RuntimeError(f"CRSQLITE_HOME not set on {target}")
    return home


def cmd_multinode(cfg, args):
    import re
    import shlex

    cloudlab = cfg.get("cloudlab") or {}
    hosts = (
        [h.strip() for h in args.hosts.split(",") if h.strip()]
        if args.hosts else list(cloudlab.get("hosts") or [])
    )
    if not hosts:
        raise ValueError("No hosts. Set nodes.clients in ycsb.yaml or pass --hosts.")
    ssh_user = args.ssh_user or cloudlab.get("ssh_user")
    ssh_key = os.path.expanduser(
        args.ssh_key or cloudlab.get("ssh_private_key_path") or "~/.ssh/id_rsa"
    )
    run_cfg = cfg["local"]["run"]
    wph = args.writers_per_host or run_cfg.get("writers_per_host", run_cfg.get("num_writers", 1))
    trial = args.trial if args.trial is not None else run_cfg.get("trial", 1)
    total = wph * len(hosts)
    run_tag = args.run_tag or datetime.now().strftime("%Y%m%d-%H%M%S")

    local_results = os.path.join(results_root(), run_tag)
    log_dir = os.path.join(local_results, "_orchestrator_logs")
    os.makedirs(log_dir, exist_ok=True)

    plan = [
        {"host": h, "target": f"{ssh_user}@{h}", "offset": i * wph}
        for i, h in enumerate(hosts)
    ]
    print(
        f"[orchestrator] phase={args.phase} tag={run_tag} hosts={len(hosts)} "
        f"writers_per_host={wph} total_writers={total} trial={trial}"
    )
    for p in plan:
        print(f"  - {p['target']}: writers [{p['offset']}, {p['offset'] + wph}) of {total}")
    if args.dry_run:
        return

    homes = {p["host"]: _remote_home(p["target"], ssh_key) for p in plan}
    rcs = {}

    def worker(p):
        home = homes[p["host"]]
        inner = [
            "python3", shlex.quote(os.path.join(home, "bench/scripts/bench.py")),
            args.phase,
            "--num_writers", str(wph),
            "--offset", str(p["offset"]),
            "--total_writers", str(total),
        ]
        if args.phase == "run":
            if args.workloads:
                inner += ["--workloads", shlex.quote(args.workloads)]
            inner += ["--trial", str(trial)]
            if args.read_barrier:
                inner += ["--read-barrier"]
        remote_cmd = " && ".join([
            f"export {RUN_TAG_ENV}={shlex.quote(run_tag)}",
            f"cd {shlex.quote(home)}",
            " ".join(inner),
        ])
        log_path = os.path.join(
            log_dir, re.sub(r"[^A-Za-z0-9._-]", "_", p["host"]) + ".log"
        )
        print(f"[{p['host']}] launching: {remote_cmd}")
        with open(log_path, "a") as f:
            f.write(f"# {datetime.now().isoformat()} bench.py multinode -> {p['target']}\n")
            f.flush()
            proc = subprocess.Popen(
                _ssh_base(p["target"], ssh_key) + [f"bash -lc {shlex.quote(remote_cmd)}"],
                stdout=f, stderr=subprocess.STDOUT,
            )
            rcs[p["host"]] = proc.wait()
        print(f"[{p['host']}] exit={rcs[p['host']]}")

    wall_start = time.time()
    threads = [threading.Thread(target=worker, args=(p,)) for p in plan]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall_ms = (time.time() - wall_start) * 1000.0
    print(f"[orchestrator] wall={wall_ms / 1000.0:.1f}s return_codes={rcs}")

    if args.phase == "load":
        print("[orchestrator] load results stay on each host under bench/results/local/")
        return
    if args.no_fetch:
        return

    for p in plan:
        remote_dir = os.path.join(homes[p["host"]], "bench/results/local", run_tag)
        cmd = [
            "scp", "-i", ssh_key,
            "-oStrictHostKeyChecking=no", "-oUserKnownHostsFile=/dev/null",
            "-r",
            # Use a glob, not `/.`. SFTP-mode scp on OpenSSH 9.x rejects `.`
            f"{p['target']}:{remote_dir}/*",
            local_results,
        ]
        print(f"[{p['host']}] {' '.join(cmd)}")
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            print(
                f"[{p['host']}] WARNING: scp exited {proc.returncode}: "
                f"{proc.stderr.strip()} -- aggregate may under-report"
            )

    if args.no_aggregate:
        return
    _cross_node_aggregate(local_results, total, wall_ms, trial)


def _cross_node_aggregate(results_dir, total_writers, wall_ms, trial_filter):
    groups = {}
    for fn in sorted(os.listdir(results_dir)):
        full = os.path.join(results_dir, fn)
        if not os.path.isfile(full):
            continue
        m = results.PERWRITER_RE.match(fn)
        if not m or int(m.group("total")) != total_writers:
            continue
        if trial_filter is not None and int(m.group("trial")) != trial_filter:
            continue
        key = tuple(m.group(g) for g in ("ks", "opcnt", "rc", "wl", "db", "thread", "trial"))
        groups.setdefault(key, []).append(full)

    if not groups:
        print("[aggregate] no per-writer files to aggregate")
        return
    for (ks, opcnt, rc, wl, db, thread, trial), files in sorted(groups.items()):
        if len(files) != total_writers:
            present = sorted(
                int(results.PERWRITER_RE.match(os.path.basename(p)).group("widx"))
                for p in files
            )
            missing = [i for i in range(total_writers) if i not in present]
            print(
                f"[aggregate] WARNING: workload={wl} trial={trial}: expected "
                f"{total_writers} files, got {len(files)}; missing writers {missing}. "
                f"Aggregate WILL UNDER-REPORT."
            )
        agg = os.path.join(
            results_dir,
            results.multinode_agg_file(ks, opcnt, rc, wl, db, total_writers, thread, trial),
        )
        results.write_aggregate(agg, sorted(files), wall_ms, total_writers)


# ==================================================== consistency experiments

PROBE_KEY = "__probe__"
COUNTER_KEY = "__counter__"


def _barrier(endpoint, timeout_s=30.0):
    """Fenced read: block until `endpoint`'s daemon completes a pull round
    that began after this call (see sync_daemon.py /barrier). Raise on any
    failure. A fenced reader must never quietly fall back to a stale local
    read."""
    with urlopen(f"http://{endpoint}/barrier", timeout=timeout_s) as resp:
        resp.read()


def _open_writable(db_path, cfg):
    """Return a python connection with the extension loaded (Ubuntu nodes)."""
    from sync_daemon import ensure_schema, open_replica

    c = cfg.get("crsqlite") or {}
    conn = open_replica(
        db_path,
        ycsb_config.crsqlite_extension_path(cfg),
        c.get("busy_timeout_ms", 30000),
        c.get("synchronous", "FULL"),
    )
    ensure_schema(conn)
    return conn


def _probe_topology(cfg, n, name, interval_ms):
    """Set up fresh replicas and the daemon port range. The range cannot collide
    with a concurrent YCSB benchmark. Return (dirs, db_paths, eps, cfg2)."""
    if interval_ms is not None:
        cfg = {**cfg, "sync": {**(cfg.get("sync") or {}), "interval_ms": interval_ms}}
    port_base = sync.sync_settings(cfg)["port_base"] + 500
    cfg = {**cfg, "sync": {**(cfg.get("sync") or {}), "port_base": port_base}}
    data_path = cfg["local"]["load"]["ycsb_data_path"]
    dirs = [os.path.join(data_path, f"{name}-w{i}") for i in range(n)]
    for d in dirs:
        replicas.fresh_dir(d)
    db_paths = [replicas.replica_db(d) for d in dirs]
    eps = sync.endpoints(cfg, n, n, multinode=False)
    return dirs, db_paths, eps, cfg


def _probe_topology_span(cfg, total, local_indices, writers_per_host, name,
                         interval_ms):
    """Cross-node variant of _probe_topology. The mesh spans hosts, but this
    process creates fresh replicas, and later daemons, only for
    `local_indices`. `eps` covers ALL `total` replicas through the LAN
    addresses. The sync.endpoints multinode mapping puts replica j on host
    j // writers_per_host. So every local daemon peers with the remote ones.
    The port offset is the same +500 as in _probe_topology, to stay clear of
    a concurrent YCSB run."""
    if interval_ms is not None:
        cfg = {**cfg, "sync": {**(cfg.get("sync") or {}), "interval_ms": interval_ms}}
    port_base = sync.sync_settings(cfg)["port_base"] + 500
    cfg = {**cfg, "sync": {**(cfg.get("sync") or {}), "port_base": port_base}}
    data_path = cfg["local"]["load"]["ycsb_data_path"]
    dirs = {i: os.path.join(data_path, f"{name}-w{i}") for i in local_indices}
    for d in dirs.values():
        replicas.fresh_dir(d)
    db_paths = {i: replicas.replica_db(dirs[i]) for i in local_indices}
    eps = sync.endpoints(cfg, total, writers_per_host, multinode=True)
    return dirs, db_paths, eps, cfg


def _write_visibility_summary(out, results, cfg, args, num_writers,
                              num_replicas, cross_node=False):
    """Write visibility.csv, summary.json and the verdict from the per-key
    records [idx, t_ack, t_notify, t_first, found_first, t_found, attempts].
    Cross-node runs reference the latency to the reader-side notify receipt
    (col 2). The CLOCK_MONOTONIC domains of the hosts are incomparable, and
    the miss count needs no clock comparison at all."""
    with open(os.path.join(out, "visibility.csv"), "w") as f:
        f.write("idx,t_ack_ns,t_notify_ns,t_first_ns,found_first,"
                "t_found_ns,attempts\n")
        for rec in results:
            f.write(",".join(str(x) for x in rec) + "\n")

    def pct(xs, q):
        if not xs:
            return 0.0
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(q / 100.0 * len(xs)))]

    ref = 2 if cross_node else 1
    checked = len(results)
    misses = sum(1 for rec in results if rec[4] == 0)
    timeouts = sum(1 for rec in results if rec[5] < 0)
    vis_ms = [(rec[5] - rec[ref]) / 1e6 for rec in results if rec[5] >= 0]
    first_age_ms = [(rec[3] - rec[ref]) / 1e6 for rec in results if rec[3] >= 0]
    pbs = [
        {
            "age_ms_le": a,
            "p_stale": ((sum(1 for v in vis_ms if v > a) + timeouts) / checked)
            if checked else 0.0,
        }
        for a in VIS_PBS_AGES_MS
    ]

    summary = {
        "engine": "cr-sqlite-syncread" if args.read_barrier else "cr-sqlite",
        "linearizable_reads": False,
        "read_barrier": bool(args.read_barrier),
        "cross_node": cross_node,
        "channel": "tcp" if cross_node else "queue",
        "inserts": checked,
        "checked": checked,
        "misses_first_read": misses,
        "miss_first_read_fraction": (misses / checked) if checked else 0.0,
        "timeouts": timeouts,
        "visibility_ms_p50": round(pct(vis_ms, 50), 3),
        "visibility_ms_p90": round(pct(vis_ms, 90), 3),
        "visibility_ms_p95": round(pct(vis_ms, 95), 3),
        "visibility_ms_p99": round(pct(vis_ms, 99), 3),
        "visibility_ms_max": round(max(vis_ms), 3) if vis_ms else 0.0,
        "first_check_age_ms_p50": round(pct(first_age_ms, 50), 3),
        "first_check_age_ms_p99": round(pct(first_age_ms, 99), 3),
        "pbs": pbs,
        "sync_interval_ms": sync.sync_settings(cfg)["interval_ms"],
        "rate_per_s": args.rate,
        "writers": num_writers,
        "aggregate_rate_per_s": num_writers * args.rate,
        "replicas": num_replicas,
        "duration_s": args.duration,
        "value_size": args.value_size,
        "poll_ms": args.poll_ms,
    }
    if not cross_node:
        notify_lag_ms = [(rec[2] - rec[1]) / 1e6 for rec in results]
        summary["notify_lag_ms_p50"] = round(pct(notify_lag_ms, 50), 4)
    with open(os.path.join(out, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    print(json.dumps(summary, indent=2))
    print(
        f"\n=> {misses} of {checked} first reads on replica {num_writers} "
        f"missed a key some writer replica had already acked "
        f"({summary['miss_first_read_fraction']:.1%}); "
        f"visibility p50 {summary['visibility_ms_p50']} ms "
        f"({num_writers} writers, sync interval "
        f"{summary['sync_interval_ms']} ms). Results in {out}"
    )
    return summary


def _consistency_outdir(name):
    d = os.path.join(
        crsqlite_home(), "bench", "results", "consistency",
        f"{name}-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
    )
    os.makedirs(d, exist_ok=True)
    return d


def cmd_probe_staleness(cfg, args):
    """A writer bumps a sequence number on replica 0 at a fixed rate. A
    reader polls replica 1. Under strong consistency every read returns the
    latest committed sequence. Under cr-sqlite's anti-entropy the reader
    trails by about one sync interval. The command reports the stale-read
    fraction, the version lag, and the staleness duration."""
    import bisect

    out = _consistency_outdir("staleness")
    dirs, db_paths, eps, cfg = _probe_topology(
        cfg, args.num_replicas, "probe-staleness", args.interval_ms
    )
    procs = []
    try:
        conns = [_open_writable(p, cfg) for p in db_paths]
        procs = _start_local_daemons(
            cfg, list(range(args.num_replicas)), db_paths, eps, out,
            ycsb_config.crsqlite_extension_path(cfg),
        )

        w = conns[0]
        w.execute("BEGIN IMMEDIATE")
        w.execute(
            "INSERT INTO usertable (ycsb_key, field_data) VALUES (?, ?) "
            "ON CONFLICT(ycsb_key) DO UPDATE SET field_data = excluded.field_data",
            (PROBE_KEY, struct.pack(">q", 0)),
        )
        w.execute("COMMIT")

        commits = [(0, time.monotonic_ns())]  # (seq, commit time)
        reads = []  # (t_read_ns, seq_seen)
        stop = threading.Event()

        def reader():
            r = conns[1]
            while not stop.is_set():
                row = r.execute(
                    "SELECT field_data FROM usertable WHERE ycsb_key = ?", (PROBE_KEY,)
                ).fetchone()
                t = time.monotonic_ns()
                if row and row[0]:
                    reads.append((t, struct.unpack(">q", bytes(row[0]))[0]))
                time.sleep(args.read_interval_ms / 1000.0)

        rt = threading.Thread(target=reader)
        rt.start()
        period = 1.0 / args.write_rate
        t_end = time.time() + args.duration
        seq = 0
        while time.time() < t_end:
            seq += 1
            w.execute("BEGIN IMMEDIATE")
            w.execute(
                "UPDATE usertable SET field_data = ? WHERE ycsb_key = ?",
                (struct.pack(">q", seq), PROBE_KEY),
            )
            w.execute("COMMIT")
            commits.append((seq, time.monotonic_ns()))
            time.sleep(period)
        # Let the reader observe the final value before it stops.
        time.sleep(max(2.0, 20 * sync.sync_settings(cfg)["interval_ms"] / 1000.0))
        stop.set()
        rt.join()

        commit_times = [t for _, t in commits]
        stale = 0
        version_lags = []
        staleness_ms = []
        for t_read, seen in reads:
            latest = bisect.bisect_right(commit_times, t_read) - 1
            lag = max(0, latest - seen)
            version_lags.append(lag)
            if lag > 0:
                stale += 1
                # The read missed every write since seen+1. How long ago did
                # the earliest missed write commit?
                staleness_ms.append((t_read - commit_times[seen + 1]) / 1e6)

        with open(os.path.join(out, "reads.csv"), "w") as f:
            f.write("t_read_ns,seq_seen\n")
            for t, s in reads:
                f.write(f"{t},{s}\n")
        with open(os.path.join(out, "commits.csv"), "w") as f:
            f.write("seq,t_commit_ns\n")
            for s, t in commits:
                f.write(f"{s},{t}\n")

        def pct(xs, p):
            if not xs:
                return 0.0
            xs = sorted(xs)
            return xs[min(len(xs) - 1, int(p / 100.0 * len(xs)))]

        summary = {
            "writes": seq,
            "reads": len(reads),
            "stale_reads": stale,
            "stale_read_fraction": (stale / len(reads)) if reads else 0.0,
            "version_lag_p50": pct(version_lags, 50),
            "version_lag_p95": pct(version_lags, 95),
            "version_lag_max": max(version_lags) if version_lags else 0,
            "staleness_ms_p50": round(pct(staleness_ms, 50), 2),
            "staleness_ms_p95": round(pct(staleness_ms, 95), 2),
            "staleness_ms_max": round(max(staleness_ms), 2) if staleness_ms else 0.0,
            "sync_interval_ms": sync.sync_settings(cfg)["interval_ms"],
            "write_rate_per_s": args.write_rate,
            "duration_s": args.duration,
        }
        with open(os.path.join(out, "summary.json"), "w") as f:
            json.dump(summary, f, indent=2)
        print(json.dumps(summary, indent=2))
        print(
            f"\n=> {stale} of {len(reads)} reads on replica 1 returned a stale value "
            f"({summary['stale_read_fraction']:.1%}); a strongly consistent system "
            f"returns 0. Results in {out}"
        )
        for c in conns:
            c.execute("SELECT crsql_finalize()")
            c.close()
    finally:
        sync.stop_daemons(procs)


# YCSB CoreWorkload.buildKeyName(i, zeropadding=1, orderedinserts=false) is
# "user" + FNVhash64(i). This matches the ozonedb probe's keyspace exactly.
def _fnvhash64(val):
    mask = (1 << 64) - 1
    h = 0xCBF29CE484222325
    for _ in range(8):
        h ^= val & 0xFF
        val >>= 8
        h = (h * 1099511628211) & mask
    signed = h - (1 << 64) if h >= (1 << 63) else h
    if signed < 0 and signed != -(1 << 63):
        signed = -signed
    return signed


def _ycsb_key(i):
    return f"user{_fnvhash64(i)}"


# PBS-curve age buckets (ms): P[stale at age a] = P[visibility latency > a].
# Keep them identical to ozonedb's consistency.py, so that the curves are
# comparable.
VIS_PBS_AGES_MS = [0.5, 1, 2, 5, 10, 20, 50, 100, 200, 500]


def cmd_check_visibility(cfg, args):
    """Two benchmarks from one run. This mirrors ozonedb's check-visibility
    command. A writer INSERTs brand-new
    YCSB-keyspace rows on replica 0 and acks each one into a queue. A reader
    polls replica 1 for every acked key until it appears. The first read
    per key starts after the ack (ack, then notify, then read). So a
    first-read miss is a read-latest violation with no clock comparison.
    New keys make "not found" unambiguous staleness. The command reports
    the miss fraction and the visibility-latency distribution. That
    distribution is about one sync interval here, about the apply lag on
    ozonedb, and about 0 under ozonedb's linearizable_reads."""
    out = _consistency_outdir("visibility")
    # Writers live on replicas 0..W-1, and the reader on replica W. So the
    # reader's daemon must merge the changes of W peers. That is exactly the
    # scaling axis that the multi-writer experiment measures. --num-replicas
    # still sets a floor for odd topologies. The writer count wins when it
    # needs more.
    num_writers = max(1, getattr(args, "num_writers", 1))
    num_replicas = max(args.num_replicas, num_writers + 1)
    dirs, db_paths, eps, cfg = _probe_topology(
        cfg, num_replicas, "check-visibility", args.interval_ms
    )
    procs = []
    try:
        conns = [_open_writable(p, cfg) for p in db_paths]
        procs = _start_local_daemons(
            cfg, list(range(num_replicas)), db_paths, eps, out,
            ycsb_config.crsqlite_extension_path(cfg),
        )

        notify_q = queue.Queue()
        done = threading.Event()
        inserts = []          # (idx, t_ack_ns)
        results = []          # rows in visibility.csv column order
        key_timeout_ns = int(args.key_timeout_s * 1e9)

        def writer(wi):
            # Each writer has a disjoint idx range, the same convention as
            # ozonedb's insert-probe --key-base. idx alone identifies the
            # key, so the reader's pending map needs no writer id. The main
            # thread sets done once ALL writers have joined.
            w = conns[wi]
            base = wi * 10_000_000
            period = 1.0 / args.rate
            t_end = time.time() + args.duration
            i = 0
            while time.time() < t_end:
                idx = base + i
                w.execute("BEGIN IMMEDIATE")
                w.execute(
                    "INSERT INTO usertable (ycsb_key, field_data) VALUES (?, ?)",
                    (_ycsb_key(idx), os.urandom(max(8, args.value_size))),
                )
                w.execute("COMMIT")
                t_ack = time.monotonic_ns()
                inserts.append((idx, t_ack))
                notify_q.put((idx, t_ack))
                i += 1
                time.sleep(period)

        def reader():
            r = conns[num_writers]
            # idx maps to [idx, t_ack, t_notify, t_first, found_first,
            # t_found, attempts]. The dict preserves the insertion order.
            pending = {}
            while True:
                while True:
                    try:
                        idx, t_ack = notify_q.get_nowait()
                    except queue.Empty:
                        break
                    pending[idx] = [idx, t_ack, time.monotonic_ns(), -1, 0, -1, 0]
                if args.read_barrier and pending:
                    # Fence before the reads below. Then every key's first
                    # read happens after a pull round that started after its
                    # ack and notify. So a strong system misses zero.
                    _barrier(eps[num_writers])
                for idx in list(pending):
                    rec = pending[idx]
                    t_start = time.monotonic_ns()
                    row = r.execute(
                        "SELECT 1 FROM usertable WHERE ycsb_key = ?",
                        (_ycsb_key(idx),),
                    ).fetchone()
                    rec[6] += 1
                    if rec[3] < 0:
                        rec[3] = t_start
                        rec[4] = 1 if row else 0
                    if row:
                        rec[5] = time.monotonic_ns()
                        results.append(rec)
                        del pending[idx]
                    elif t_start - rec[2] > key_timeout_ns:
                        results.append(rec)
                        del pending[idx]
                if done.is_set() and notify_q.empty() and not pending:
                    break
                time.sleep(args.poll_ms / 1000.0)

        wts = [threading.Thread(target=writer, args=(wi,))
               for wi in range(num_writers)]
        rt = threading.Thread(target=reader)
        for wt in wts:
            wt.start()
        rt.start()
        for wt in wts:
            wt.join()
        # "done" means every writer exited. Only the main thread can see that.
        done.set()
        rt.join()

        with open(os.path.join(out, "inserts.csv"), "w") as f:
            f.write("idx,t_ack_ns\n")
            for i, t in inserts:
                f.write(f"{i},{t}\n")
        _write_visibility_summary(out, results, cfg, args, num_writers,
                                  num_replicas, cross_node=False)
        for c in conns:
            c.execute("SELECT crsql_finalize()")
            c.close()
    finally:
        sync.stop_daemons(procs)


def cmd_visibility_writer(cfg, args):
    """The writer half of the cross-node visibility experiment. W writer
    replicas and daemons run on THIS host (clients[0]). The writer threads
    insert disjoint YCSB-keyspace slices. They ack each insert over one TCP
    socket to the reader host. A successful connect proves that the reader
    and its daemon are up. The "go" line that the reader sends is the start
    barrier. After the insert phase the writer sends "done". Then it BLOCKS
    until the reader closes the socket, and only then stops its daemons.
    The reader is still pulling late keys through the mesh. An early
    teardown of the writer daemons would strand them."""
    out = os.path.join(crsqlite_home(), args.out_dir)
    os.makedirs(out, exist_ok=True)
    w_count = args.num_writers
    dirs, db_paths, eps, cfg = _probe_topology_span(
        cfg, w_count + 1, list(range(w_count)), w_count,
        f"visx-writer-{os.path.basename(out)}", args.interval_ms)
    procs = []
    sock = None
    try:
        conns = [_open_writable(db_paths[w], cfg) for w in range(w_count)]
        procs = _start_local_daemons(
            cfg, list(range(w_count)), [db_paths[w] for w in range(w_count)],
            eps, out, ycsb_config.crsqlite_extension_path(cfg))
        host, _, port = args.connect.rpartition(":")
        deadline = time.time() + 240
        while True:
            try:
                sock = socket.create_connection((host, int(port)), timeout=2)
                break
            except OSError:
                if time.time() > deadline:
                    raise
                time.sleep(0.2)
        sock.settimeout(None)
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        rf = sock.makefile("r")
        rf.readline()  # the reader's "go", the cross-node start barrier
        send_lock = threading.Lock()
        inserts = []

        def writer(wi):
            conn = conns[wi]
            base = wi * 10_000_000
            period = 1.0 / args.rate
            t_end = time.time() + args.duration
            i = 0
            while time.time() < t_end:
                idx = base + i
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "INSERT INTO usertable (ycsb_key, field_data) VALUES (?, ?)",
                    (_ycsb_key(idx), os.urandom(max(8, args.value_size))),
                )
                conn.execute("COMMIT")
                t_ack = time.monotonic_ns()
                inserts.append((idx, t_ack))
                with send_lock:
                    sock.sendall(f"{idx},{t_ack}\n".encode())
                i += 1
                time.sleep(period)

        wts = [threading.Thread(target=writer, args=(wi,))
               for wi in range(w_count)]
        for t in wts:
            t.start()
        for t in wts:
            t.join()
        sock.sendall(b"done\n")
        # The reader closes the socket once it is fully drained. Wait for that
        # EOF, with a bound, so that the daemons outlive every pending key.
        sock.settimeout(args.duration + 300)
        try:
            rf.readline()
        except socket.timeout:
            print("[visx-writer] reader never closed the socket; "
                  "stopping daemons anyway")
        with open(os.path.join(out, "inserts.csv"), "w") as f:
            f.write("idx,t_ack_ns\n")
            for i, t in inserts:
                f.write(f"{i},{t}\n")
        print(json.dumps({"inserts": len(inserts), "writers": w_count}))
        for c in conns:
            c.execute("SELECT crsql_finalize()")
            c.close()
    finally:
        if sock is not None:
            sock.close()
        sync.stop_daemons(procs)


def cmd_visibility_reader(cfg, args):
    """The reader half of the cross-node visibility experiment. ONE replica
    and daemon run on THIS host (clients[1]). The reader polls every key
    that the writer host acks over TCP. t_notify is stamped at receipt on
    this host's clock, and it is the latency reference. The miss count is
    clock-free. --read-barrier fences each tick through the LOCAL daemon's
    /barrier. That is a pull round from all W writer daemons across the LAN."""
    out = os.path.join(crsqlite_home(), args.out_dir)
    os.makedirs(out, exist_ok=True)
    w_count = args.num_writers
    dirs, db_paths, eps, cfg = _probe_topology_span(
        cfg, w_count + 1, [w_count], w_count,
        f"visx-reader-{os.path.basename(out)}", args.interval_ms)
    procs = []
    srv = sock = None
    try:
        conn = _open_writable(db_paths[w_count], cfg)
        procs = _start_local_daemons(
            cfg, [w_count], [db_paths[w_count]], eps, out,
            ycsb_config.crsqlite_extension_path(cfg))
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        srv.bind(("", args.port))
        srv.listen(1)
        srv.settimeout(300)
        sock, _ = srv.accept()
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.sendall(b"go\n")

        notify_q = queue.Queue()
        eof = threading.Event()

        def drain():
            f = sock.makefile("r")
            for line in f:
                line = line.strip()
                if line == "done":
                    break
                idx, t_ack = line.split(",", 1)
                notify_q.put((int(idx), int(t_ack), time.monotonic_ns()))
            eof.set()

        threading.Thread(target=drain, daemon=True).start()
        key_timeout_ns = int(args.key_timeout_s * 1e9)
        run_deadline = time.monotonic_ns() + int(args.run_timeout_s * 1e9)
        pending = {}
        results = []
        while True:
            while True:
                try:
                    idx, t_ack, t_notify = notify_q.get_nowait()
                except queue.Empty:
                    break
                pending[idx] = [idx, t_ack, t_notify, -1, 0, -1, 0]
            if args.read_barrier and pending:
                _barrier(eps[w_count])
            for idx in list(pending):
                rec = pending[idx]
                t_start = time.monotonic_ns()
                row = conn.execute(
                    "SELECT 1 FROM usertable WHERE ycsb_key = ?",
                    (_ycsb_key(idx),),
                ).fetchone()
                rec[6] += 1
                if rec[3] < 0:
                    rec[3] = t_start
                    rec[4] = 1 if row else 0
                if row:
                    rec[5] = time.monotonic_ns()
                    results.append(rec)
                    del pending[idx]
                elif t_start - rec[2] > key_timeout_ns:
                    results.append(rec)
                    del pending[idx]
            if eof.is_set() and notify_q.empty() and not pending:
                break
            if time.monotonic_ns() > run_deadline:
                print(f"[visx-reader] run timeout with {len(pending)} pending")
                results.extend(pending.values())
                break
            time.sleep(args.poll_ms / 1000.0)
        _write_visibility_summary(out, results, cfg, args, w_count,
                                  w_count + 1, cross_node=True)
        conn.execute("SELECT crsql_finalize()")
        conn.close()
    finally:
        # The close of our end is the writer's release. It holds its daemons
        # up until this EOF.
        if sock is not None:
            sock.close()
        if srv is not None:
            srv.close()
        sync.stop_daemons(procs)


def cmd_check_lost_updates(cfg, args):
    """K read-modify-write increments of one counter, split across N replicas
    that run concurrently. cr-sqlite merges the concurrent updates with
    last-write-wins. So increments are silently lost, and the final value
    lands well below K. The --control run does the same increments against
    ONE shared SQLite file. There BEGIN IMMEDIATE serializes them, and the
    final value is exactly K."""
    out = _consistency_outdir("lost-updates")
    n = 1 if args.control else args.num_replicas
    dirs, db_paths, eps, cfg = _probe_topology(cfg, n, "lost-updates", args.interval_ms)
    procs = []
    poll_ms, timeout_s = _convergence_knobs(cfg)
    try:
        seed = _open_writable(db_paths[0], cfg)
        seed.execute("BEGIN IMMEDIATE")
        seed.execute(
            "INSERT INTO usertable (ycsb_key, field_data) VALUES (?, ?) "
            "ON CONFLICT(ycsb_key) DO UPDATE SET field_data = excluded.field_data",
            (COUNTER_KEY, b"0"),
        )
        seed.execute("COMMIT")
        seed.execute("SELECT crsql_finalize()")
        seed.close()

        if not args.control and n > 1:
            procs = _start_local_daemons(
                cfg, list(range(n)), db_paths, eps, out,
                ycsb_config.crsqlite_extension_path(cfg),
            )
            # The counter row must exist everywhere before workers RMW it.
            sync.wait_for_convergence(eps, expected_rows=1,
                                      poll_ms=poll_ms, timeout_s=timeout_s)

        workers = args.num_replicas
        per_worker = ylib.partition_records(args.increments, workers)

        def worker(i, count):
            # In control mode, every worker uses the one shared file. Otherwise
            # worker i uses replica i.
            path = db_paths[0] if args.control else db_paths[i]
            conn = _open_writable(path, cfg)
            fenced = args.read_barrier and not args.control and n > 1
            for _ in range(count):
                if fenced:
                    # Fence the RMW's read. This shrinks the conflict window
                    # from the sync-interval scale to the read-to-write gap,
                    # where ozonedb linearizable_reads lands. It cannot close
                    # the window.
                    _barrier(eps[i])
                conn.execute("BEGIN IMMEDIATE")
                cur = conn.execute(
                    "SELECT field_data FROM usertable WHERE ycsb_key = ?", (COUNTER_KEY,)
                ).fetchone()[0]
                nxt = str(int(bytes(cur)) + 1).encode()
                conn.execute(
                    "UPDATE usertable SET field_data = ? WHERE ycsb_key = ?",
                    (nxt, COUNTER_KEY),
                )
                conn.execute("COMMIT")
            conn.execute("SELECT crsql_finalize()")
            conn.close()

        t0 = time.time()
        threads = [
            threading.Thread(target=worker, args=(i, cnt))
            for i, (_, cnt) in enumerate(per_worker)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.time() - t0

        conv_s = 0.0
        if not args.control and n > 1:
            conv_s = sync.wait_for_convergence(eps, expected_rows=1,
                                               poll_ms=poll_ms, timeout_s=timeout_s)

        finals = []
        for p in db_paths:
            c = _open_writable(p, cfg)
            finals.append(int(bytes(c.execute(
                "SELECT field_data FROM usertable WHERE ycsb_key = ?", (COUNTER_KEY,)
            ).fetchone()[0])))
            c.execute("SELECT crsql_finalize()")
            c.close()

        k = args.increments
        f_val = finals[0]
        fenced_run = bool(args.read_barrier and not args.control)
        summary = {
            "mode": "control(single shared sqlite)" if args.control
            else ("crsqlite replicas + fenced reads" if fenced_run
                  else "crsqlite replicas"),
            "read_barrier": fenced_run,
            "replicas": n,
            "workers": args.num_replicas,
            "increments_issued": k,
            "final_counter": f_val,
            "final_per_replica": finals,
            "lost_updates": k - f_val,
            "lost_fraction": (k - f_val) / k,
            "elapsed_s": round(elapsed, 2),
            "convergence_s": round(conv_s, 2),
            "sync_interval_ms": sync.sync_settings(cfg)["interval_ms"],
        }
        with open(os.path.join(out, "summary.json"), "w") as f:
            json.dump(summary, f, indent=2)
        print(json.dumps(summary, indent=2))
        verdict = (
            "no updates lost (strong serialization)"
            if k == f_val
            else f"{k - f_val} of {k} increments SILENTLY LOST ({(k - f_val) / k:.1%})"
        )
        print(f"\n=> final counter {f_val} after {k} increments: {verdict}. Results in {out}")
    finally:
        sync.stop_daemons(procs)


def cmd_check_convergence(cfg, args):
    if args.dbs:
        base = args.dbs[0]
        print(f"rows({base}) = {replicas.count_rows(base)}")
        for other in args.dbs[1:]:
            d = replicas.divergent_keys(base, other)
            print(f"{other}: rows={replicas.count_rows(other)} divergent_keys_vs_first={d}")
    elif args.endpoints:
        eps = [e.strip() for e in args.endpoints.split(",") if e.strip()]
        for e in eps:
            st = sync.get_status(e)
            h = sync.get_hash(e)
            print(f"{e}: rows={st['rows']} db_version={st['db_version']} hash={h['hash']}")
    else:
        raise ValueError("pass --dbs or --endpoints")


# ======================================================================= main

def main():
    p = argparse.ArgumentParser(
        description=__doc__.splitlines()[1],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--config", default=None, help="ycsb.yaml (default: config/ycsb.yaml)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("load", help="load phase: N writers insert disjoint slices, then converge")
    sp.add_argument("--num_writers", type=int, default=None)
    sp.add_argument("--offset", type=int, default=0)
    sp.add_argument("--total_writers", type=int, default=None)

    sp = sub.add_parser("run", help="run phase: op-partitioned YCSB + consistency measurement")
    sp.add_argument("--num_writers", type=int, default=None)
    sp.add_argument("--workloads", type=str, default=None, help="comma-separated letters")
    sp.add_argument("--offset", type=int, default=0)
    sp.add_argument("--total_writers", type=int, default=None)
    sp.add_argument("--trial", type=int, default=None)
    sp.add_argument("--max_exec_time", type=int, default=None)
    sp.add_argument("--read-barrier", action="store_true",
                    help="fenced reads: every read barriers through its writer's sync "
                         "daemon first (results labeled crsqlite-syncread)")

    sp = sub.add_parser("sweep", help="trials x workloads x writer-counts")
    sp.add_argument("--workloads", default="a b c d f")
    sp.add_argument("--writers-list", default="1 2 4 8")
    sp.add_argument("--trials", type=int, default=1)
    sp.add_argument("--duration", type=int, default=None,
                    help="per-iteration cap in seconds (default local.run.max_exec_time)")
    sp.add_argument("--load", action="store_true",
                    help="run the load phase first if cached replicas are missing")
    sp.add_argument("--read-barrier", action="store_true",
                    help="fenced reads for every iteration (results labeled crsqlite-syncread)")

    sp = sub.add_parser("multinode", help="fan out over cloudlab.hosts via ssh")
    sp.add_argument("--phase", choices=["load", "run"], default="run")
    sp.add_argument("--hosts")
    sp.add_argument("--ssh_user")
    sp.add_argument("--ssh_key")
    sp.add_argument("--writers_per_host", type=int, default=None)
    sp.add_argument("--workloads")
    sp.add_argument("--trial", type=int, default=None)
    sp.add_argument("--read-barrier", action="store_true",
                    help="pass --read-barrier to every host's run phase")
    sp.add_argument("--run_tag")
    sp.add_argument("--no_fetch", action="store_true")
    sp.add_argument("--no_aggregate", action="store_true")
    sp.add_argument("--dry_run", action="store_true")

    sp = sub.add_parser("probe-staleness", help="stale reads across two replicas")
    sp.add_argument("--duration", type=int, default=60)
    sp.add_argument("--write-rate", type=float, default=100.0)
    sp.add_argument("--read-interval-ms", type=float, default=1.0)
    sp.add_argument("--num-replicas", type=int, default=2)
    sp.add_argument("--interval-ms", type=float, default=None,
                    help="override sync.interval_ms for this probe")

    sp = sub.add_parser("check-lost-updates", help="concurrent RMW increments across replicas")
    sp.add_argument("--increments", type=int, default=10000)
    sp.add_argument("--num-replicas", type=int, default=2)
    sp.add_argument("--interval-ms", type=float, default=None)
    sp.add_argument("--control", action="store_true",
                    help="same increments against ONE shared sqlite file (expect zero lost)")
    sp.add_argument("--read-barrier", action="store_true",
                    help="fence each RMW's read through its replica's /barrier first")

    sp = sub.add_parser("check-visibility",
                        help="insert visibility: retry-until-found latency + first-read miss rate")
    sp.add_argument("--rate", type=float, default=20.0,
                    help="inserts/s PER WRITER (aggregate = writers x rate)")
    sp.add_argument("--num-writers", type=int, default=1,
                    help="writer replicas 0..W-1, reader on replica W; "
                         "replica count grows to W+1 (scaling experiments)")
    sp.add_argument("--duration", type=int, default=15, help="insert phase seconds")
    sp.add_argument("--value-size", type=int, default=1000, help="value bytes")
    sp.add_argument("--poll-ms", type=float, default=1.0,
                    help="reader poll interval (bounds latency resolution)")
    sp.add_argument("--key-timeout-s", type=float, default=30.0,
                    help="give up on a key after this long (>> sync interval)")
    sp.add_argument("--num-replicas", type=int, default=2)
    sp.add_argument("--interval-ms", type=float, default=None,
                    help="override sync.interval_ms for this probe")
    sp.add_argument("--read-barrier", action="store_true",
                    help="fence every read through the reader replica's /barrier; a correct "
                         "barrier yields ZERO first-read misses (the validation gate)")

    sp = sub.add_parser("visibility-writer",
                        help="cross-node writer role: W replicas + daemons on "
                             "THIS host, TCP acks to the reader host "
                             "(driven by run_visibility_cross_node.sh)")
    sp.add_argument("--out-dir", required=True,
                    help="run dir relative to CRSQLITE_HOME")
    sp.add_argument("--connect", required=True, help="reader LAN host:port")
    sp.add_argument("--num-writers", type=int, default=1)
    sp.add_argument("--rate", type=float, default=20.0, help="inserts/s per writer")
    sp.add_argument("--duration", type=int, default=15)
    sp.add_argument("--value-size", type=int, default=1000)
    sp.add_argument("--interval-ms", type=float, default=None)

    sp = sub.add_parser("visibility-reader",
                        help="cross-node reader role: one replica + daemon on "
                             "THIS host, polls every TCP-acked key "
                             "(driven by run_visibility_cross_node.sh)")
    sp.add_argument("--out-dir", required=True,
                    help="run dir relative to CRSQLITE_HOME")
    sp.add_argument("--port", type=int, default=7912)
    sp.add_argument("--num-writers", type=int, default=1)
    sp.add_argument("--rate", type=float, default=20.0,
                    help="per-writer rate (summary provenance only)")
    sp.add_argument("--duration", type=int, default=15)
    sp.add_argument("--value-size", type=int, default=1000)
    sp.add_argument("--poll-ms", type=float, default=1.0)
    sp.add_argument("--key-timeout-s", type=float, default=30.0)
    sp.add_argument("--run-timeout-s", type=float, default=600.0)
    sp.add_argument("--interval-ms", type=float, default=None)
    sp.add_argument("--read-barrier", action="store_true",
                    help="fence each tick through the local daemon's /barrier")

    sp = sub.add_parser("check-convergence", help="compare replicas by file or endpoint")
    sp.add_argument("--dbs", nargs="*", default=None)
    sp.add_argument("--endpoints", default=None)

    args = p.parse_args()
    cfg = load_cfg(args.config)

    if args.cmd == "load":
        lc = cfg["local"]["load"]
        nw = args.num_writers if args.num_writers is not None else lc.get("num_writers", 2)
        cmd_load(cfg, nw, args.offset, args.total_writers)
    elif args.cmd == "run":
        rc_ = cfg["local"]["run"]
        nw = args.num_writers if args.num_writers is not None else rc_.get("num_writers", 2)
        workloads = (
            [w.strip() for w in args.workloads.split(",") if w.strip()]
            if args.workloads else None
        )
        trial = args.trial if args.trial is not None else rc_.get("trial", 1)
        met = args.max_exec_time if args.max_exec_time is not None else rc_.get("max_exec_time")
        rb = args.read_barrier or sync.sync_settings(cfg)["read_barrier"]
        cmd_run(cfg, nw, workloads, args.offset, args.total_writers, trial, met,
                read_barrier=rb)
    elif args.cmd == "sweep":
        if args.duration is None:
            args.duration = cfg["local"]["run"].get("max_exec_time")
        cmd_sweep(cfg, args)
    elif args.cmd == "multinode":
        cmd_multinode(cfg, args)
    elif args.cmd == "probe-staleness":
        cmd_probe_staleness(cfg, args)
    elif args.cmd == "check-lost-updates":
        cmd_check_lost_updates(cfg, args)
    elif args.cmd == "visibility-writer":
        cmd_visibility_writer(cfg, args)
    elif args.cmd == "visibility-reader":
        cmd_visibility_reader(cfg, args)
    elif args.cmd == "check-visibility":
        cmd_check_visibility(cfg, args)
    elif args.cmd == "check-convergence":
        cmd_check_convergence(cfg, args)


if __name__ == "__main__":
    main()
