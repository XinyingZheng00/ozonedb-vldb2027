#!/usr/bin/env python3
"""Shared trial-running/analysis code for the use-case benchmarks.

A "trial" = one (backend, concurrency) combination. It spawns:
  - optionally, one long-running daemon process (kv_writer --role daemon)
  - a pool of short-lived "cli" processes (kv_writer --role cli), each
    launched on its own. The pool is kept at a target steady-state
    concurrency for the trial's wall-clock duration. Any process that exits
    is replaced at once.

Each spawned process is a separate OS process (subprocess.Popen), confirmed
by PID. It is not a thread within this orchestrator.

Every op emits one JSON line to its own file. The file is per process, to
avoid any risk of interleaved partial writes across processes that share one
file. After the trial, analyze_trial() aggregates throughput/latency/errors.
It also builds a verification manifest. kv_writer --role verify checks the
manifest against the store's actual final state.
"""
import glob
import json
import os
import random
import signal
import subprocess
import time
from pathlib import Path


def spawn_cli(binary, backend, config, op, key, value_bytes, out_path, busy_timeout_ms=1000):
    cmd = [binary, "--backend", backend, "--config", config, "--role", "cli",
           "--op", op, "--key", key, "--value-bytes", str(value_bytes), "--out", out_path]
    if backend == "sqlite":
        cmd += ["--sqlite-busy-timeout-ms", str(busy_timeout_ms)]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def spawn_daemon(binary, backend, config, duration_s, rate_per_s, out_path,
                  key_prefix="daemon", value_bytes=128, busy_timeout_ms=1000):
    cmd = [binary, "--backend", backend, "--config", config, "--role", "daemon",
           "--duration-s", str(duration_s), "--rate-per-s", str(rate_per_s),
           "--key-prefix", key_prefix, "--value-bytes", str(value_bytes), "--out", out_path]
    if backend == "sqlite":
        cmd += ["--sqlite-busy-timeout-ms", str(busy_timeout_ms)]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_trial(binary, backend, config, n_processes, duration_s, trial_dir,
              value_bytes=128, putdel_frac=0.2, daemon_rate=20,
              busy_timeout_ms=1000, with_daemon=True, poll_s=0.002,
              key_namespace="cli", mode="percli"):
    """Run one trial. Returns a dict with the pids seen and the wall time.

    mode="percli"    -- (the literal Rhizome-bug shape) each writer besides
                         the daemon is a short-lived process that is launched
                         on its own: open, ONE put/putdel, close, exit. When
                         a process finishes, a fresh process is spawned at
                         once to replace it. This holds steady-state
                         concurrency.
    mode="persistent" -- each non-daemon writer instead holds ONE open
                         handle for the whole trial and loops ops on it (like
                         a second daemon). This mode is included because
                         OzoneDB's DB::closeDB() blocks on
                         stopViewUpdate()->join(). That join waits for the
                         100ms-period poll loop of the view-update thread
                         (metadata_log_handler.cpp,
                         rollForwardMetadataLogPeriodically). That is, every
                         open/close cycle pays a real ~50-100ms fixed cost.
                         "percli" faithfully reproduces the real bug's
                         process shape and is the primary result. "persistent"
                         isolates whether that fixed teardown cost, and not
                         genuine lock contention, is what dominates.
    """
    trial_dir = Path(trial_dir)
    trial_dir.mkdir(parents=True, exist_ok=True)

    daemon_proc = None
    n_cli_concurrent = n_processes
    if with_daemon:
        daemon_out = str(trial_dir / "daemon.jsonl")
        daemon_proc = spawn_daemon(binary, backend, config, duration_s + 3, daemon_rate,
                                   daemon_out, busy_timeout_ms=busy_timeout_ms)
        n_cli_concurrent = max(0, n_processes - 1)

    if mode == "persistent":
        procs = []
        pids_seen = set()
        if daemon_proc:
            pids_seen.add(daemon_proc.pid)
        for i in range(n_cli_concurrent):
            out_path = str(trial_dir / f"persist_{i}.jsonl")
            p = spawn_daemon(binary, backend, config, duration_s, 0.0, out_path,
                             key_prefix=f"{key_namespace}-slot{i}", value_bytes=value_bytes,
                             busy_timeout_ms=busy_timeout_ms)
            pids_seen.add(p.pid)
            procs.append(p)
        start = time.time()
        time.sleep(duration_s)
        for p in procs:
            p.send_signal(signal.SIGTERM)
        for p in procs:
            try:
                p.wait(timeout=15)
            except subprocess.TimeoutExpired:
                p.kill()
        if daemon_proc:
            daemon_proc.send_signal(signal.SIGTERM)
            try:
                daemon_proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                daemon_proc.kill()
        wall_s = time.time() - start
        return {"n_cli_spawned": n_cli_concurrent, "wall_s": wall_s, "n_distinct_pids": len(pids_seen)}

    start = time.time()
    seq = 0
    active = []
    pids_seen = set()
    if daemon_proc:
        pids_seen.add(daemon_proc.pid)

    while time.time() - start < duration_s:
        still = []
        for p, outp in active:
            if p.poll() is None:
                still.append((p, outp))
        active = still
        while len(active) < n_cli_concurrent:
            key = f"{key_namespace}-{seq}"
            op = "putdel" if random.random() < putdel_frac else "put"
            outp = str(trial_dir / f"cli_{seq}.jsonl")
            p = spawn_cli(binary, backend, config, op, key, value_bytes, outp,
                          busy_timeout_ms=busy_timeout_ms)
            pids_seen.add(p.pid)
            active.append((p, outp))
            seq += 1
        time.sleep(poll_s)

    # Drain in-flight cli processes (stop spawning new ones).
    deadline = time.time() + 30
    for p, outp in active:
        remaining = max(0.1, deadline - time.time())
        try:
            p.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            p.kill()
    if daemon_proc:
        daemon_proc.send_signal(signal.SIGTERM)
        try:
            daemon_proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            daemon_proc.kill()

    wall_s = time.time() - start
    return {"n_cli_spawned": seq, "wall_s": wall_s, "n_distinct_pids": len(pids_seen)}


