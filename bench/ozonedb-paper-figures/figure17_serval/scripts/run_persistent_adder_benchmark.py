#!/usr/bin/env python3
"""Fixed-duration 3-way comparison, reproducing serval-dna issue #1's literal
scenario (a persistent daemon connection racing independently-opened CLI
connections on the same store) for `sqlite` and `ozonedb`, against the
`sqlite_rest` re-architected fix.

sqlite / ozonedb: one real servald daemon runs for the whole trial with its
benchmark-only persistent_adder_tick alarm active (server.c, gated by
RHIZOME_PERSISTENT_ADD_INTERVAL_MS) -- a genuinely persistent, once-opened
connection issuing real add-file operations on a fixed interval, exactly
mirroring how the real daemon holds rhizome_database.db open for its whole
lifetime. Concurrently, N independent CLI worker processes run `rhizome add
file` / `rhizome extract file` against the SAME store, each a fresh
subprocess per operation (matching how every real rhizome CLI invocation
opens its own separate connection). Both actors race the same file for the
whole trial.

sqlite_rest: one real servald daemon serves the REST API -- the ONLY
process that ever opens the store, by construction, so there is no second
connection to race. N+1 REST client processes (not N) hit it concurrently,
volume-matched against the other two backends' "N CLI workers + 1 daemon
adder" = N+1 total add-issuing actors, so all three backends face the same
total request volume; the daemon itself still never originates a request.

Trials run for a FIXED WALL-CLOCK DURATION: the adder ticks the same number of
times regardless of N, so N is the only thing varying across the sweep, and workers simply complete as many cycles as they can in the window (better latency-percentile sample sizes at low N
too).
"""
import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OZONEDB_HOME = Path(os.environ.get("OZONEDB_HOME", Path.home() / "ozonedb"))
# serval-dna with the OzoneDB Rhizome backend: the bench/usecases/serval-dna submodule, built in place.
SERVALD = OZONEDB_HOME / "bench" / "usecases" / "serval-dna" / "servald"

sys.path.insert(0, str(HERE))  # config_template.py
import config_template  # noqa: E402


def run(cmd, env, timeout=30):
    return subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=timeout)


def setup_identity(instance_dir, env):
    r = run([str(SERVALD), "keyring", "add"], env)
    m = re.search(r"^sid:([0-9A-Fa-f]+)", r.stdout, re.MULTILINE)
    if not m:
        raise RuntimeError(f"keyring add failed: {r.stdout}\n{r.stderr}")
    return m.group(1)


def wait_for_port(port, timeout_s=10):
    import socket
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.1)
    return False


def parse_daemon_tick_durations(daemon_instance_dir):
    """Returns the list of per-tick durations (ms, ints) for every
    successful persistent_adder_tick, parsed from the daemon's own log
    line: 'persistent_adder_tick: added bundle at <now> duration_ms=<n>'.
    Measures the same span as a CLI worker's t_add (from just after the
    interval/env gating checks to just before the log line), so it's
    directly comparable to mean_add_ms/p99_add_ms."""
    log_path = daemon_instance_dir / "serval.log"
    if not log_path.exists():
        return []
    try:
        text = log_path.read_text(errors="replace")
    except OSError:
        return []
    return [int(m) for m in re.findall(
        r"persistent_adder_tick: added bundle at \d+ duration_ms=(\d+)", text)]


def worker_cycle(worker_id, seq, env, sid, tmp_files_dir):
    content = f"worker={worker_id} seq={seq} payload={os.urandom(16).hex()}\n".encode()
    src = tmp_files_dir / f"w{worker_id}_{seq}.txt"
    src.write_bytes(content)

    t0 = time.time()
    try:
        r_add = run([str(SERVALD), "rhizome", "add", "file", sid, str(src)], env, timeout=60)
    except subprocess.TimeoutExpired:
        return {"ok": False, "stage": "add", "t_add": time.time() - t0, "t_extract": None,
                "error": "subprocess timeout (60s)"}
    t_add = time.time() - t0
    if r_add.returncode != 0:
        return {"ok": False, "stage": "add", "t_add": t_add, "t_extract": None,
                "error": (r_add.stdout + r_add.stderr)[-300:]}

    m_bid = re.search(r"^manifestid:([0-9A-Fa-f]+)", r_add.stdout, re.MULTILINE)
    if not m_bid:
        return {"ok": False, "stage": "add-parse", "t_add": t_add, "t_extract": None,
                "error": (r_add.stdout + r_add.stderr)[-300:]}
    bid = m_bid.group(1)

    out = tmp_files_dir / f"w{worker_id}_{seq}.out"
    t1 = time.time()
    try:
        r_ext = run([str(SERVALD), "rhizome", "extract", "file", bid, str(out)], env, timeout=60)
    except subprocess.TimeoutExpired:
        return {"ok": False, "stage": "extract", "t_add": t_add, "t_extract": time.time() - t1,
                "error": "subprocess timeout (60s)"}
    t_extract = time.time() - t1
    if r_ext.returncode != 0:
        return {"ok": False, "stage": "extract", "t_add": t_add, "t_extract": t_extract,
                "error": (r_ext.stdout + r_ext.stderr)[-300:]}

    if not out.exists() or out.read_bytes() != content:
        return {"ok": False, "stage": "verify-mismatch", "t_add": t_add, "t_extract": t_extract,
                "error": "content mismatch"}

    return {"ok": True, "stage": None, "t_add": t_add, "t_extract": t_extract, "error": None}


