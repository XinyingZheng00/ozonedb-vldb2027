"""Ray Tune trainable: one real training trial. This matches how Ray Tune
is actually used in practice. We confirmed this via the own docs and
source of Ray during this investigation. The pattern is one OS process
per trial, one MLflow run per trial, and log_metric called inline from
the training loop. There are no replayed or synthetic metric values. The
trainable trains a real (small, fast) model and logs its real loss.

The trainable deliberately does NOT use the built-in MLflow integration
of Ray (setup_mlflow/MLflowLoggerCallback). That integration resolves
stores by URI scheme, and there is no "ozonedb://" scheme registered.
Instead, each trial instantiates our own store directly (SqlAlchemyStore
or OzoneDBMetricStore), exactly like mlflow_swapin_worker.py did. So Ray
handles process orchestration and scheduling, and we keep control of
which backend each trial talks to.

Optional additional threads exist. Each one can be toggled independently.
All of them share the same store instance and run as the main training
thread. Each one writes its own distinct key(s):
  - config["with_monitor"]=True: cpu_percent/mem_percent, via `psutil`.
  - config["with_disk_monitor"]=True: disk_read_mb_s/disk_write_mb_s, via
    deltas of `psutil.disk_io_counters()`. This is a real rate, not the
    raw cumulative-since-boot counter that psutil returns. It is a
    genuinely separate thread, not more keys logged from the existing
    monitor thread. The point is more *concurrent writers* per process,
    not only more metrics.
All of this is a real, common MLflow usage pattern. It is distinct from
the earlier (discarded) design of T=5 threads that share one key: here,
different threads write different keys in the same run. Per the earlier
investigation, this does NOT reintroduce the latest_metrics race removed
from ozonedb_metric_store.py. The UNIQUE constraint that the race
violated is scoped to (run_uuid, key). No two threads here ever write the
same key, however many threads run. Testing confirmed this, not only
reasoning. That reasoning generalizes to any thread count.
"""
import json
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))


def _monitor_loop(store, run_id, stop_event, interval_s, records, lock):
    import psutil
    step = 0
    while not stop_event.is_set():
        cpu = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory().percent
        for key, val in (("cpu_percent", cpu), ("mem_percent", mem)):
            from mlflow.entities import Metric
            t0 = time.perf_counter()
            try:
                store.log_metric(
                    run_id,
                    Metric(key=key, value=float(val), timestamp=int(time.time() * 1000), step=step),
                )
                latency_us = (time.perf_counter() - t0) * 1e6
                rec = {"ok": True, "latency_us": latency_us, "key": f"{key}{step}", "final_state": "present"}
            except Exception as e:  # noqa: BLE001
                latency_us = (time.perf_counter() - t0) * 1e6
                rec = {"ok": False, "latency_us": latency_us, "error": repr(e)[:600], "key": f"{key}{step}"}
            with lock:
                records.append(rec)
        step += 1
        stop_event.wait(interval_s)


def _disk_monitor_loop(store, run_id, stop_event, interval_s, records, lock):
    import psutil
    step = 0
    prev = psutil.disk_io_counters()
    prev_t = time.perf_counter()
    while not stop_event.is_set():
        stop_event.wait(interval_s)
        now = psutil.disk_io_counters()
        now_t = time.perf_counter()
        dt = max(now_t - prev_t, 1e-6)
        read_mb_s = (now.read_bytes - prev.read_bytes) / dt / 1e6
        write_mb_s = (now.write_bytes - prev.write_bytes) / dt / 1e6
        prev, prev_t = now, now_t
        for key, val in (("disk_read_mb_s", read_mb_s), ("disk_write_mb_s", write_mb_s)):
            from mlflow.entities import Metric
            t0 = time.perf_counter()
            try:
                store.log_metric(
                    run_id,
                    Metric(key=key, value=float(val), timestamp=int(time.time() * 1000), step=step),
                )
                latency_us = (time.perf_counter() - t0) * 1e6
                rec = {"ok": True, "latency_us": latency_us, "key": f"{key}{step}", "final_state": "present"}
            except Exception as e:  # noqa: BLE001
                latency_us = (time.perf_counter() - t0) * 1e6
                rec = {"ok": False, "latency_us": latency_us, "error": repr(e)[:600], "key": f"{key}{step}"}
            with lock:
                records.append(rec)
        step += 1


