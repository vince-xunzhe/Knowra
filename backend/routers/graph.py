from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from database import get_db
from models import Paper, KnowledgeNode, KnowledgeEdge
from config import load_config
from services import wiki_search as wiki_search_service
from services import promotion_service
from services.graph_service import (
    AUTO_NODE_ORIGIN,
    MANUAL_NODE_ORIGIN,
    PROMOTED_BY_USER,
    PROMOTION_PROMOTED,
    PROMOTION_REJECTED,
    _add_similarity_edges,
    find_existing_concept_node,
    get_graph_data,
    get_hidden_graph_nodes,
    get_node_detail_data,
    is_concept_candidate_node,
    is_publishable_concept_node,
    node_is_hidden,
    normalize_source_paper_ids,
    promotion_status,
)
from services.paper_record_service import sync_record_from_paper
from services.wiki_compiler import reconcile_concept_pages_dir

router = APIRouter(prefix="/api", tags=["graph"])


class ManualConceptInput(BaseModel):
    title: str
    content: str = ""
    paper_ids: list[int] = []
    tags: list[str] = []


def _normalize_tags(tags: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in tags or []:
        tag = (raw or "").strip()
        key = tag.lower()
        if not tag or key in seen:
            continue
        seen.add(key)
        out.append(tag)
    return out


def _validate_paper_ids(db: Session, paper_ids: list[int]) -> list[int]:
    ids = []
    seen = set()
    for pid in normalize_source_paper_ids(paper_ids):
        if pid in seen:
            continue
        seen.add(pid)
        ids.append(pid)

    if not ids:
        return []

    existing = {
        paper.id for paper in db.query(Paper).filter(Paper.id.in_(ids)).all()
    }
    missing = [pid for pid in ids if pid not in existing]
    if missing:
        raise HTTPException(status_code=400, detail=f"未知论文 ID: {missing}")
    return ids


def _reconcile_curated_wiki(db: Session) -> None:
    reconcile_concept_pages_dir(db, prune_orphans=True)
    try:
        wiki_search_service.rebuild_index()
    except Exception:
        pass


def _merge_unique_ints(existing: list[int], incoming: list[int]) -> tuple[list[int], int]:
    out = list(existing or [])
    seen = set(out)
    added = 0
    for item in incoming or []:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
        added += 1
    return out, added


def _should_replace_content(existing_content: str, existing_title: str, new_content: str) -> bool:
    current = (existing_content or "").strip()
    title = (existing_title or "").strip()
    incoming = (new_content or "").strip()
    if not incoming or incoming == title:
        return False
    return not current or current == title


def _adopt_existing_manual_identity(
    node: KnowledgeNode,
    *,
    incoming_content: str,
    incoming_tags: list[str],
    incoming_paper_ids: list[int],
) -> dict:
    was_manual = (node.node_origin or AUTO_NODE_ORIGIN) == MANUAL_NODE_ORIGIN
    if not was_manual:
        node.node_origin = MANUAL_NODE_ORIGIN
    node.hidden = False
    node.promotion_status = PROMOTION_PROMOTED
    node.promoted_by = PROMOTED_BY_USER

    # Keep tag order stable: old tags first, then new tags.
    current_tags = _normalize_tags(list(node.tags or []))
    next_tags = current_tags[:]
    seen_tags = {tag.lower() for tag in current_tags}
    added_tags = 0
    for tag in incoming_tags:
        key = tag.lower()
        if key in seen_tags:
            continue
        seen_tags.add(key)
        next_tags.append(tag)
        added_tags += 1
    node.tags = next_tags

    merged_papers, added_papers = _merge_unique_ints(
        normalize_source_paper_ids(node.source_paper_ids),
        incoming_paper_ids,
    )
    node.source_paper_ids = merged_papers

    content_applied = False
    if _should_replace_content(node.content or "", node.title or "", incoming_content):
        node.content = incoming_content.strip()
        content_applied = True
    elif not (node.content or "").strip():
        node.content = (node.title or "").strip()

    return {
        "adopted_existing": not was_manual,
        "merged_tags": added_tags,
        "merged_papers": added_papers,
        "content_applied": content_applied,
    }


@router.get("/graph")
def get_graph(
    db: Session = Depends(get_db),
    include_candidates: bool = False,
):
    return get_graph_data(db, include_candidates=include_candidates)


@router.get("/graph/hidden_nodes")
def get_hidden_nodes(db: Session = Depends(get_db)):
    return {"nodes": get_hidden_graph_nodes(db)}


@router.get("/nodes/{node_id}")
def get_node(node_id: int, db: Session = Depends(get_db)):
    detail = get_node_detail_data(db, node_id)
    if not detail:
        raise HTTPException(status_code=404, detail="Node not found")
    return detail


@router.post("/graph/manual_concepts")
def create_manual_concept(body: ManualConceptInput, db: Session = Depends(get_db)):
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="概念名称不能为空")

    tags = _normalize_tags(body.tags)
    paper_ids = _validate_paper_ids(db, body.paper_ids)
    content = (body.content or "").strip() or title
    existing = find_existing_concept_node(db, title, include_hidden=True)
    if existing is not None:
        merge_info = _adopt_existing_manual_identity(
            existing,
            incoming_content=content,
            incoming_tags=tags,
            incoming_paper_ids=paper_ids,
        )
        db.commit()
        db.refresh(existing)
        _reconcile_curated_wiki(db)
        detail = get_node_detail_data(db, existing.id)
        return {
            "node": detail,
            "created": False,
            "reused_existing": True,
            **merge_info,
        }

    node = KnowledgeNode(
        title=title,
        content=content,
        node_type="concept",
        node_origin=MANUAL_NODE_ORIGIN,
        hidden=False,
        tags=tags,
        source_paper_ids=paper_ids,
        embedding=None,
        # User-created concepts skip LLM review per design decision #4 —
        # the user's intent is the strongest signal we have.
        promotion_status=PROMOTION_PROMOTED,
        promoted_by=PROMOTED_BY_USER,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    _reconcile_curated_wiki(db)
    detail = get_node_detail_data(db, node.id)
    return {
        "node": detail,
        "created": True,
        "reused_existing": False,
        "adopted_existing": False,
        "merged_tags": 0,
        "merged_papers": 0,
        "content_applied": False,
    }


@router.put("/graph/manual_concepts/{node_id}")
def update_manual_concept(
    node_id: int,
    body: ManualConceptInput,
    db: Session = Depends(get_db),
):
    node = db.query(KnowledgeNode).filter(KnowledgeNode.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    if (node.node_origin or AUTO_NODE_ORIGIN) != MANUAL_NODE_ORIGIN:
        raise HTTPException(status_code=400, detail="只有手动概念可以编辑")

    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="概念名称不能为空")

    existing = find_existing_concept_node(
        db,
        title,
        exclude_node_id=node_id,
        include_hidden=True,
    )
    if existing is not None:
        raise HTTPException(
            status_code=409,
            detail={
                "message": f"已存在同名概念「{existing.title}」，请直接编辑现有概念，避免重复。",
                "existing_node_id": existing.id,
                "existing_title": existing.title,
            },
        )

    node.title = title
    node.content = (body.content or "").strip() or title
    node.tags = _normalize_tags(body.tags)
    node.source_paper_ids = _validate_paper_ids(db, body.paper_ids)
    db.commit()
    db.refresh(node)
    _reconcile_curated_wiki(db)
    detail = get_node_detail_data(db, node.id)
    return {
        "node": detail,
        "created": False,
        "reused_existing": False,
        "adopted_existing": False,
        "merged_tags": 0,
        "merged_papers": 0,
        "content_applied": False,
    }


@router.post("/graph/nodes/{node_id}/suppress")
def suppress_node(node_id: int, db: Session = Depends(get_db)):
    """Legacy endpoint — kept for back-compat with any external scripts.
    Now routes through the promotion lifecycle (status=rejected,
    promoted_by=user) so the rescue UI can recall the node, and so the
    stored evidence stays consistent with the rest of the system."""
    node = db.query(KnowledgeNode).filter(KnowledgeNode.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    if not is_concept_candidate_node(node):
        raise HTTPException(status_code=400, detail="只有概念候选节点可以淘汰")
    promotion_service.set_status_by_user(node, status=PROMOTION_REJECTED)
    db.commit()
    _reconcile_curated_wiki(db)
    return {"message": "节点已淘汰", "node_id": node.id}


@router.post("/graph/nodes/{node_id}/restore")
def restore_node(node_id: int, db: Session = Depends(get_db)):
    """Legacy endpoint — now resets the promotion lifecycle (status=pending,
    promoted_by=None) so the node re-enters the eval queue. The old
    `hidden` flag is also cleared in case anything still reads it."""
    node = db.query(KnowledgeNode).filter(KnowledgeNode.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    if is_concept_candidate_node(node):
        promotion_service.reset_status(node)
    node.hidden = False
    db.commit()
    _reconcile_curated_wiki(db)
    detail = get_node_detail_data(db, node.id)
    return {"node": detail}


@router.post("/graph/rebuild_edges")
def rebuild_edges(db: Session = Depends(get_db)):
    """Recompute similarity edges with current threshold. Does NOT re-call VLM."""
    cfg = load_config()
    threshold = cfg.get("similarity_threshold", 0.6)

    # Delete only similarity edges, keep explicit relationships
    db.query(KnowledgeEdge).filter(KnowledgeEdge.relation_type == "similar").delete()
    db.commit()

    nodes = db.query(KnowledgeNode).filter(KnowledgeNode.embedding.isnot(None)).all()
    for node in nodes:
        _add_similarity_edges(db, node, threshold)
    db.commit()

    edge_count = db.query(KnowledgeEdge).count()
    return {"threshold": threshold, "total_edges": edge_count}


@router.post("/graph/reset")
def reset_graph(db: Session = Depends(get_db)):
    """Clear auto-generated graph state but preserve manual concepts."""
    db.query(KnowledgeEdge).delete()
    db.query(KnowledgeNode).filter(
        KnowledgeNode.node_origin != MANUAL_NODE_ORIGIN
    ).delete(synchronize_session=False)
    db.query(Paper).update({
        Paper.processed: False,
        Paper.processed_at: None,
        Paper.raw_llm_response: None,
        Paper.error: None,
    })
    db.commit()
    for paper in db.query(Paper).all():
        try:
            sync_record_from_paper(paper, event="graph_reset")
        except Exception:
            pass
    _reconcile_curated_wiki(db)
    return {"message": "Auto graph cleared. Manual concepts were preserved. All papers marked for re-processing."}


@router.get("/search")
def search_nodes(q: str, db: Session = Depends(get_db)):
    if not q or len(q.strip()) < 1:
        return []
    query = f"%{q.strip()}%"
    processed_ids = {
        row[0] for row in db.query(Paper.id).filter(Paper.processed.is_(True)).all()
    }
    nodes = [n for n in db.query(KnowledgeNode).all() if not node_is_hidden(n)]
    nodes = [
        n for n in nodes
        if query.strip("%").lower() in (n.title or "").lower()
        or query.strip("%").lower() in (n.content or "").lower()
    ][:20]
    return [
        {
            "id": str(n.id),
            "title": n.title,
            "content": (n.content or "")[:200],
            "node_type": n.node_type,
            "origin": n.node_origin or AUTO_NODE_ORIGIN,
            "hidden": bool(n.hidden),
            "concept_candidate": is_concept_candidate_node(n),
            "publishable_concept": is_publishable_concept_node(n, processed_ids),
            "promotion_status": promotion_status(n),
            "promoted_by": getattr(n, "promoted_by", None),
            "promotion_reason": getattr(n, "promotion_reason", None),
            "last_promotion_eval_at": (
                n.last_promotion_eval_at.isoformat()
                if getattr(n, "last_promotion_eval_at", None)
                else None
            ),
            "tags": n.tags or [],
            "source_paper_ids": normalize_source_paper_ids(n.source_paper_ids),
            "created_at": n.created_at.isoformat() if getattr(n, "created_at", None) else None,
        }
        for n in nodes
    ]
