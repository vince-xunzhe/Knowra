"""Recommendation feed — global arXiv search per system tag, on a Mon/Wed/Fri
schedule, stored in the cloud DB and pruned to 30 days. Cloud-mode only.

Tags are system-defined (REC_TAGS below). A user picks which to follow on the
desktop 推荐 page — that's a client-side display filter, so there's no per-user
table here. The scheduler is a daemon thread that wakes hourly and runs any tag
that's "due" (a Mon/Wed/Fri slot has passed since it last ran). That makes it
catch-up friendly: a missed slot or a machine restart is handled on the next
tick rather than lost.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

from services.arxiv_service import search_arxiv

log = logging.getLogger(__name__)

RETENTION_DAYS = 7
MAX_RESULTS_PER_TAG = 40
SEARCH_WEEKDAYS = {0, 2, 4}  # Mon / Wed / Fri
MIN_GAP_HOURS = 20           # don't re-search a tag more than ~once/day

# System-maintained tags: display name → arXiv search query. Edit here.
REC_TAGS: list[dict] = [
    {"name": "多模态", "query": "cat:cs.CV AND (multimodal OR \"vision language\" OR VLM)"},
    {"name": "生成式", "query": "cat:cs.LG AND (generative OR diffusion OR \"generative model\")"},
    {"name": "世界模型", "query": "(abs:\"world model\" OR abs:\"world models\") AND (cat:cs.LG OR cat:cs.AI OR cat:cs.RO)"},
    {"name": "计算机视觉", "query": "cat:cs.CV"},
    {"name": "计算机科学", "query": "cat:cs.AI"},
    {"name": "具身智能", "query": "cat:cs.RO"},
    {"name": "三维重建", "query": "cat:cs.CV AND (\"3D reconstruction\" OR NeRF OR \"gaussian splatting\" OR \"neural rendering\")"},
    {"name": "自动驾驶", "query": "(cat:cs.RO OR cat:cs.CV) AND (\"autonomous driving\" OR \"vision-language-action\" OR VLA)"},
    {"name": "智能体", "query": "cat:cs.AI AND (agent OR \"LLM agent\" OR \"language agent\")"},
]


def rec_tags() -> list[dict]:
    """The public tag list (name only — the query is internal)."""
    return [{"name": t["name"]} for t in REC_TAGS]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _is_due(now: datetime, last: Optional[datetime]) -> bool:
    if now.weekday() not in SEARCH_WEEKDAYS:
        return False
    if last is None:
        return True
    return (now - last) >= timedelta(hours=MIN_GAP_HOURS)


def _prune(db, now: datetime) -> int:
    from cloud_models import Recommendation

    cutoff = now - timedelta(days=RETENTION_DAYS)
    q = db.query(Recommendation).filter(Recommendation.created_at < cutoff)
    n = q.count()
    if n:
        q.delete(synchronize_session=False)
    return n


def run_search(db, *, force: bool = False) -> dict:
    """Search every due tag, upsert new results, prune >30d. ``force`` ignores
    the schedule (manual refresh). Returns a small summary."""
    from cloud_models import Recommendation, RecSearchState

    now = _utcnow()
    added = 0
    searched: list[str] = []
    for tag in REC_TAGS:
        name = tag["name"]
        state = db.query(RecSearchState).filter(RecSearchState.tag == name).first()
        last = _aware(state.last_searched_at) if state else None
        if not force and not _is_due(now, last):
            continue
        try:
            results = search_arxiv(tag["query"], max_results=MAX_RESULTS_PER_TAG, since=last)
        except Exception as e:  # noqa: BLE001
            log.warning("arxiv search failed for tag %s: %s", name, e)
            continue
        for r in results:
            exists = (
                db.query(Recommendation)
                .filter(Recommendation.tag == name, Recommendation.arxiv_id == r["arxiv_id"])
                .first()
            )
            if exists:
                continue
            db.add(Recommendation(
                tag=name,
                arxiv_id=r["arxiv_id"],
                title=(r["title"] or "")[:500],
                authors=r["authors"],
                abstract=r["abstract"],
                pdf_url=r["pdf_url"],
                primary_category=r["primary_category"],
                published=r["published"],
            ))
            added += 1
        if state is None:
            db.add(RecSearchState(tag=name, last_searched_at=now))
        else:
            state.last_searched_at = now
        searched.append(name)

    pruned = _prune(db, now)
    db.commit()
    return {"added": added, "pruned": pruned, "tags": searched}


# ── scheduler ──────────────────────────────────────────────────────────

_scheduler_started = False


def start_scheduler() -> None:
    """Launch the daemon scheduler thread (cloud-mode only, idempotent)."""
    global _scheduler_started
    if _scheduler_started:
        return
    _scheduler_started = True
    threading.Thread(target=_loop, name="rec-scheduler", daemon=True).start()
    log.info("recommendation scheduler started")


def _loop() -> None:
    import cloud_db

    time.sleep(30)  # let startup finish before the first (cold) arXiv call
    while True:
        db = None
        try:
            db = cloud_db.cloud_session()
            summary = run_search(db)
            if summary["added"] or summary["tags"]:
                log.info("rec scheduler: %s", summary)
        except Exception as e:  # noqa: BLE001
            log.warning("rec scheduler tick failed: %s", e)
        finally:
            if db is not None:
                try:
                    db.close()
                except Exception:
                    pass
        time.sleep(3600)  # hourly tick; _is_due gates the actual searches
