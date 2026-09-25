"""End read-only transactions before slow work on loaded ORM snapshots."""
from sqlalchemy import inspect
from sqlalchemy.orm import Session


def finish_read_snapshot(db: Session) -> None:
    """Detach loaded rows and return the connection without expiring their data.

    Only for read-only phases: callers must re-query before later DB writes.
    Refuse pending changes rather than silently discarding or committing them.
    """
    if db.new or db.dirty or db.deleted:
        raise RuntimeError("Cannot release a read snapshot with pending database changes")
    for row in list(db.identity_map.values()):
        state = inspect(row)
        # A previous commit may have expired columns; load them before detaching.
        if state.expired_attributes:
            db.refresh(row)
    db.expunge_all()
    db.close()