def worker_loop_until(deadline, worker_id, env, sid, tmp_files_dir):
    rows = []
    seq = 0
    while time.time() < deadline:
        try:
            rows.append(worker_cycle(worker_id, seq, env, sid, tmp_files_dir))
        except Exception as e:
            rows.append({"ok": False, "stage": "worker-exception", "t_add": None, "t_extract": None,
                          "error": f"{type(e).__name__}: {e}"[:300]})
        seq += 1
    return rows


def worker_main_direct(worker_id, deadline, backend, oz_config, sqlite_store, trial_dir):
    instance_dir = trial_dir / f"worker_{worker_id}"
    instance_dir.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["SERVALINSTANCE_PATH"] = str(instance_dir)
    if backend == "ozonedb":
        env["RHIZOME_OZONEDB_CONFIG"] = oz_config
        env.pop("RHIZOME_PERSISTENT_ADD_INTERVAL_MS", None)
    else:
        env.pop("RHIZOME_OZONEDB_CONFIG", None)
        env.pop("RHIZOME_PERSISTENT_ADD_INTERVAL_MS", None)
        (instance_dir / "serval.conf").write_text(f"rhizome.datastore_path={sqlite_store}\n")
    try:
        sid = setup_identity(instance_dir, env)
        tmp_files_dir = trial_dir / f"worker_files_{worker_id}"
        tmp_files_dir.mkdir(parents=True, exist_ok=True)
        return worker_loop_until(deadline, worker_id, env, sid, tmp_files_dir)
    except Exception as e:
        return [{"ok": False, "stage": "worker-exception", "t_add": None, "t_extract": None,
                 "error": f"{type(e).__name__}: {e}"[:300]}]


def run_trial_daemon_cli(backend, n_workers, duration_s, trial_dir, adder_interval_ms):
    """backend: 'sqlite' or 'ozonedb'. One real daemon (persistent_adder_tick
    active) + N independent CLI workers, both racing the same store."""
    short_root = Path(f"/tmp/persist_bench_{trial_dir.name}")
    if short_root.exists():
        shutil.rmtree(short_root)
    short_root.mkdir(parents=True)
    try:
        daemon_dir = short_root / "daemon"
        daemon_dir.mkdir(parents=True)
        env_d = os.environ.copy()
        env_d["SERVALINSTANCE_PATH"] = str(daemon_dir)
        env_d["RHIZOME_PERSISTENT_ADD_INTERVAL_MS"] = str(adder_interval_ms)

        oz_config = None
        sqlite_store = None
        if backend == "ozonedb":
            db_dir = trial_dir / "ozdb"
            oz_config = str(trial_dir / "shared_config.json")
            config_template.write_config(db_dir, oz_config)
            env_d["RHIZOME_OZONEDB_CONFIG"] = oz_config
        else:
            sqlite_store = str(trial_dir / "shared_store")
            Path(sqlite_store).mkdir(parents=True, exist_ok=True)
            run([str(SERVALD), "config", "set", "rhizome.datastore_path", sqlite_store], env_d)

        setup_identity(daemon_dir, env_d)
        r = run([str(SERVALD), "start"], env_d)
        if "status:running" not in r.stdout:
            rows = [{"ok": False, "stage": "daemon-start", "t_add": None, "t_extract": None,
                      "error": (r.stdout + r.stderr)[-300:]} for _ in range(n_workers)]
            return rows, {"wall_s": 0.001, "daemon_ticks": 0, "daemon_tick_durations_ms": []}

        deadline = time.time() + duration_s
        from concurrent.futures import ThreadPoolExecutor
        t0 = time.time()
        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            futures = [ex.submit(worker_main_direct, w, deadline, backend, oz_config, sqlite_store, trial_dir)
                       for w in range(n_workers)]
            all_rows = []
            for f in futures:
                all_rows.extend(f.result())
        wall_s = time.time() - t0

        tick_durations = parse_daemon_tick_durations(daemon_dir)
        run([str(SERVALD), "stop"], env_d)
        return all_rows, {"wall_s": wall_s, "daemon_ticks": len(tick_durations),
                           "daemon_tick_durations_ms": tick_durations}
    finally:
        shutil.rmtree(short_root, ignore_errors=True)


