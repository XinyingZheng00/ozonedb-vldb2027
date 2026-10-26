"""Control run, not part of the paper figure (enabled by
run_ray_swapin.py --serialize-commits; results are labeled
'postgres_serialized'). It forces every commit on a Postgres engine to run
one at a time, using a transaction-scoped advisory lock
(`pg_advisory_xact_lock`), so Postgres can't batch commits (WAL group
commit). SQLite's single-writer lock already rules out batching. This tests
whether group commit explains Postgres's speed under concurrency.

It is a SQLAlchemy `before_commit` session event, not an override of
`_log_metrics`, so the stock MLflow code path runs unchanged.

The lock is transaction-scoped, so it is released at COMMIT or ROLLBACK
with no unlock call. A session-scoped `pg_advisory_lock`/`pg_advisory_unlock`
pair could unlock from a different pooled connection than the one that
locked.

The event is registered on `Session` and filtered by `session.bind is
engine`, because SqlAlchemyStore doesn't expose its plain sessionmaker. So
it serializes every commit on that engine in the process (including
create_run's), not only the metric writes: a stricter control than needed.
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
