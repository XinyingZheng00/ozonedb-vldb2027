"""Real mlflow.store.tracking.SqlAlchemyStore with one change: the
metrics-history insert goes through OzoneDB instead of the `metrics` SQL
table. Everything else stays on the real SQL path unchanged: run and
experiment management, params, tags, `latest_metrics`, and search.

This is the Phase-1-grade swap-in bridge for MLflow. It uses real MLflow
code, not the synthetic kv_writer harness that Phase 2 used. Only the
exact operation that issue #6013 and MLflow's own regression test
(test_log_metric_concurrent_logging_succeeds) exercise gets bridged.
This follows the same descoping precedent as the Rhizome and Podman
phases. Bridge only what the target workload touches. Leave the rest on
the real backend.

The override is pinned to the actual `_log_metrics` implementation of
mlflow==3.15.1 (installed via pip, not the unreleased GitHub main
branch). An earlier pass at this file targeted a `_log_metrics_once`
method that main has and 3.15.1 does not. That method is now superseded.
The `_log_metrics` of 3.15.1 does the same work without a deadlock-retry
wrapper. On any mlflow version bump, re-verify that this override still
matches the body of the target method.

Scope decisions. We state them explicitly here, so they do not become
silent behavior drift from real MLflow:

  1. `latest_metrics` is an upsert keyed by (run_uuid, key): "replace if
     the step of this value is newer". It is bridged to real SQL via the
     real `_update_latest_metrics_if_necessary`, the same as unmodified
     MLflow. It is NOT skipped. This matters. Real `_log_metrics` does
     the `metrics` insert and the `latest_metrics` upsert in one
     transaction. So the lock-hold time of `latest_metrics` is already
     part of the cost of every real `log_metric` call on sqlite and
     postgres. An earlier version of this file intended to route
     `latest_metrics` through SQL, but it never called this method. It
     skipped the upsert silently. So the `ozonedb` arm did strictly less
     work than `sqlite` and `postgres` per call. This was a real confound
     in results measured before this fix.
     `latest_metrics` itself is a compare-and-swap, not a blind put. The
     put/get/remove of OzoneKV cannot express CAS. This is exactly why it
     stays on real SQL here, and is not also routed to OzoneDB. A naive
     get-then-put would introduce a lost-update race under concurrent
     writers to the same run+key. The real `UPDATE...WHERE` does not have
     this race. That is a real gap between "single-key enough" and
     "single-key CAS". Carry it into the report as a limit on what is
     actually bridged. Do not hide it.

     Direct measurement confirmed this, not only theory. When `metrics`
     goes to OzoneDB, the *own* `session.add_all(metric_instances)` write
     of real MLflow is removed. In real `_log_metrics` that write happens
     before `_update_latest_metrics_if_necessary`. Through the autoflush
     of SQLAlchemy, it incidentally takes the write-lock of SQLite early
     enough to serialize the unprotected check-then-insert of
     `_update_latest_metrics_if_necessary`. Without that accidental
     protection, two concurrent threads can both pass the "no row yet"
     check and both attempt `INSERT`. They then hit `UNIQUE constraint
     failed: latest_metrics.key, latest_metrics.run_uuid`. We reproduced
     this in isolation at about 0.1-0.16% over 2500 concurrent attempts
     (5 threads x 500 calls). That was under the P-processes/T=5-threads-
     per-run benchmark design this file was originally built for. That
     design is now superseded. Now one Ray
     Tune trial = one process = one run = one thread. So the race is now
     structurally impossible. No two writers ever touch the same
     `(run_uuid, key)`, because every run has exactly one writer for its
     whole lifetime. At the time, we added a per-process `threading.Lock`
     (`_latest_metrics_lock`) as the fix. We have since **removed** it.
     It is structurally unnecessary under the current design. An
     uncontended lock (one that only a single thread ever acquires, and
     never waits on) is expected to be cheap. We re-measured at P=320
     after the removal, to check that expectation against real numbers
     rather than assume it.
     **This is a latent race in the *own*
     `_update_latest_metrics_if_necessary` of MLflow, not something
     OzoneDB introduces**. It will resurface if this class is ever reused
     where multiple threads genuinely share one run again. If that
     happens, re-add per-run-scoped locking around
     `_update_latest_metrics_if_necessary`. Do not assume it is still
     safe by default.
  2. Real `_log_metrics` also has a composite-PK dedup path. If the
     identical (key, timestamp, step, value, is_nan) is logged twice
     within one run, an IntegrityError-triggered retry dedups it. It is
     not double-inserted. Each metric bridged to OzoneDB instead gets a
     uuid-suffixed key. This sidesteps that dedup rather than replicate
     it. Collisions are vanishingly unlikely with synthetic or HPO-B
     values (the actual workload of Phase 2). But this is a known
     simplification. This benchmark does not try to prove it either way.
  3. Metrics tagged with `metric.model_id` are out of scope. Real MLflow
     routes them through `_log_model_metrics`, a separate table. The
     workload of the benchmark (P processes x T threads x plain
     log_metric calls, matching issue #6013 exactly) never sets model_id.
"""
import json
import threading
import uuid

from mlflow.store.tracking.dbmodels.models import SqlMetric
from mlflow.store.tracking.sqlalchemy_store import SqlAlchemyStore
from mlflow.utils.validation import _validate_metric

from ozonedb_py import OzoneDB


class OzoneDBMetricStore(SqlAlchemyStore):
    def __init__(self, db_uri, default_artifact_root, ozonedb_config_path):
        super().__init__(db_uri, default_artifact_root)
        self._ozonedb_config_path = ozonedb_config_path
        self._local = threading.local()

    def _ozonedb(self):
        # One OzoneDB handle per thread. There is no cross-handle
        # locking inside OzoneDB to rely on instead.
        db = getattr(self._local, "db", None)
        if db is None:
            db = OzoneDB(self._ozonedb_config_path)
            self._local.db = db
        return db

    def _log_metrics(self, run_id, metrics):
        db = self._ozonedb()
        is_single_metric = len(metrics) == 1
        metric_instances = []
        for idx, metric in enumerate(metrics):
            _validate_metric(
                metric.key,
                metric.value,
                metric.timestamp,
                metric.step,
                path="" if is_single_metric else f"metrics[{idx}]",
            )
            is_nan, value = self.sanitize_metric_value(metric.value)
            metric_instances.append(
                SqlMetric(
                    run_uuid=run_id, key=metric.key, value=value,
                    timestamp=metric.timestamp, step=metric.step, is_nan=is_nan,
                )
            )

        # Real run validation and real latest_metrics upsert, the same call
        # that real unmodified _log_metrics makes. Only the `metrics` history
        # rows themselves go to OzoneDB instead of session.add_all().
        with self.ManagedSessionMaker(read_only=False) as session:
            run = self._get_run(run_uuid=run_id, session=session)
            self._check_run_is_active(run)
            self._update_latest_metrics_if_necessary(metric_instances, session)
            session.commit()

        for metric_row in metric_instances:
            row = {
                "run_uuid": metric_row.run_uuid,
                "key": metric_row.key,
                "value": metric_row.value,
                "timestamp": metric_row.timestamp,
                "step": metric_row.step,
                "is_nan": metric_row.is_nan,
            }
            key = (
                f"{run_id}:{metric_row.key}:{metric_row.step}:{metric_row.timestamp}:"
                f"{uuid.uuid4().hex}"
            )
            db.put(key.encode(), json.dumps(row).encode())