def train_and_log(config):
    import numpy as np
    from mlflow.entities import Metric
    from sklearn.datasets import make_classification
    from sklearn.linear_model import SGDClassifier
    from sklearn.metrics import log_loss

    if config["backend"] == "ozonedb":
        from ozonedb_metric_store import OzoneDBMetricStore
        store = OzoneDBMetricStore(
            config["db_uri"], config["artifact_root"], config["ozonedb_config"]
        )
    else:
        from mlflow.store.tracking.sqlalchemy_store import SqlAlchemyStore
        store = SqlAlchemyStore(config["db_uri"], config["artifact_root"])
        if config.get("serialize_commits") and config["backend"] == "postgres":
            from serialized_postgres_store import enable_commit_serialization
            enable_commit_serialization(store.engine)

    run = store.create_run(
        experiment_id=config["experiment_id"], user_id="ray", start_time=0,
        tags=[], run_name=f"trial-{config['trial_id']}",
    )
    run_id = run.info.run_id

    records = []
    records_lock = threading.Lock()
    extra_threads = []
    stop_events = []
    # Same store instance, same run, genuinely different threads.
    # The _ozonedb() of OzoneDBMetricStore gives each thread its own OzoneDB
    # handle via thread-local storage (the T-convention). The main thread
    # gets its own in the same way. Each toggle below is a separate thread,
    # not more keys logged from one thread. The point is more concurrent
    # writers per process.
    if config.get("with_monitor"):
        stop_event = threading.Event()
        t = threading.Thread(
            target=_monitor_loop,
            args=(store, run_id, stop_event, config.get("monitor_interval_s", 0.5), records, records_lock),
            daemon=True,
        )
        t.start()
        extra_threads.append(t)
        stop_events.append(stop_event)
    if config.get("with_disk_monitor"):
        stop_event = threading.Event()
        t = threading.Thread(
            target=_disk_monitor_loop,
            args=(store, run_id, stop_event, config.get("disk_monitor_interval_s", 0.5), records, records_lock),
            daemon=True,
        )
        t.start()
        extra_threads.append(t)
        stop_events.append(stop_event)

    X, y = make_classification(
        n_samples=config["n_samples"], n_features=20, n_informative=10,
        random_state=config["seed"],
    )
    classes = np.unique(y)
    model = SGDClassifier(
        loss="log_loss", learning_rate="constant",
        eta0=config["lr"], alpha=config["alpha"], random_state=config["seed"],
    )

    k_steps = config["k_steps"]
    batch_size = max(1, len(X) // k_steps)
    for step in range(k_steps):
        start = (step * batch_size) % max(1, len(X) - batch_size)
        Xb, yb = X[start:start + batch_size], y[start:start + batch_size]
        if len(Xb) == 0:
            continue
        t_compute0 = time.perf_counter()
        if step == 0:
            model.partial_fit(Xb, yb, classes=classes)
        else:
            model.partial_fit(Xb, yb)
        # Real training loss on this batch. It is not a replayed or synthetic value.
        proba = model.predict_proba(Xb)
        loss_val = float(log_loss(yb, proba, labels=classes))
        compute_latency_us = (time.perf_counter() - t_compute0) * 1e6

        t0 = time.perf_counter()
        try:
            store.log_metric(
                run_id,
                Metric(key="loss", value=loss_val, timestamp=int(time.time() * 1000), step=step),
            )
            latency_us = (time.perf_counter() - t0) * 1e6
            rec = {"ok": True, "latency_us": latency_us, "compute_latency_us": compute_latency_us,
                   "key": f"step{step}", "final_state": "present"}
        except Exception as e:  # noqa: BLE001 -- want every failure mode counted
            latency_us = (time.perf_counter() - t0) * 1e6
            rec = {"ok": False, "latency_us": latency_us, "compute_latency_us": compute_latency_us,
                   "error": repr(e)[:600], "key": f"step{step}"}
        with records_lock:
            records.append(rec)

    for stop_event in stop_events:
        stop_event.set()
    for t in extra_threads:
        t.join(timeout=5)

    out_path = os.path.join(config["out_dir"], f"trial_{config['trial_id']}.jsonl")
    with open(out_path, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")

    n_errors = sum(1 for r in records if not r["ok"])
    return {"n_ops": len(records), "n_errors": n_errors}