def run_trial_sqlite_rest(n_workers, duration_s, trial_dir, port):
    """One real daemon serving the REST API (the sole writer, no adder
    needed/possible), N+1 REST clients -- volume-matched against the other
    two backends' N CLI workers + 1 daemon adder = N+1 total actors."""
    actual_clients = n_workers + 1
    short_root = Path(f"/tmp/persist_bench_{trial_dir.name}")
    if short_root.exists():
        shutil.rmtree(short_root)
    short_root.mkdir(parents=True)
    try:
        daemon_dir = short_root / "daemon"
        daemon_dir.mkdir(parents=True)
        store_dir = trial_dir / "shared_rhizome_store"
        store_dir.mkdir(parents=True, exist_ok=True)
        (daemon_dir / "serval.conf").write_text(
            f"rhizome.datastore_path={store_dir}\n"
            f"rhizome.http.port={port}\n"
            f"api.restful.users.bench.password=benchpw\n"
        )
        env = os.environ.copy()
        env["SERVALINSTANCE_PATH"] = str(daemon_dir)
        env.pop("RHIZOME_PERSISTENT_ADD_INTERVAL_MS", None)
        env.pop("RHIZOME_OZONEDB_CONFIG", None)

        r = run([str(SERVALD), "start"], env)
        if "status:running" not in r.stdout:
            rows = [{"ok": False, "stage": "daemon-start", "t_add": None, "t_extract": None,
                      "error": (r.stdout + r.stderr)[-300:]} for _ in range(actual_clients)]
            return rows, {"wall_s": 0.001, "daemon_ticks": None}
        if not wait_for_port(port):
            run([str(SERVALD), "stop"], env)
            rows = [{"ok": False, "stage": "daemon-not-ready", "t_add": None, "t_extract": None,
                      "error": "http port never opened"} for _ in range(actual_clients)]
            return rows, {"wall_s": 0.001, "daemon_ticks": None}

        base_url = f"http://127.0.0.1:{port}"
        procs = []
        out_files = []
        t0 = time.time()
        for w in range(actual_clients):
            out_path = trial_dir / f"rest_worker_{w}.jsonl"
            out_files.append(out_path)
            cmd = [sys.executable, str(HERE / "rest_worker.py"),
                   "--base-url", base_url, "--user", "bench", "--password", "benchpw",
                   "--worker-id", str(w), "--duration-s", str(duration_s), "--out", str(out_path)]
            procs.append(subprocess.Popen(cmd))
        for p in procs:
            p.wait(timeout=duration_s + 60)
        wall_s = time.time() - t0

        run([str(SERVALD), "stop"], env)

        all_rows = []
        for out_path in out_files:
            if out_path.exists():
                for line in out_path.read_text().splitlines():
                    if line.strip():
                        import json
                        all_rows.append(json.loads(line))
        return all_rows, {"wall_s": wall_s, "daemon_ticks": None}
    finally:
        shutil.rmtree(short_root, ignore_errors=True)


