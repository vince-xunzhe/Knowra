from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session
from database import get_db
from models import Paper, KnowledgeNode, KnowledgeEdge
from config import load_config
from services import wiki_search as wiki_search_service
from services.graph_service import (
    AUTO_NODE_ORIGIN,
    MANUAL_NODE_ORIGIN,
    _add_similarity_edges,
    get_graph_data,
    get_node_detail_data,
    node_is_hidden,
    normalize_source_paper_ids,
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


@router.get("/graph")
def get_graph(db: Session = Depends(get_db)):
    return get_graph_data(db)


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

    paper_ids = _validate_paper_ids(db, body.paper_ids)
    node = KnowledgeNode(
        title=title,
        content=(body.content or "").strip() or title,
        node_type="concept",
        node_origin=MANUAL_NODE_ORIGIN,
        hidden=False,
        tags=_normalize_tags(body.tags),
        source_paper_ids=paper_ids,
        embedding=None,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    _reconcile_curated_wiki(db)
    detail = get_node_detail_data(db, node.id)
    return {"node": detail}


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

    node.title = title
    node.content = (body.content or "").strip() or title
    node.tags = _normalize_tags(body.tags)
    node.source_paper_ids = _validate_paper_ids(db, body.paper_ids)
    db.commit()
    db.refresh(node)
    _reconcile_curated_wiki(db)
    detail = get_node_detail_data(db, node.id)
    return {"node": detail}


@router.post("/graph/nodes/{node_id}/suppress")
def suppress_node(node_id: int, db: Session = Depends(get_db)):
    node = db.query(KnowledgeNode).filter(KnowledgeNode.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")
    if node.node_type == "paper":
        raise HTTPException(status_code=400, detail="论文节点不能直接删除")

    node.hidden = True
    db.commit()
    _reconcile_curated_wiki(db)
    return {"message": "节点已从概念层移除", "node_id": node.id}


@router.post("/graph/nodes/{node_id}/restore")
def restore_node(node_id: int, db: Session = Depends(get_db)):
    node = db.query(KnowledgeNode).filter(KnowledgeNode.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

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
            "content": n.content[:200],
            "node_type": n.node_type,
            "origin": n.node_origin or AUTO_NODE_ORIGIN,
            "hidden": bool(n.hidden),
            "tags": n.tags or [],
            "source_paper_ids": normalize_source_paper_ids(n.source_paper_ids),
        }
        for n in nodes
    ]
