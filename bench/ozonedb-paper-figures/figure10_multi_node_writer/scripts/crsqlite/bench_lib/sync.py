"""Sync-daemon lifecycle, peer topology, and convergence detection.

Topology: a full mesh over the global writer indices. Writer j's daemon
listens on sync.port_base + j. Single-node runs dial 127.0.0.1. Multi-node
runs dial the host LAN addresses from cloudlab.lan_hosts. Host i holds the
writers [i*writers_per_host, (i+1)*writers_per_host).

Convergence: cheap /status polls (row counts and db_versions) until they are
stable, then one /hash round to confirm identical content. If expected_rows is
set (the load phase knows the total), the counts must reach it first.
"""

import json
import os
import signal
import subprocess
import sys
import time
from urllib.request import urlopen

_HERE = os.path.dirname(os.path.abspath(__file__))
DAEMON_SCRIPT = os.path.join(os.path.dirname(_HERE), "sync_daemon.py")


def sync_settings(cfg):
    s = cfg.get("sync") or {}
    return {
        "enabled": bool(s.get("enabled", True)),
        "interval_ms": float(s.get("interval_ms", 100)),
        "batch": int(s.get("batch", 4096)),
        "port_base": int(s.get("port_base", 7801)),
        "read_barrier": bool(s.get("read_barrier", False)),
    }


def endpoints(cfg, total_writers, writers_per_host, multinode):
    """Return the host:port of every writer's daemon, indexed by the global writer index."""
    port_base = sync_settings(cfg)["port_base"]
    if not multinode:
        return [f"127.0.0.1:{port_base + j}" for j in range(total_writers)]
    lan_hosts = (cfg.get("cloudlab") or {}).get("lan_hosts") or []
    eps = []
    for j in range(total_writers):
        host_idx = j // writers_per_host
        if host_idx >= len(lan_hosts):
            raise ValueError(
                f"writer {j} maps to host index {host_idx} but only "
                f"{len(lan_hosts)} clients are configured in nodes.clients"
            )
        eps.append(f"{lan_hosts[host_idx]}:{port_base + j}")
    return eps


def start_daemon(cfg, writer_idx, db_path, all_endpoints, extension, log_path,
                 metrics_path, synchronous, assume_converged=False):
    """Spawn writer_idx's daemon. Return the Popen handle."""
    s = sync_settings(cfg)
    port = int(all_endpoints[writer_idx].rsplit(":", 1)[1])
    peers = [e for j, e in enumerate(all_endpoints) if j != writer_idx]
    cmd = [
        sys.executable, DAEMON_SCRIPT,
        "--db", db_path,
        "--port", str(port),
        "--peers", ",".join(peers),
        "--interval-ms", str(s["interval_ms"]),
        "--batch", str(s["batch"]),
        "--extension", extension,
        "--synchronous", synchronous,
        "--metrics", metrics_path,
    ]
    if assume_converged:
        cmd.append("--assume-converged")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    f = open(log_path, "a")
    print(f"[syncd w{writer_idx}] {' '.join(cmd)}")
    return subprocess.Popen(cmd, stdout=f, stderr=f)


def stop_daemons(procs, timeout=15):
    for p in procs:
        if p.poll() is None:
            p.terminate()
    deadline = time.time() + timeout
    for p in procs:
        try:
            p.wait(timeout=max(0.1, deadline - time.time()))
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()


def pause_daemons(procs):
    """Send SIGSTOP, so that a divergence snapshot is not a moving target."""
    for p in procs:
        if p.poll() is None:
            os.kill(p.pid, signal.SIGSTOP)


def resume_daemons(procs):
    for p in procs:
        if p.poll() is None:
            os.kill(p.pid, signal.SIGCONT)


