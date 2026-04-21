from __future__ import annotations
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from models import KnowledgeNode, KnowledgeEdge
from services.vlm_service import get_embedding, cosine_similarity

MAX_TITLE_LEN = 24


def _normalize_name(s: str) -> str:
    return (s or "").strip().lower()


def _truncate_title(s: str, max_len: int = MAX_TITLE_LEN) -> str:
    s = (s or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _find_existing_node(db: Session, name: str, aliases: list) -> Optional[KnowledgeNode]:
    """Find by exact title (case-insensitive) or alias intersection."""
    candidates = {_normalize_name(name)} | {_normalize_name(a) for a in aliases}
    candidates.discard("")
    if not candidates:
        return None

    for cand in candidates:
        node = db.query(KnowledgeNode).filter(
            func.lower(KnowledgeNode.title) == cand
        ).first()
        if node:
            return node

    # Alias overlap via tags (we store aliases in tags)
    all_nodes = db.query(KnowledgeNode).all()
    for n in all_nodes:
        existing = {_normalize_name(a) for a in (n.tags or []) if a}
        existing.add(_normalize_name(n.title))
        if candidates & existing:
            return n
    return None


def _upsert_node(
    db: Session,
    title: str,
    content: str,
    node_type: str,
    aliases: list,
    tags: list,
    paper_id: int,
    api_key: str,
    embedding_model: str,
) -> KnowledgeNode:
    title = _truncate_title(title)
    existing = _find_existing_node(db, title, aliases)
    if existing:
        ids = list(existing.source_paper_ids or [])
        if paper_id not in ids:
            ids.append(paper_id)
            existing.source_paper_ids = ids
        existing.tags = list({*(existing.tags or []), *tags, *aliases})
        # Enrich content if existing is shorter
        if content and len(content) > len(existing.content or ""):
            existing.content = content
        db.commit()
        return existing

    try:
        embedding = get_embedding(f"{title}: {content}", api_key, embedding_model)
    except Exception:
        embedding = None

    node = KnowledgeNode(
        title=title,
        content=content,
        node_type=node_type,
        tags=list({*tags, *aliases}),
        embedding=embedding,
        source_paper_ids=[paper_id],
    )
    db.add(node)
    db.flush()
    return node


def _add_edge(db: Session, src_id: int, tgt_id: int, relation: str, weight: float):
    if src_id == tgt_id:
        return
    existing = (
        db.query(KnowledgeEdge)
        .filter(
            KnowledgeEdge.source_id == src_id,
            KnowledgeEdge.target_id == tgt_id,
            KnowledgeEdge.relation_type == relation,
        )
        .first()
    )
    if existing:
        return
    # For 'similar', also check reverse direction to avoid duplicates
    if relation == "similar":
        reverse = (
            db.query(KnowledgeEdge)
            .filter(
                KnowledgeEdge.source_id == tgt_id,
                KnowledgeEdge.target_id == src_id,
                KnowledgeEdge.relation_type == "similar",
            )
            .first()
        )
        if reverse:
            return
    db.add(KnowledgeEdge(
        source_id=src_id, target_id=tgt_id,
        relation_type=relation, weight=weight,
    ))


def _add_similarity_edges(db: Session, node: KnowledgeNode, threshold: float):
    if not node.embedding:
        return
    others = db.query(KnowledgeNode).filter(
        KnowledgeNode.id != node.id,
        KnowledgeNode.embedding.isnot(None),
    ).all()
    for other in others:
        sim = cosine_similarity(node.embedding, other.embedding)
        if sim >= threshold:
            _add_edge(db, node.id, other.id, "similar", round(sim, 4))


def remove_nodes_for_paper(db: Session, paper_id: int) -> int:
    """Detach or delete graph nodes that were created from one paper.

    Shared nodes stay in the graph with this paper id removed from their
    source list. Paper-only nodes, including findings, are deleted with their
    connected edges so a reprocess or manual repair does not duplicate them.
    """
    nodes_to_delete: list[KnowledgeNode] = []
    removed = 0

    for node in db.query(KnowledgeNode).all():
        raw_ids = node.source_paper_ids or []
        ids = raw_ids if isinstance(raw_ids, list) else [raw_ids]
        if not any(str(source_id) == str(paper_id) for source_id in ids):
            continue

        remaining = [source_id for source_id in ids if str(source_id) != str(paper_id)]
        if remaining:
            node.source_paper_ids = remaining
        else:
            nodes_to_delete.append(node)
        removed += 1

    delete_ids = [node.id for node in nodes_to_delete]
    if delete_ids:
        db.query(KnowledgeEdge).filter(
            or_(
                KnowledgeEdge.source_id.in_(delete_ids),
                KnowledgeEdge.target_id.in_(delete_ids),
            )
        ).delete(synchronize_session=False)
        for node in nodes_to_delete:
            db.delete(node)

    db.flush()
    return removed


def add_nodes_from_paper_extraction(
    extraction: dict,
    paper_id: int,
    api_key: str,
    embedding_model: str,
    similarity_threshold: float,
    db: Session,
) -> list:
    """
    Convert paper extraction into knowledge nodes + edges.
    Node types: paper, technique, dataset, problem_area, finding, keyword
    """
    name_to_node: dict = {}
    keywords = extraction.get("keywords", []) or []
    tag_base = list(keywords)

    def register(node: KnowledgeNode, names: list):
        for n in names:
            key = _normalize_name(n)
            if key:
                name_to_node[key] = node

    # --- Paper node (the root for this paper) ---
    paper_title = (extraction.get("title") or "").strip()
    abstract = (extraction.get("abstract_summary") or "").strip()
    venue = (extraction.get("venue") or "").strip()
    year = extraction.get("year")
    paper_content = abstract
    if venue or year:
        paper_content = f"[{venue} {year}] {abstract}" if abstract else f"{venue} {year}"

    paper_node = None
    if paper_title:
        paper_node = _upsert_node(
            db, paper_title, paper_content or paper_title, "paper",
            aliases=[], tags=tag_base,
            paper_id=paper_id, api_key=api_key, embedding_model=embedding_model,
        )
        register(paper_node, [paper_title])

    # --- Problem area ---
    area = (extraction.get("problem_area") or "").strip()
    if area:
        area_node = _upsert_node(
            db, area, f"研究领域: {area}", "problem_area",
            aliases=[], tags=tag_base,
            paper_id=paper_id, api_key=api_key, embedding_model=embedding_model,
        )
        register(area_node, [area])
        if paper_node:
            _add_edge(db, paper_node.id, area_node.id, "belongs_to", 1.0)

    # --- Techniques ---
    tech_nodes: dict = {}  # name -> node
    for t in extraction.get("techniques", []):
        if not isinstance(t, dict):
            continue
        name = (t.get("name") or "").strip()
        if not name:
            continue
        aliases = [a.strip() for a in (t.get("aliases") or []) if a and a.strip()]
        role = (t.get("role") or "").strip()
        desc = f"{name}" + (f"（{role}）" if role else "")
        node = _upsert_node(
            db, name, desc, "technique",
            aliases=aliases, tags=tag_base,
            paper_id=paper_id, api_key=api_key, embedding_model=embedding_model,
        )
        tech_nodes[_normalize_name(name)] = node
        register(node, [name, *aliases])
        if paper_node:
            _add_edge(db, paper_node.id, node.id, "uses", 1.0)

    # --- builds_on edges between techniques (technical path!) ---
    for t in extraction.get("techniques", []):
        if not isinstance(t, dict):
            continue
        name = _normalize_name(t.get("name"))
        if not name or name not in tech_nodes:
            continue
        src = tech_nodes[name]
        for dep in t.get("builds_on") or []:
            dep_key = _normalize_name(dep)
            target = tech_nodes.get(dep_key) or name_to_node.get(dep_key)
            if target:
                _add_edge(db, src.id, target.id, "builds_on", 1.0)

    # --- Datasets ---
    for d in extraction.get("datasets", []):
        if not isinstance(d, dict):
            continue
        name = (d.get("name") or "").strip()
        if not name:
            continue
        purpose = (d.get("purpose") or "").strip()
        node = _upsert_node(
            db, name, f"数据集: {name}" + (f"（{purpose}）" if purpose else ""),
            "dataset",
            aliases=[], tags=tag_base,
            paper_id=paper_id, api_key=api_key, embedding_model=embedding_model,
        )
        register(node, [name])
        if paper_node:
            rel = "trained_on" if "train" in purpose.lower() or "训练" in purpose else "evaluated_on"
            _add_edge(db, paper_node.id, node.id, rel, 1.0)

    # --- Baselines (link as compared_to) ---
    for b in extraction.get("baselines", []) or []:
        if not isinstance(b, str) or not b.strip():
            continue
        name = b.strip()
        # If it matches an existing technique, link to that; otherwise create technique node
        existing = name_to_node.get(_normalize_name(name))
        if not existing:
            existing = _upsert_node(
                db, name, f"Baseline: {name}", "technique",
                aliases=[], tags=tag_base,
                paper_id=paper_id, api_key=api_key, embedding_model=embedding_model,
            )
            register(existing, [name])
        if paper_node:
            _add_edge(db, paper_node.id, existing.id, "compared_to", 1.0)

    # --- Key findings (unique per paper, no merging) ---
    for f in extraction.get("key_findings", []) or []:
        if isinstance(f, str):
            short, detail = f[:MAX_TITLE_LEN], f
        elif isinstance(f, dict):
            short = (f.get("short") or "").strip()
            detail = (f.get("detail") or short).strip()
            if not short and detail:
                short = detail[:MAX_TITLE_LEN]
        else:
            continue
        if not detail or len(detail) < 5:
            continue
        node = KnowledgeNode(
            title=_truncate_title(short),
            content=detail,
            node_type="finding",
            tags=tag_base,
            source_paper_ids=[paper_id],
        )
        try:
            node.embedding = get_embedding(detail, api_key, embedding_model)
        except Exception:
            node.embedding = None
        db.add(node)
        db.flush()
        if paper_node:
            _add_edge(db, paper_node.id, node.id, "finding", 1.0)

    db.commit()

    # --- Similarity edges for newly-touched nodes ---
    touched = list({n.id: n for n in name_to_node.values()}.values())
    for node in touched:
        _add_similarity_edges(db, node, similarity_threshold)
    db.commit()

    return [n.id for n in touched]


def get_graph_data(db: Session) -> dict:
    nodes = db.query(KnowledgeNode).all()
    edges = db.query(KnowledgeEdge).all()
    return {
        "nodes": [
            {
                "id": str(n.id),
                "title": n.title,
                "content": n.content,
                "node_type": n.node_type,
                "tags": n.tags or [],
                "source_paper_ids": n.source_paper_ids or [],
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in nodes
        ],
        "edges": [
            {
                "id": str(e.id),
                "source": str(e.source_id),
                "target": str(e.target_id),
                "relation_type": e.relation_type,
                "weight": e.weight,
            }
            for e in edges
        ],
    }