def load_ops(trial_dir):
    ops = []
    for path in glob.glob(str(Path(trial_dir) / "*.jsonl")):
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ops.append(json.loads(line))
                except json.JSONDecodeError:
                    pass  # partial or torn line. The caller flags it separately if it is frequent
    return ops


def percentile(sorted_vals, p):
    if not sorted_vals:
        return None
    k = (len(sorted_vals) - 1) * p
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return sorted_vals[f]
    return sorted_vals[f] + (sorted_vals[c] - sorted_vals[f]) * (k - f)


def classify_error(error_text):
    """Return which SQL statement/table an MLflow write failure occurred on.
    The function parses the real exception text. It does not infer the table
    from the call structure. The real, unmodified SqlAlchemyStore does the
    `metrics` history insert and the `latest_metrics` upsert as two separate
    statements in one call. So for a single log_metric() failure, only the
    error text itself can say which one it was. The function checks
    "latest_metrics" before the bare "metrics" table name, because the former
    contains the latter as a substring.
    """
    if not error_text:
        return "unknown"
    text = error_text.lower()
    if "latest_metrics" in text:
        return "latest_metrics"
    if "into metrics" in text or "metrics (" in text or '"metrics"' in text:
        return "metrics"
    return "other"


def analyze_trial(trial_dir, wall_s):
    ops = load_ops(trial_dir)
    total = len(ops)
    oks = [o for o in ops if o.get("ok")]
    fails = [o for o in ops if not o.get("ok")]
    lat_ms = sorted(o["latency_us"] / 1000.0 for o in oks)
    # Pure training-compute time (partial_fit/predict_proba/log_loss). It is
    # only present on the step records of the main training thread
    # (ray_trainable.py), not on monitor-thread records. The code is identical
    # for every backend. So any difference across backends reflects host
    # resource contention (for example a client-server backend's own processes
    # that compete for CPU), not the training code itself.
    compute_lat_ms = sorted(o["compute_latency_us"] / 1000.0 for o in oks if "compute_latency_us" in o)
    error_counts = {}
    error_counts_by_category = {}
    for o in fails:
        # The grouping key is capped at 160 chars on purpose. ray_trainable.py
        # captures up to 600 chars per error, because classify_error() needs
        # to see the failing SQL statement. But the full error text includes
        # per-call parameters (values/timestamps/run_uuid). They make the full
        # text of every instance unique. Grouping on the full text blew up
        # error_counts to one entry per failure (hundreds of near-duplicate
        # ~600-char keys). That broke CSV serialization outright, with a real
        # field-size-limit error, not only an unwieldy report. 160 chars keep
        # the generic error shape (exception type + "database is locked" etc.)
        # without the per-call parameter tail.
        error_text = o.get("error", "?")
        error_counts[error_text[:160]] = error_counts.get(error_text[:160], 0) + 1
        category = classify_error(error_text)
        error_counts_by_category[category] = error_counts_by_category.get(category, 0) + 1

    fails_metrics_table = error_counts_by_category.get("metrics", 0) + error_counts_by_category.get("unknown", 0)

    summary = {
        "total_ops": total,
        "ok_ops": len(oks),
        "failed_ops": len(fails),
        "error_rate": (len(fails) / total) if total else None,
        "error_counts": error_counts,
        "error_counts_by_category": error_counts_by_category,
        # Error rate that counts only failures on the `metrics` history write.
        # That write is the operation that is swapped between backends. It
        # excludes `latest_metrics` failures. Those happen on real SQL for
        # every backend, including ozonedb, and they are not part of the
        # comparison. "unknown"-category failures (the message matched neither
        # pattern) are counted here, not silently dropped. So this number is
        # a ceiling, not an undercount.
        "error_rate_metrics_only": (fails_metrics_table / total) if total else None,
        "throughput_ops_sec": total / wall_s if wall_s > 0 else None,
        "successful_throughput_ops_sec": len(oks) / wall_s if wall_s > 0 else None,
        "p50_latency_ms": percentile(lat_ms, 0.50),
        "p95_latency_ms": percentile(lat_ms, 0.95),
        "p99_latency_ms": percentile(lat_ms, 0.99),
        "mean_latency_ms": (sum(lat_ms) / len(lat_ms)) if lat_ms else None,
        "p50_compute_latency_ms": percentile(compute_lat_ms, 0.50),
        "p99_compute_latency_ms": percentile(compute_lat_ms, 0.99),
        "mean_compute_latency_ms": (sum(compute_lat_ms) / len(compute_lat_ms)) if compute_lat_ms else None,
    }
    return summary, ops


def build_manifest(ops, manifest_path):
    """Only the *last write intent* per key needs a check. Every key in this
    workload is generated once per process invocation. So there is at most
    one state-bearing op per key, and no last-writer-wins logic is needed.
    We take whichever op recorded a non-empty final_state for that key. That
    should be exactly one op, or zero if the op itself failed."""
    with open(manifest_path, "w") as f:
        for o in ops:
            state = o.get("final_state", "")
            if not state:
                continue
            key = o["key"]
            if state == "present":
                f.write(f"present {key} {o.get('value','')}\n")
            elif state == "absent":
                f.write(f"absent {key}\n")
