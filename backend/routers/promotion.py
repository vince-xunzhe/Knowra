"""HTTP surface for the concept promotion lifecycle.

Three flavors of endpoint:

  POST /api/promotion/run    — trigger an evaluation pass (heuristic, then
                               optional LLM stage).
  GET  /api/promotion/...    — list candidates for the review UI.
  PATCH /api/promotion/{id}  — user override (rescue / demote / reset).

The user-override endpoint pins `promoted_by=user`, which makes future
auto-runs skip the node.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from database import get_db
from models import KnowledgeNode
from services import promotion_service
from services.graph_service import (
    PROMOTION_PENDING,
    PROMOTION_PROMOTED,
    PROMOTION_REJECTED,
    PROMOTION_STATUSES,
    is_concept_candidate_node,
    promotion_status,
)
from services import wiki_search as wiki_search_service
from services.promotion_llm import PROMOTION_LLM_DEFAULT_PROMPT
from services.wiki_compiler import reconcile_concept_pages_dir
from config import load_config, save_config


def _reconcile_curated_wiki(db: Session) -> None:
    """Drop now-orphaned concept .md files (e.g. promoted → rejected) and
    refresh the wiki search index. Mirrors the helper in routers.graph so
    every status mutation keeps disk + index in sync."""
    reconcile_concept_pages_dir(db, prune_orphans=True)
    try:
        wiki_search_service.rebuild_index()
    except Exception:
        pass

router = APIRouter(prefix="/api/promotion", tags=["promotion"])


class RunRequest(BaseModel):
    force_all: bool = False
    use_llm: bool = True  # honored once stage 3 is wired in


class StatusUpdate(BaseModel):
    status: str
    reason: Optional[str] = None


class BulkStatusUpdate(BaseModel):
    node_ids: list[int]
    status: str
    reason: Optional[str] = None


def _serialize_candidate(node: KnowledgeNode) -> dict:
    last_eval = getattr(node, "last_promotion_eval_at", None)
    return {
        "id": node.id,
        "title": node.title,
        "node_type": node.node_type,
        "tags": list(node.tags or []),
        "source_paper_ids": list(node.source_paper_ids or []),
        "promotion_status": promotion_status(node),
        "promoted_by": node.promoted_by,
        "promotion_reason": node.promotion_reason,
        "last_promotion_eval_at": last_eval.isoformat() if last_eval else None,
    }


@router.post("/run")
def run_promotion(
    body: RunRequest = RunRequest(),
    db: Session = Depends(get_db),
):
    heuristic = promotion_service.run_heuristic_pass(db, force_all=body.force_all)
    response = {
        "heuristic": {
            "promoted": heuristic.promoted,
            "rejected": heuristic.rejected,
            "deferred": heuristic.deferred,
            "total_evaluated": heuristic.total_evaluated,
            "skipped_user_pinned": heuristic.skipped_user_pinned,
        },
        "llm": None,
    }

    if body.use_llm:
        try:
            from services import promotion_llm
        except ImportError:
            promotion_llm = None
        if promotion_llm is not None:
            try:
                llm_result = promotion_llm.run_llm_pass(db)
            except promotion_llm.PromotionLLMUnavailable as exc:
                response["llm"] = {"error": str(exc)}
            else:
                response["llm"] = {
                    "promoted": llm_result.promoted,
                    "rejected": llm_result.rejected,
                    "still_ambiguous": llm_result.still_ambiguous,
                    "total_evaluated": llm_result.total_evaluated,
                    "model": llm_result.model,
                }

    # A run can flip nodes to rejected → their .md needs to go. Likewise
    # newly-promoted nodes will surface as "missing" in the freshness panel
    # so the user knows to recompile.
    _reconcile_curated_wiki(db)
    response["summary"] = promotion_service.promotion_summary(db)
    response["counts"] = response["summary"]["counts"]
    return response


@router.get("/candidates")
def list_candidates(
    status: Optional[str] = None,
    limit: int = 500,
    db: Session = Depends(get_db),
):
    if status is not None and status not in PROMOTION_STATUSES:
        raise HTTPException(status_code=400, detail=f"unknown status: {status}")
    nodes = promotion_service.list_candidates(db, status=status, limit=limit)
    return {
        "items": [_serialize_candidate(n) for n in nodes],
        "counts": promotion_service.status_counts(db),
    }


@router.patch("/{node_id}")
def update_status(
    node_id: int,
    body: StatusUpdate,
    db: Session = Depends(get_db),
):
    node = db.query(KnowledgeNode).filter(KnowledgeNode.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    if not is_concept_candidate_node(node):
        raise HTTPException(
            status_code=400,
            detail="此节点不是概念候选（paper / finding 不参与精选）",
        )
    if body.status == PROMOTION_PENDING:
        promotion_service.reset_status(node)
    elif body.status in {PROMOTION_PROMOTED, PROMOTION_REJECTED}:
        promotion_service.set_status_by_user(node, status=body.status, reason=body.reason)
    else:
        raise HTTPException(status_code=400, detail=f"unknown status: {body.status}")
    db.commit()
    db.refresh(node)
    _reconcile_curated_wiki(db)
    return {"node": _serialize_candidate(node)}


@router.get("/counts")
def get_counts(db: Session = Depends(get_db)):
    summary = promotion_service.promotion_summary(db)
    return {"counts": summary["counts"], "summary": summary}


@router.post("/accept_llm")
def accept_llm_proposals(db: Session = Depends(get_db)):
    """User: 'I trust the LLM, lock its decisions.'  Re-stamps every
    `promoted_by=llm` row as `promoted_by=user` so future auto-runs leave
    them alone."""
    locked = promotion_service.accept_llm_proposals(db)
    _reconcile_curated_wiki(db)
    summary = promotion_service.promotion_summary(db)
    return {
        "locked": locked,
        "counts": summary["counts"],
        "summary": summary,
    }


class PromotionPromptUpdate(BaseModel):
    prompt: str


@router.get("/prompt")
def get_promotion_prompt():
    """Returns the user's saved system prompt for the LLM stage plus the
    built-in default template the editor exposes as a starter."""
    cfg = load_config()
    return {
        "prompt": cfg.get("promotion_prompt") or "",
        "default_template": PROMOTION_LLM_DEFAULT_PROMPT,
    }


@router.put("/prompt")
def update_promotion_prompt(body: PromotionPromptUpdate):
    """Persist the user's edits. Empty string is allowed and means
    "skip the LLM stage; only run heuristic"."""
    save_config({"promotion_prompt": body.prompt or ""})
    return {"prompt": body.prompt or ""}


@router.post("/bulk")
def bulk_update(body: BulkStatusUpdate, db: Session = Depends(get_db)):
    """Bulk status flip — used by the rescue UI to recall a whole group of
    rejected nodes (or to demote a group at once)."""
    if body.status not in PROMOTION_STATUSES:
        raise HTTPException(status_code=400, detail=f"unknown status: {body.status}")
    changed = promotion_service.bulk_set_status(
        db,
        node_ids=body.node_ids,
        status=body.status,
        reason=body.reason,
    )
    _reconcile_curated_wiki(db)
    summary = promotion_service.promotion_summary(db)
    return {
        "changed": changed,
        "counts": summary["counts"],
        "summary": summary,
    }
