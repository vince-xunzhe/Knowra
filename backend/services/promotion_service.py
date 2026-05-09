"""Concept promotion lifecycle.

Auto-generated KnowledgeNodes start as `pending`. The promotion service
moves them through three exits:
  - `promoted` — appears in the curated graph, gets a wiki concept page.
  - `rejected` — kept in DB (with edges) but hidden from default views.
  - stays `pending` after a run — too ambiguous for heuristics, queued for
    the LLM stage.

Two evaluators run in sequence:
  1. heuristic   (this module — fast, free, decides obvious cases)
  2. LLM         (services.promotion_llm — judges whatever heuristics left
                  ambiguous)

Both honor `promoted_by="user"`: once a human has decided, future runs
leave the node alone.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Iterable, Optional

from sqlalchemy.orm import Session

from models import KnowledgeNode
from services.graph_service import (
    AUTO_CONCEPT_NODE_TYPES,
    PROMOTED_BY_HEURISTIC,
    PROMOTED_BY_USER,
    PROMOTION_PENDING,
    PROMOTION_PROMOTED,
    PROMOTION_REJECTED,
    PROMOTION_STATUSES,
    is_concept_candidate_node,
    node_is_hidden,
    node_origin,
    normalize_source_paper_ids,
    promotion_status,
)

# Anything older than this gets re-evaluated even without a source-paper
# change, so a node can't get permanently stuck if our heuristics shift.
RE_EVAL_AFTER = timedelta(days=30)

# Heuristic thresholds. These are deliberately conservative: heuristics
# should only fire when the answer is obvious. Borderline cases stay
# pending and fall through to the LLM stage.
HEURISTIC_PROMOTE_MIN_PAPERS = 3
HEURISTIC_REJECT_MAX_TITLE_LEN = 2  # "AI" stays, "X" goes


@dataclass
class PromotionDecision:
    status: str
    by: str
    reason: str


@dataclass
class HeuristicRunResult:
    promoted: int
    rejected: int
    deferred: int           # left as pending — for LLM stage
    skipped_user_pinned: int  # untouched because promoted_by=user
    total_evaluated: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _to_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """SQLite stores DATETIME as naive strings; SQLAlchemy returns them as
    naive Python datetimes. Comparing naive vs aware raises TypeError, so
    every read of `last_promotion_eval_at` goes through this normalizer."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def select_for_eval(
    db: Session,
    *,
    force_all: bool = False,
    cutoff: Optional[datetime] = None,
) -> list[KnowledgeNode]:
    """Return the candidate nodes that this run should re-evaluate.

    Granularity rules (per design doc decision #1):
      - never-evaluated nodes are always in
      - last-evaluated more than RE_EVAL_AFTER ago: in
      - force_all=True: everything pending, regardless of last-eval
      - user-pinned (promoted_by=user) is always excluded
      - non-concept-eligible types are always excluded
    """
    cutoff = cutoff or (_now() - RE_EVAL_AFTER)
    nodes = db.query(KnowledgeNode).all()
    out: list[KnowledgeNode] = []
    for node in nodes:
        if not is_concept_candidate_node(node):
            continue
        if (node.promoted_by or "").lower() == PROMOTED_BY_USER:
            continue
        if promotion_status(node) != PROMOTION_PENDING and not force_all:
            # Once a node has reached promoted/rejected, only force_all or
            # explicit user action can re-open it.
            continue
        last_eval = _to_aware(getattr(node, "last_promotion_eval_at", None))
        if force_all or last_eval is None or last_eval < cutoff:
            out.append(node)
    return out


