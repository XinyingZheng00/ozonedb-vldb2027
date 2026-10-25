#!/usr/bin/env python3
"""Ray Tune-driven MLflow swap-in benchmark. It uses real training and
real Ray worker processes (not a synthetic loop that mimics the shape). It
runs one run per trial and one thread per trial. We confirmed this pattern
via the own docs and source of Ray Tune and the own thread-safety guidance
of MLflow during this investigation. It supersedes the synthetic
multi-threaded design of run_mlflow_swapin.py, for the reasons documented
in that investigation.

Each Ray Tune trial = one real OS process. It trains a real (small, fast)
SGDClassifier via partial_fit and logs its real per-step training loss.
"""
import argparse
import csv
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import orchestrator as orch  # noqa: E402
import config_template  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ray_trainable import train_and_log  # noqa: E402


def pg_admin_conn():
    import psycopg2
    return psycopg2.connect(
        host="127.0.0.1", port=5432, user="ozonedb_bench", password="bench",
        dbname="postgres",
    )


def make_pg_database(name):
    conn = pg_admin_conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(f'DROP DATABASE IF EXISTS "{name}"')
    cur.execute(f'CREATE DATABASE "{name}" OWNER ozonedb_bench')
    cur.close()
    conn.close()


def run_one(backend, p, k_steps, n_samples, cpus_per_trial, results_root, repeat_idx,
            with_monitor=False, monitor_interval_s=0.5,
            with_disk_monitor=False, disk_monitor_interval_s=0.5,
            serialize_commits=False):
    import ray
    from ray import tune

    label = f"{backend}_serialized" if serialize_commits else backend
    trial_name = f"{label}_p{p}_r{repeat_idx}"
    trial_dir = results_root / trial_name
    if trial_dir.exists():
        shutil.rmtree(trial_dir)
    trial_dir.mkdir(parents=True)
    artifact_root = str(trial_dir / "artifacts")

    ozonedb_config_arg = None
    if backend == "sqlite":
        db_uri = f"sqlite:///{trial_dir / 'mlflow.db'}"
    elif backend == "ozonedb":
        db_uri = f"sqlite:///{trial_dir / 'mlflow.db'}"
        ozonedb_db_dir = trial_dir / "ozonedb"
        ozonedb_config_path = trial_dir / "ozonedb_config.json"
        config_template.write_config(ozonedb_db_dir, ozonedb_config_path)
        ozonedb_config_arg = str(ozonedb_config_path)
    elif backend == "postgres":
        pg_db_name = f"rayswapin_{label}_p{p}_r{repeat_idx}"
        make_pg_database(pg_db_name)
        db_uri = f"postgresql://ozonedb_bench:bench@127.0.0.1:5432/{pg_db_name}"
    else:
        raise ValueError(backend)

    from mlflow.store.tracking.sqlalchemy_store import SqlAlchemyStore
    setup_store = SqlAlchemyStore(db_uri, artifact_root)
    exp_id = setup_store.create_experiment(f"rayswapin_{label}_p{p}_r{repeat_idx}")

    ray.init(ignore_reinit_error=True, include_dashboard=False, logging_level="ERROR")
    try:
        base_config = {
            "backend": backend, "db_uri": db_uri, "artifact_root": artifact_root,
            "ozonedb_config": ozonedb_config_arg, "experiment_id": exp_id,
            "out_dir": str(trial_dir), "k_steps": k_steps, "n_samples": n_samples,
            "with_monitor": with_monitor, "monitor_interval_s": monitor_interval_s,
            "with_disk_monitor": with_disk_monitor, "disk_monitor_interval_s": disk_monitor_interval_s,
            "serialize_commits": serialize_commits,
        }
        param_space = dict(base_config)
        param_space["trial_id"] = tune.grid_search(list(range(p)))
        param_space["seed"] = tune.sample_from(lambda spec: spec.config.trial_id)
        param_space["lr"] = tune.loguniform(1e-3, 1e-1)
        param_space["alpha"] = tune.loguniform(1e-5, 1e-2)

        tuner = tune.Tuner(
            tune.with_resources(train_and_log, {"cpu": cpus_per_trial}),
            param_space=param_space,
            tune_config=tune.TuneConfig(num_samples=1),
        )
        from resource_sampler import ResourceSampler
        sampler = ResourceSampler(interval_s=1.0, sample_postgres=(backend == "postgres"))
        sampler.start()
        start = time.time()
        results = tuner.fit()
        wall_s = time.time() - start
        sampler.stop()
        resource_summary = sampler.summary()
    finally:
        ray.shutdown()

    n_errors_ray = sum(
        1 for r in results if r.error is not None
    )
    if n_errors_ray:
        print(f"  [{trial_name}] {n_errors_ray} Ray trial(s) errored at the framework level "
              f"(not counted in per-op error_rate below)", file=sys.stderr)

    summary, ops = orch.analyze_trial(trial_dir, wall_s)
    row = {
        "backend": label, "n_processes": p, "k_steps": k_steps,
        "n_samples": n_samples, "repeat": repeat_idx, "wall_s": wall_s,
        "n_trials_errored": n_errors_ray,
        **summary,
        **resource_summary,
    }
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--processes", default="16,32")
    ap.add_argument("--k-steps", type=int, default=100)
    ap.add_argument("--n-samples", type=int, default=2000)
    ap.add_argument("--cpus-per-trial", type=float, default=0.5)
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--backends", default="sqlite")
    ap.add_argument(
        "--out-dir",
        default=str(Path(__file__).resolve().parent.parent.parent.parent
                    / "bench" / "results" / "local" / "usecases" / "ray_mlflow_swapin"),
    )
    ap.add_argument("--with-monitor", action="store_true",
                     help="spawn a background thread per trial logging real "
                          "cpu_percent/mem_percent under their own keys, alongside "
                          "the main training thread's loss logging")
    ap.add_argument("--monitor-interval-s", type=float, default=0.5)
    ap.add_argument("--with-disk-monitor", action="store_true",
                     help="spawn a third (independent of --with-monitor) background "
                          "thread per trial logging real disk_read_mb_s/disk_write_mb_s "
                          "under their own keys -- more concurrent writers per process, "
                          "not more keys from an existing thread")
    ap.add_argument("--disk-monitor-interval-s", type=float, default=0.5)
    ap.add_argument("--serialize-commits", action="store_true",
                     help="Postgres arm only, control experiment (not real "
                          "MLflow/Postgres behavior): force every commit on the "
                          "Postgres engine to serialize via a transaction-scoped "
                          "advisory lock, so no two can ever be in flight at once "
                          "-- tests whether Postgres's speed at concurrency is "
                          "explained by WAL group commit. Recorded under backend "
                          "label '<backend>_serialized' in trials.csv.")
    args = ap.parse_args()

    processes = [int(x) for x in args.processes.split(",")]
    backends = args.backends.split(",")
    results_root = Path(args.out_dir)
    results_root.mkdir(parents=True, exist_ok=True)

    rows = []
    for backend in backends:
        for p in processes:
            for r in range(args.repeats):
                t0 = time.time()
                row = run_one(backend, p, args.k_steps, args.n_samples,
                               args.cpus_per_trial, results_root, r,
                               with_monitor=args.with_monitor,
                               monitor_interval_s=args.monitor_interval_s,
                               with_disk_monitor=args.with_disk_monitor,
                               disk_monitor_interval_s=args.disk_monitor_interval_s,
                               serialize_commits=args.serialize_commits)
                print(f"[{backend} p={p} k={args.k_steps} r={r}] "
                      f"tput={row['throughput_ops_sec']:.1f} ops/s  "
                      f"err_rate={row['error_rate']:.4f}  "
                      f"p50={row['p50_latency_ms']:.3f}ms  p99={row['p99_latency_ms']:.3f}ms  "
                      f"errors={row['error_counts']}  "
                      f"trials_errored={row['n_trials_errored']}  "
                      f"({time.time()-t0:.1f}s wall)", flush=True)
                rows.append(row)

    csv_path = results_root / "trials.csv"
    fieldnames = list(rows[0].keys())
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in rows:
            row = dict(row)
            row["error_counts"] = json.dumps(row["error_counts"])
            w.writerow(row)
    print(f"\nwrote {csv_path}")


if __name__ == "__main__":
    main()
