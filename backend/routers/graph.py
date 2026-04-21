from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from database import get_db
from models import Paper, KnowledgeNode, KnowledgeEdge
from config import load_config
from services.graph_service import get_graph_data, _add_similarity_edges

router = APIRouter(prefix="/api", tags=["graph"])


@router.get("/graph")
def get_graph(db: Session = Depends(get_db)):
    return get_graph_data(db)


@router.get("/nodes/{node_id}")
def get_node(node_id: int, db: Session = Depends(get_db)):
    node = db.query(KnowledgeNode).filter(KnowledgeNode.id == node_id).first()
    if not node:
        raise HTTPException(status_code=404, detail="Node not found")

    # Get connected nodes via edges
    edges_out = db.query(KnowledgeEdge).filter(KnowledgeEdge.source_id == node_id).all()
    edges_in = db.query(KnowledgeEdge).filter(KnowledgeEdge.target_id == node_id).all()

    connected_ids = {e.target_id for e in edges_out} | {e.source_id for e in edges_in}
    connected_nodes = db.query(KnowledgeNode).filter(KnowledgeNode.id.in_(connected_ids)).all()

    all_edges = edges_out + edges_in

    return {
        "id": node.id,
        "title": node.title,
        "content": node.content,
        "node_type": node.node_type,
        "tags": node.tags or [],
        "source_paper_ids": node.source_paper_ids or [],
        "created_at": node.created_at.isoformat() if node.created_at else None,
        "connected_nodes": [
            {
                "id": n.id,
                "title": n.title,
                "node_type": n.node_type,
            }
            for n in connected_nodes
        ],
        "edges": [
            {
                "id": e.id,
                "source": e.source_id,
                "target": e.target_id,
                "relation_type": e.relation_type,
                "weight": e.weight,
            }
            for e in all_edges
        ],
    }


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
    """Wipe all knowledge nodes/edges, mark all papers as unprocessed (for re-extraction)."""
    db.query(KnowledgeEdge).delete()
    db.query(KnowledgeNode).delete()
    db.query(Paper).update({
        Paper.processed: False,
        Paper.processed_at: None,
        Paper.raw_llm_response: None,
        Paper.error: None,
    })
    db.commit()
    return {"message": "Graph cleared. All papers marked for re-processing."}


@router.get("/search")
def search_nodes(q: str, db: Session = Depends(get_db)):
    if not q or len(q.strip()) < 1:
        return []
    query = f"%{q.strip()}%"
    nodes = (
        db.query(KnowledgeNode)
        .filter(
            KnowledgeNode.title.ilike(query) | KnowledgeNode.content.ilike(query)
        )
        .limit(20)
        .all()
    )
    return [
        {
            "id": n.id,
            "title": n.title,
            "content": n.content[:200],
            "node_type": n.node_type,
            "tags": n.tags or [],
        }
        for n in nodes
    ]
