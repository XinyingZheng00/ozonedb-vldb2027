"""Real mlflow.store.tracking.SqlAlchemyStore with the metrics-history
insert routed through OzoneDB instead of the `metrics` SQL table.
Everything else (run/experiment management, params, tags,
`latest_metrics`, search) stays on the real SQL path unchanged.

This is the Phase-1-grade swap-in bridge for MLflow: real MLflow code
(not the synthetic kv_writer harness Phase 2 used), only the exact
operation issue #6013 and MLflow's own regression test
(test_log_metric_concurrent_logging_succeeds) exercise gets bridged --
same descoping precedent as the Rhizome and Podman phases (bridge only
what the target workload touches, leave the rest on the real backend).

Pinned to mlflow==3.15.1's actual `_log_metrics` implementation
(installed via pip, not the unreleased GitHub main branch -- an earlier
pass at this file targeted a since-superseded `_log_metrics_once` method
that main has and 3.15.1 does not; 3.15.1's `_log_metrics` does the same
work without a deadlock-retry wrapper). Re-verify this override still
matches the target method's body on any mlflow version bump.

Scope decisions, stated explicitly rather than left as silent behavior
drift from real MLflow:

  1. `latest_metrics` (an upsert keyed by (run_uuid, key) -- "replace if
     this value's step is newer") is bridged to real SQL, via the real
     `_update_latest_metrics_if_necessary`, same as unmodified MLflow --
     NOT skipped. This matters: real `_log_metrics` does the `metrics`
     insert and the `latest_metrics` upsert in one transaction, so
     `latest_metrics`'s lock-hold time is already part of every real
     `log_metric` call's cost on sqlite/postgres. An earlier version of
     this file intended to route `latest_metrics` through SQL but never
     actually called this method, silently skipping it -- meaning the
     `ozonedb` arm was doing strictly less work than `sqlite`/`postgres`
     per call, a real confound in results measured before this fix.
     `latest_metrics` itself is a compare-and-swap, not a blind put, and
     OzoneKV's put/get/remove can't express CAS -- which is exactly why
     it stays on real SQL here rather than also being routed to OzoneDB;
     a naive get-then-put would introduce a lost-update race under
     concurrent writers to the same run+key that the real
     `UPDATE...WHERE` doesn't have. That's a real gap between "single-key
     enough" and "single-key CAS," worth carrying into the report as a
     limit on what's actually bridged, not something to paper over.

     Confirmed by direct measurement, not just theory: routing `metrics`
     to OzoneDB removes real MLflow's *own* `session.add_all(metric_instances)`
     write, which happens before `_update_latest_metrics_if_necessary` in
     real `_log_metrics` and (via SQLAlchemy's autoflush) incidentally
     triggers SQLite's write-lock early enough to serialize
     `_update_latest_metrics_if_necessary`'s own unprotected
     check-then-insert. Without that accidental protection, concurrent
     threads can both pass the "no row yet" check and both attempt
     `INSERT`, hitting `UNIQUE constraint failed: latest_metrics.key,
     latest_metrics.run_uuid` -- reproduced in isolation at ~0.1-0.16%
     over 2500 concurrent attempts (5 threads x 500 calls) under the
     P-processes/T=5-threads-per-run benchmark design this file was
     originally built for. That design is now superseded (see
     PHASE_REPORT.md Phase 2) by one Ray Tune trial = one process = one
     run = one thread, so the race is now structurally impossible: no two
     writers ever touch the same `(run_uuid, key)`, since every run has
     exactly one writer for its whole lifetime. A per-process
     `threading.Lock` (`_latest_metrics_lock`) was added as the fix at the
     time and has since been **removed**, since it's structurally
     unnecessary under the current design and an uncontended lock (one
     that only ever has a single thread acquiring it, never waiting) is
     expected to be cheap -- re-measured at P=320 after removal to check
     that expectation against real numbers rather than assume it (see
     PHASE_REPORT.md Phase 2 for the result). **This is a
     latent race in MLflow's *own* `_update_latest_metrics_if_necessary`,
     not something OzoneDB introduces**, and it will resurface if this
     class is ever reused in a context where multiple threads genuinely
     share one run again -- re-add per-run-scoped locking around
     `_update_latest_metrics_if_necessary` if that happens, don't assume
     it's still safe by default.
  2. Real `_log_metrics` also has a composite-PK dedup path: identical
     (key, timestamp, step, value, is_nan) logged twice within one run is
     deduped via an IntegrityError-triggered retry, not double-inserted.
     Each metric bridged to OzoneDB instead gets a uuid-suffixed key,
     sidestepping that dedup rather than replicating it. Collisions are
     vanishingly unlikely with synthetic or HPO-B values (Phase 2's
     actual workload), but this is a known simplification, not something
     this benchmark is trying to prove either way.
  3. `metric.model_id`-tagged metrics (routed by real MLflow through
     `_log_model_metrics`, a separate table) are out of scope -- the
     benchmark's workload (P processes x T threads x plain log_metric
     calls, matching issue #6013 exactly) never sets model_id.
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
        # One OzoneDB handle per thread, matching the T convention
        # documented in PHASE_REPORT.md Phase 0 -- there is no
        # cross-handle locking inside OzoneDB to rely on instead.
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

        # Real run-validation + real latest_metrics upsert, same call real
        # unmodified _log_metrics makes -- only the `metrics` history rows
        # themselves are diverted to OzoneDB instead of session.add_all().
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
