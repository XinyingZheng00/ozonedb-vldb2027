"""Control-only experiment, not real MLflow or real Postgres deployment
behavior: forces every commit on a given Postgres engine to serialize via
a transaction-scoped advisory lock (`pg_advisory_xact_lock`), so no two
commits can ever be in flight at the same time -- the same "batch size = 1"
constraint SQLite's single-writer lock and OzoneDB's own design already
impose (see Phase 2c's group-commit investigation: OzoneDB's real workload
measured avg_batch=1.00, i.e. it never naturally gets to batch either).
Used to test whether Postgres's speed under concurrency (Phase 2c's
P-sweep) is actually explained by WAL group commit, or by something else
in its architecture independent of batching -- see PHASE_REPORT.md for
the result.

Implemented as a SQLAlchemy `before_commit` session event rather than an
override of `_log_metrics` -- this runs against the real, unmodified
`_log_metrics` method every non-serialized `postgres`/`sqlite` arm also
uses as-is, not a reimplementation of it, avoiding the risk of drifting
from the pinned method body the way `ozonedb_metric_store.py` has to
track (see that file's own docstring on the same problem).

The lock is transaction-scoped (`pg_advisory_xact_lock`, not
`pg_advisory_lock`) so it auto-releases at COMMIT or ROLLBACK -- no
separate unlock call needed, and no risk of trying to unlock from a
different pooled connection than the one that acquired it (a real risk
with the session-scoped `pg_advisory_lock`/`pg_advisory_unlock` pair,
since SQLAlchemy's connection pool doesn't guarantee the same underlying
connection across two separate `with self.ManagedSessionMaker(...)`
blocks).

Scope, stated explicitly: the event is registered on `Session` (global to
the process, filtered by `session.bind is engine`) because
`SqlAlchemyStore` doesn't expose the underlying plain `sessionmaker` it
wraps into `ManagedSessionMaker`, only the wrapped result -- filtering by
bound engine is the surgical option available without reaching into
MLflow internals. This serializes *every* commit made against this
engine within this process (including e.g. `create_run`'s own commit),
not narrowly just the `metrics`/`latest_metrics` write -- a real
imprecision worth being upfront about: this is "Postgres with essentially
no overlap allowed anywhere," a stricter control than "just the metrics
commit," so a result here is a conservative (if anything, unfavorable-to-
Postgres) test of the batching hypothesis, not a scalpel-precise one.
"""
from sqlalchemy import event, text
from sqlalchemy.orm import Session

_SERIALIZE_LOCK_KEY = 999999137  # arbitrary, fixed for this control experiment


def enable_commit_serialization(engine):
    """Every Session bound to `engine` acquires a single, fixed advisory
    lock as the first statement of its transaction before it's allowed to
    commit -- so only one such transaction can ever be mid-commit at a
    time against this database. Returns the registered listener (keep a
    reference if you ever need to `event.remove` it; not needed for a
    one-shot Ray trial process that exits when done)."""

    def _before_commit(session):
        if session.bind is engine:
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SERIALIZE_LOCK_KEY})

    event.listen(Session, "before_commit", _before_commit)
    return _before_commit