def summarize(backend, n_workers, all_rows, wall_s, daemon_ticks, duration_s, daemon_tick_durations_ms=None):
    oks = [r for r in all_rows if r["ok"]]
    fails = [r for r in all_rows if not r["ok"]]
    total = len(all_rows)
    add_times_ms = sorted(r["t_add"] * 1000 for r in oks if r.get("t_add") is not None)
    ext_times_ms = sorted(r["t_extract"] * 1000 for r in oks if r.get("t_extract") is not None)
    daemon_durs = sorted(daemon_tick_durations_ms) if daemon_tick_durations_ms else []

    def pct(vals, p):
        if not vals:
            return None
        k = (len(vals) - 1) * p
        f, c = int(k), min(int(k) + 1, len(vals) - 1)
        return vals[f] if f == c else vals[f] + (vals[c] - vals[f]) * (k - f)

    return {
        "backend": backend, "n_workers": n_workers, "total_add_actors": n_workers + 1,
        "duration_s": duration_s, "total": total, "ok": len(oks), "fail": len(fails),
        "wall_s": wall_s, "cycles_per_sec": total / wall_s if wall_s > 0 else None,
        "mean_add_ms": (sum(add_times_ms) / len(add_times_ms)) if add_times_ms else None,
        "p50_add_ms": pct(add_times_ms, 0.5), "p99_add_ms": pct(add_times_ms, 0.99),
        "mean_extract_ms": (sum(ext_times_ms) / len(ext_times_ms)) if ext_times_ms else None,
        "daemon_ticks": daemon_ticks,
        # Directly comparable to mean_add_ms/p99_add_ms above -- same span
        # (add-operation work only, not process/subprocess launch overhead
        # on either side), parsed from persistent_adder_tick()'s own
        # duration_ms= log field. None for sqlite_rest (no adder there).
        "daemon_mean_tick_ms": (sum(daemon_durs) / len(daemon_durs)) if daemon_durs else None,
        "daemon_p99_tick_ms": pct(daemon_durs, 0.99),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", default="1,2,4,8")
    ap.add_argument("--duration-s", type=float, default=8.0)
    ap.add_argument("--adder-interval-ms", type=int, default=100)
    ap.add_argument("--backends", default="sqlite,ozonedb,sqlite_rest")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--out-dir",  # never the paper's data/paper_data
                    default=str(HERE.parent / "data" / "reproduced_data" / time.strftime("%Y%m%d")))
    args = ap.parse_args()

    if not SERVALD.exists():
        print(f"FATAL: {SERVALD} not built", file=sys.stderr)
        sys.exit(1)

    workers_list = [int(x) for x in args.workers.split(",")]
    backends = args.backends.split(",")
    results_root = Path(args.out_dir).resolve()
    if results_root.exists() and any(results_root.iterdir()):
        sys.exit(f"{results_root} already has results; pass another --out-dir")
    results_root.mkdir(parents=True, exist_ok=True)
    summary_rows = []

    for backend in backends:
        for n in workers_list:
            for rep in range(1, args.repeats + 1):
                suffix = f"_rep{rep}" if args.repeats > 1 else ""
                trial_dir = results_root / f"{backend}_n{n}{suffix}"
                trial_dir.mkdir(parents=True)
                if backend == "sqlite_rest":
                    port = 4300 + n
                    all_rows, diag = run_trial_sqlite_rest(n, args.duration_s, trial_dir, port)
                else:
                    all_rows, diag = run_trial_daemon_cli(backend, n, args.duration_s, trial_dir, args.adder_interval_ms)

                row = summarize(backend, n, all_rows, diag["wall_s"], diag.get("daemon_ticks"), args.duration_s,
                                 diag.get("daemon_tick_durations_ms"))
                row["repeat"] = rep
                summary_rows.append(row)

                tick_str = (f" daemon_ticks={row['daemon_ticks']} daemon_mean_tick_ms={row['daemon_mean_tick_ms']:.1f}"
                            if row["daemon_ticks"] is not None and row["daemon_mean_tick_ms"] is not None
                            else (f" daemon_ticks={row['daemon_ticks']}" if row["daemon_ticks"] is not None else ""))
                client_str = f" (N+1={n+1} REST clients)" if backend == "sqlite_rest" else ""
                rep_str = f" rep={rep}/{args.repeats}" if args.repeats > 1 else ""
                if row["mean_add_ms"] is not None:
                    print(f"[{backend} n={n}{rep_str}]{client_str} total={row['total']} ok={row['ok']} fail={row['fail']} "
                          f"wall={diag['wall_s']:.1f}s cycles/s={row['cycles_per_sec']:.2f} "
                          f"mean_add={row['mean_add_ms']:.1f}ms{tick_str}", flush=True)
                else:
                    print(f"[{backend} n={n}{rep_str}]{client_str} total={row['total']} ok=0 fail={row['fail']}{tick_str}", flush=True)
                fails = [r for r in all_rows if not r["ok"]]
                if fails:
                    for r in fails[:3]:
                        print(f"    FAIL stage={r['stage']} error={r['error']!r}", flush=True)

    csv_path = results_root / "trials.csv"
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        w.writeheader()
        for row in summary_rows:
            w.writerow(row)
    print(f"\nwrote {csv_path}")


if __name__ == "__main__":
    main()