def _heuristic_decide(node: KnowledgeNode) -> Optional[PromotionDecision]:
    """Apply the cheap rules. Returns None when the node is ambiguous and
    should fall through to the LLM stage."""
    title = (node.title or "").strip()
    source_ids = set(normalize_source_paper_ids(node.source_paper_ids))

    if not title:
        return PromotionDecision(
            PROMOTION_REJECTED,
            PROMOTED_BY_HEURISTIC,
            "empty title",
        )
    if len(title) <= HEURISTIC_REJECT_MAX_TITLE_LEN:
        return PromotionDecision(
            PROMOTION_REJECTED,
            PROMOTED_BY_HEURISTIC,
            f"title too short ({len(title)} chars)",
        )
    if title.replace(".", "").replace("-", "").isdigit():
        return PromotionDecision(
            PROMOTION_REJECTED,
            PROMOTED_BY_HEURISTIC,
            "title is purely numeric",
        )
    if not source_ids:
        return PromotionDecision(
            PROMOTION_REJECTED,
            PROMOTED_BY_HEURISTIC,
            "no source papers",
        )

    if len(source_ids) >= HEURISTIC_PROMOTE_MIN_PAPERS and len(title) >= 3:
        return PromotionDecision(
            PROMOTION_PROMOTED,
            PROMOTED_BY_HEURISTIC,
            f"cited by {len(source_ids)} papers",
        )

    # Ambiguous: 1–2 source papers, non-trivial title. Let the LLM decide.
    return None


def apply_decision(
    node: KnowledgeNode,
    decision: PromotionDecision,
    *,
    when: Optional[datetime] = None,
) -> None:
    node.promotion_status = decision.status
    node.promoted_by = decision.by
    node.promotion_reason = decision.reason
    node.last_promotion_eval_at = when or _now()


def mark_evaluated(
    node: KnowledgeNode,
    *,
    when: Optional[datetime] = None,
) -> None:
    """Stamp the eval timestamp without changing the status — used when a
    heuristic decides 'ambiguous, defer'. Without this stamp the node would
    keep being picked up by every subsequent run."""
    node.last_promotion_eval_at = when or _now()


def run_heuristic_pass(
    db: Session,
    *,
    force_all: bool = False,
) -> HeuristicRunResult:
    """Walk the eval queue and apply heuristic decisions.

    Returns counts; does NOT call out to LLM. Ambiguous nodes are left as
    pending but with `last_promotion_eval_at` stamped so the LLM stage
    knows what's queued."""
    promoted = rejected = deferred = 0
    skipped_user_pinned = 0
    candidates = select_for_eval(db, force_all=force_all)
    now = _now()

    # Surface user-pinned count for telemetry even though we skipped them.
    for node in db.query(KnowledgeNode).all():
        if (node.promoted_by or "").lower() == PROMOTED_BY_USER:
            skipped_user_pinned += 1

    for node in candidates:
        decision = _heuristic_decide(node)
        if decision is None:
            mark_evaluated(node, when=now)
            deferred += 1
            continue
        apply_decision(node, decision, when=now)
        if decision.status == PROMOTION_PROMOTED:
            promoted += 1
        elif decision.status == PROMOTION_REJECTED:
            rejected += 1

    db.commit()
    return HeuristicRunResult(
        promoted=promoted,
        rejected=rejected,
        deferred=deferred,
        skipped_user_pinned=skipped_user_pinned,
        total_evaluated=len(candidates),
    )


def list_candidates(
    db: Session,
    *,
    status: Optional[str] = None,
    limit: int = 500,
) -> list[KnowledgeNode]:
    """List concept-eligible nodes for the review UI. `status=None` returns
    all three buckets."""
    q = db.query(KnowledgeNode).filter(
        KnowledgeNode.node_type.in_(list(AUTO_CONCEPT_NODE_TYPES))
    )
    if status:
        if status not in PROMOTION_STATUSES:
            raise ValueError(f"unknown status: {status}")
        q = q.filter(KnowledgeNode.promotion_status == status)
    q = q.order_by(KnowledgeNode.id.desc())
    return q.limit(limit).all()


