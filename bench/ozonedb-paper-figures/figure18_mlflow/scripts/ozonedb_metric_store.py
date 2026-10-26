"""MLflow's SqlAlchemyStore with the metric history routed to OzoneDB.

Pinned to mlflow==3.15.1. Only `_log_metrics` (the method behind
`log_metric` and `log_batch`) is overridden; re-check it against
`SqlAlchemyStore._log_metrics` on any MLflow version bump. Everything else
(experiments, runs, params, tags, search) stays on the SQL backend.

Stock `_log_metrics` does two writes in one SQL transaction: it inserts the
history row into `metrics` and updates `latest_metrics` if the step is newer.
Here:

  1. The `metrics` history row is written to OzoneDB with one put() per
     metric, under the key `<run_id>:<key>:<step>:<timestamp>:<uuid>`.
  2. `latest_metrics` stays in SQL, through the stock
     `_update_latest_metrics_if_necessary`. It is a conditional update
     ("replace only if this step is newer"), which a get-then-put on
     OzoneDB can't do atomically. So every log_metric call still runs one
     SQL write transaction, the same one stock MLflow runs.

Differences from stock MLflow:

  - Logging the identical metric (same key, timestamp, step and value)
    twice in one run is deduplicated by stock MLflow; here each put gets a
    random uuid suffix, so both are kept.
  - `_update_latest_metrics_if_necessary` checks for a row and then inserts
    it without a lock. In stock MLflow the preceding `metrics` insert takes
    SQLite's write lock first, which serializes that check. Without it, two
    threads logging the same (run, key) at once can both insert and fail on
    `UNIQUE constraint failed: latest_metrics.key, latest_metrics.run_uuid`.
    The benchmark never does this: each thread of a trial logs its own keys.
    Add a per-(run, key) lock if this store is used where threads share keys.
  - Metrics with a `model_id` are unaffected: `log_metric` sends them to
    `_log_model_metrics` (the `logged_model_metrics` table), which is not
    overridden.
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
        # One OzoneDB handle per thread, as the JNI binding does (it keeps a
        # thread_local DB*).
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

        # Stock run validation and latest_metrics update, in SQL.
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