def _get(endpoint, path, timeout=30):
    with urlopen(f"http://{endpoint}{path}", timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_status(endpoint):
    return _get(endpoint, "/status")


def get_hash(endpoint, timeout=600):
    return _get(endpoint, "/hash", timeout=timeout)


def get_barrier_stats(endpoint):
    """Return the distribution of /barrier round times since daemon startup.
    The run harness scrapes it once per run for the [SYNCREAD] aggregate lines."""
    return _get(endpoint, "/barrier_stats")


def get_round_stats(endpoint, reset=False):
    """Return the background (interval) round distribution over the daemon's
    current window. It includes the ACHIEVED anti-entropy period
    (period_mean_ms) against the nominal interval. reset=True restarts the
    window after the snapshot. The run phase resets the window when the
    writers start and scrapes it when they finish. So the [CONSISTENCY]
    SyncRound*/SyncPeriodMean lines cover exactly the writer window."""
    return _get(endpoint, "/round_stats?reset=1" if reset else "/round_stats")


def round_stat_lines(eps, indices, section="[CONSISTENCY]"):
    """Return aggregate lines for the achieved sync round behaviour of the
    daemons at eps[j] for j in indices. The host-level summary comes first:
    the mean of the per-writer p50 and achieved period, and the max of p99.
    Then the per-writer detail follows, in the same shape as the [SYNCREAD]
    Writer{j}Barrier* lines."""
    stats = [(j, get_round_stats(eps[j])) for j in indices]
    lines = [
        f"{section}, SyncRoundCount, {sum(s['count'] for _, s in stats)}",
        f"{section}, SyncPullFailures, {sum(s['pull_failures'] for _, s in stats)}",
        f"{section}, SyncRowsApplied, {sum(s['rows_applied'] for _, s in stats)}",
    ]
    p50s = [s["p50_ms"] for _, s in stats if s["count"]]
    p99s = [s["p99_ms"] for _, s in stats if s["count"]]
    periods = [s["period_mean_ms"] for _, s in stats if s["count"] >= 2]
    if p50s:
        lines.append(f"{section}, SyncRoundP50(ms), {sum(p50s) / len(p50s):.3f}")
    if p99s:
        lines.append(f"{section}, SyncRoundP99(ms), {max(p99s):.3f}")
    if periods:
        lines.append(
            f"{section}, SyncPeriodMean(ms), {sum(periods) / len(periods):.3f}"
        )
    for j, s in stats:
        lines.append(f"{section}, Writer{j}SyncRoundP50(ms), {s['p50_ms']}")
        lines.append(f"{section}, Writer{j}SyncRoundP99(ms), {s['p99_ms']}")
        lines.append(f"{section}, Writer{j}SyncPeriodMean(ms), {s['period_mean_ms']}")
    return lines


def wait_until_up(eps, timeout_s=60):
    """Wait until every daemon is up AND ready. With --assume-converged, ready
    means that the cursors are stamped from every peer. The writers are gated
    on ready, and that is what makes the stamp happen strictly before any
    write. Daemons without the flag report ready immediately. Pre-flag daemons
    lack the field, and the function treats them as ready."""
    deadline = time.time() + timeout_s
    pending = list(eps)
    while pending:
        if time.time() > deadline:
            raise TimeoutError(f"sync daemons never came up/ready: {pending}")
        still = []
        for e in pending:
            try:
                if not get_status(e).get("ready", True):
                    still.append(e)
            except Exception:
                still.append(e)
        pending = still
        if pending:
            time.sleep(0.5)


def wait_for_convergence(eps, expected_rows=None, poll_ms=1000, timeout_s=900):
    """Block until every replica behind `eps` holds identical content.
    Return the elapsed seconds. Raise TimeoutError with the last statuses."""
    start = time.time()
    prev_versions = None
    while True:
        if time.time() - start > timeout_s:
            raise TimeoutError(
                f"replicas did not converge within {timeout_s}s; last={prev_versions}"
            )
        try:
            statuses = [get_status(e) for e in eps]
        except Exception as exc:
            print(f"[converge] status poll failed ({exc}); retrying")
            time.sleep(poll_ms / 1000.0)
            continue

        counts = [s["rows"] for s in statuses]
        versions = [s["db_version"] for s in statuses]
        counts_ok = (
            all(c == expected_rows for c in counts)
            if expected_rows is not None
            else len(set(counts)) == 1
        )
        if counts_ok and versions == prev_versions:
            # The replicas are quiescent by the cheap signals. Confirm with content hashes.
            try:
                hashes = [get_hash(e)["hash"] for e in eps]
            except Exception as exc:
                print(f"[converge] hash poll failed ({exc}); retrying")
                prev_versions = None
                time.sleep(poll_ms / 1000.0)
                continue
            if len(set(hashes)) == 1:
                return time.time() - start
            print(f"[converge] hashes still differ across {len(eps)} replicas")
        prev_versions = versions
        time.sleep(poll_ms / 1000.0)
