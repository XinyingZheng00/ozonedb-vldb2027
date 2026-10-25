"""Control-only experiment. This is not real MLflow or real Postgres
deployment behavior. It forces every commit on a given Postgres engine to
serialize via a transaction-scoped advisory lock (`pg_advisory_xact_lock`).
So no two commits can ever be in flight at the same time. This is the same
"batch size = 1" constraint that the single-writer lock of SQLite and the
own design of OzoneDB already impose. See the group-commit investigation
of Phase 2c: the real workload of OzoneDB measured avg_batch=1.00, that
is, it never naturally gets to batch either. We use this control to test
one question. Is the speed of Postgres under concurrency (the P-sweep of
Phase 2c) actually explained by WAL group commit? Or is it explained by
something else in its architecture, independent of batching? See
PHASE_REPORT.md for the result.

The control is a SQLAlchemy `before_commit` session event, not an
override of `_log_metrics`. So it runs against the real, unmodified
`_log_metrics` method that every non-serialized `postgres` and `sqlite`
arm also uses as-is. It is not a reimplementation of that method. This
avoids the risk of drift from the pinned method body. The file
`ozonedb_metric_store.py` has to track that drift (see the own docstring
of that file on the same problem).

The lock is transaction-scoped (`pg_advisory_xact_lock`, not
`pg_advisory_lock`). So it auto-releases at COMMIT or ROLLBACK. No
separate unlock call is needed. There is also no risk of an unlock from
a different pooled connection than the one that acquired the lock. That
risk is real with the session-scoped `pg_advisory_lock` and
`pg_advisory_unlock` pair. The connection pool of SQLAlchemy does not
guarantee the same underlying connection across two separate
`with self.ManagedSessionMaker(...)` blocks.

Scope, stated explicitly: the event is registered on `Session`, global to
the process, and filtered by `session.bind is engine`. `SqlAlchemyStore`
does not expose the underlying plain `sessionmaker` that it wraps into
`ManagedSessionMaker`. It only exposes the wrapped result. So a filter on
the bound engine is the most precise option available without a reach
into MLflow internals. This serializes *every* commit made against this
engine within this process, including for example the own commit of
`create_run`. It does not serialize only the `metrics`/`latest_metrics`
write. This is a real imprecision, and we state it up front. This is
"Postgres with no overlap allowed anywhere". That is a stricter control
than "only the metrics commit". So a result here is a conservative test
of the batching hypothesis, not a precise one. If anything, it is
unfavorable to Postgres.
"""
from sqlalchemy import event, text
from sqlalchemy.orm import Session

_SERIALIZE_LOCK_KEY = 999999137  # arbitrary, fixed for this control experiment


def enable_commit_serialization(engine):
    """Every Session bound to `engine` acquires a single, fixed advisory
    lock as the first statement of its transaction, before it is allowed
    to commit. So only one such transaction can ever be mid-commit at a
    time against this database. Returns the registered listener. Keep a
    reference if you ever need to `event.remove` it. A one-shot Ray trial
    process that exits when done does not need this."""

    def _before_commit(session):
        if session.bind is engine:
            session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _SERIALIZE_LOCK_KEY})

    event.listen(Session, "before_commit", _before_commit)
    return _before_commit
