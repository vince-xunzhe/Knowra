"""Explicit local recommendation-cache cleanup; never opens the paper library."""

import json
from datetime import timedelta
from pathlib import Path

from cloud_models import RecBatch, RecCandidate, RecEvent
from sqlalchemy import or_
from sqlalchemy.exc import OperationalError

from services.local_recommendations import LOCAL_USER
from services.personal_recommendation import utcnow
from services.recommendation_jobs import HISTORY_DAYS


def _queries(db):
    cutoff = utcnow() - timedelta(days=HISTORY_DAYS)
    expired = db.query(RecBatch).filter(
        RecBatch.user_id == LOCAL_USER,
        RecBatch.status.in_(["completed", "failed"]),
        or_(
            RecBatch.completed_at < cutoff,
            (RecBatch.completed_at.is_(None)) & (RecBatch.created_at < cutoff),
        ),
    )
    # Late imports and positive-feedback reconciliation still refer to these batches.
    linked = db.query(RecEvent.batch_id)
    removable = expired.filter(~RecBatch.id.in_(linked))
    candidates = db.query(RecCandidate).filter(RecCandidate.updated_at < cutoff)
    return expired, removable, candidates


def _file_bytes(engine):
    path = Path(engine.url.database)
    return sum(p.stat().st_size for p in (path, Path(str(path) + "-wal")) if p.exists())


def preview(store):
    with store.session() as db:
        expired, removable, candidates = _queries(db)
        batch_rows, candidate_rows = removable.all(), candidates.all()
        payload_bytes = sum(
            len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
            for payload in [
                *[{"items": b.items, "snapshot": b.snapshot} for b in batch_rows],
                *[
                    {"metadata": c.metadata_json, "features": c.features}
                    for c in candidate_rows
                ],
            ]
        )
        return {
            "retention_days": HISTORY_DAYS,
            "expired_batches": len(batch_rows),
            "expired_candidates": len(candidate_rows),
            "protected_batches": expired.count() - len(batch_rows),
            "estimated_payload_bytes": payload_bytes,
            "database_bytes": _file_bytes(store.engine),
        }


def cleanup(store):
    before = _file_bytes(store.engine)
    with store.session() as db:
        # Take the writer lock before checking references, preventing a feedback race.
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        _, batches, candidates = _queries(db)
        deleted_batches = batches.delete(synchronize_session=False)
        deleted_candidates = candidates.delete(synchronize_session=False)
        db.commit()
    compacted = True
    try:
        with store.engine.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as conn:
            conn.exec_driver_sql("VACUUM")
            checkpoint = conn.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)").one()
            compacted = checkpoint[0] == 0
    except OperationalError:
        # Deletion has committed; occupied pages are reusable even if compaction is busy.
        compacted = False
    return {
        "deleted_batches": deleted_batches,
        "deleted_candidates": deleted_candidates,
        "compacted": compacted,
        "reclaimed_bytes": max(0, before - _file_bytes(store.engine)),
    }