def set_status_by_user(
    node: KnowledgeNode,
    *,
    status: str,
    reason: Optional[str] = None,
) -> None:
    """User override: pin the status with `promoted_by=user` so subsequent
    auto-runs leave it alone."""
    if status not in PROMOTION_STATUSES:
        raise ValueError(f"unknown status: {status}")
    node.promotion_status = status
    node.promoted_by = PROMOTED_BY_USER
    node.promotion_reason = reason
    node.last_promotion_eval_at = _now()


def reset_status(node: KnowledgeNode) -> None:
    """Drop a user pin and put the node back in the eval queue."""
    node.promotion_status = PROMOTION_PENDING
    node.promoted_by = None
    node.promotion_reason = None
    node.last_promotion_eval_at = None


def status_counts(db: Session) -> dict[str, int]:
    counts = {s: 0 for s in PROMOTION_STATUSES}
    nodes = db.query(KnowledgeNode).filter(
        KnowledgeNode.node_type.in_(list(AUTO_CONCEPT_NODE_TYPES))
    ).all()
    for node in nodes:
        counts[promotion_status(node)] = counts.get(promotion_status(node), 0) + 1
    return counts


def promotion_summary(db: Session) -> dict:
    """Wider counts payload for the candidate panel: per-status totals, the
    promoted_by breakdown (so the UI can show "human N / agent M"), and the
    most recent eval timestamp so the user can see how stale the current
    state is."""
    nodes = db.query(KnowledgeNode).filter(
        KnowledgeNode.node_type.in_(list(AUTO_CONCEPT_NODE_TYPES))
    ).all()
    counts = {s: 0 for s in PROMOTION_STATUSES}
    by: dict[str, int] = {"user": 0, "llm": 0, "heuristic": 0, "legacy": 0, "unset": 0}
    last_eval: Optional[datetime] = None
    decided = 0  # nodes that have left `pending` (i.e. promoted or rejected)
    for node in nodes:
        status = promotion_status(node)
        counts[status] = counts.get(status, 0) + 1
        if status != PROMOTION_PENDING:
            decided += 1
        key = (node.promoted_by or "").lower() or "unset"
        by[key] = by.get(key, 0) + 1
        ts = _to_aware(getattr(node, "last_promotion_eval_at", None))
        if ts is not None and (last_eval is None or ts > last_eval):
            last_eval = ts
    return {
        "counts": counts,
        "by": by,
        "last_eval_at": last_eval.isoformat() if last_eval else None,
        "total_candidates": len(nodes),
        "decided": decided,
    }


def accept_llm_proposals(db: Session) -> int:
    """Lock every node currently tagged `promoted_by=llm` by re-stamping
    `promoted_by=user`. Once locked the node is invisible to future
    automated runs (heuristic / LLM both skip user-pinned nodes), so this
    is the "I trust the LLM, leave it alone" button."""
    rows = (
        db.query(KnowledgeNode)
        .filter(KnowledgeNode.promoted_by == "llm")
        .all()
    )
    for node in rows:
        node.promoted_by = "user"
    db.commit()
    return len(rows)


def bulk_set_status(
    db: Session,
    *,
    node_ids: list[int],
    status: str,
    reason: Optional[str] = None,
) -> int:
    """Apply the same user override to many nodes (rescue group / bulk
    reject). Returns count actually changed."""
    if status not in PROMOTION_STATUSES:
        raise ValueError(f"unknown status: {status}")
    if not node_ids:
        return 0
    rows = (
        db.query(KnowledgeNode)
        .filter(KnowledgeNode.id.in_(node_ids))
        .all()
    )
    now = _now()
    for node in rows:
        if not is_concept_candidate_node(node):
            continue
        if status == PROMOTION_PENDING:
            reset_status(node)
        else:
            node.promotion_status = status
            node.promoted_by = "user"
            node.promotion_reason = reason
            node.last_promotion_eval_at = now
    db.commit()
    return len(rows)
