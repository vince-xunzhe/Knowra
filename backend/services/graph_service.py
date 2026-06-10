from __future__ import annotations
import json
from datetime import datetime, timezone
from typing import Optional
from sqlalchemy.orm import Session
from sqlalchemy import func, or_
from models import Paper, KnowledgeNode, KnowledgeEdge
from logging_utils import get_logger
from services.paper_category_service import PAPER_CATEGORY_OTHER, effective_paper_category
from services.vlm_service import get_embedding, cosine_similarity, parse_extraction_response

MAX_TITLE_LEN = 24
AUTO_NODE_ORIGIN = "auto"
MANUAL_NODE_ORIGIN = "manual"
MANUAL_LINK_RELATION = "curated_link"
AUTO_CONCEPT_NODE_TYPES = {"technique", "dataset", "problem_area", "concept"}

# Promotion lifecycle. Concept-eligible nodes start as `pending` and need to
# pass either heuristic or LLM review (or be promoted by the user) before
# they show up in the curated graph or get a wiki concept page. Paper
# nodes are tagged `promoted` at creation since they don't go through
# curation.
PROMOTION_PENDING = "pending"
PROMOTION_PROMOTED = "promoted"
PROMOTION_REJECTED = "rejected"
PROMOTION_STATUSES = {PROMOTION_PENDING, PROMOTION_PROMOTED, PROMOTION_REJECTED}

PROMOTED_BY_HEURISTIC = "heuristic"
PROMOTED_BY_LLM = "llm"
PROMOTED_BY_USER = "user"
PROMOTED_BY_LEGACY = "legacy"

logger = get_logger("graph_service")


def _normalize_name(s: str) -> str:
    return (s or "").strip().lower()


def _truncate_title(s: str, max_len: int = MAX_TITLE_LEN) -> str:
    s = (s or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 1] + "…"


def _paper_title_matches(node_title: str, full_title: str) -> bool:
    existing = _normalize_name(node_title)
    target = _normalize_name(full_title)
    if not existing or not target:
        return False
    if existing == target:
        return True
    trimmed = existing.rstrip("…").rstrip(".")
    return bool(trimmed) and target.startswith(trimmed)


def normalize_source_paper_ids(values) -> list[str]:
    """Coerce ``KnowledgeNode.source_paper_ids`` into a clean list of
    string IDs.

    Post-multitenant migration paper IDs are UUID strings; this
    function deliberately keeps them as strings and just strips/
    drops empty entries. Pre-migration JSON data may contain INT
    values — those are str()ed so the dict-key lookup in callers
    (``papers_by_id[paper_id]``) stays consistent regardless of which
    schema generation produced the data."""
    raw = values or []
    if not isinstance(raw, list):
        raw = [raw]
    out: list[str] = []
    for item in raw:
        if item is None:
            continue
        s = str(item).strip()
        if s:
            out.append(s)
    return out


def node_origin(node: KnowledgeNode) -> str:
    origin = (getattr(node, "node_origin", None) or AUTO_NODE_ORIGIN).strip().lower()
    return origin or AUTO_NODE_ORIGIN


def node_is_hidden(node: KnowledgeNode) -> bool:
    return bool(getattr(node, "hidden", False))


def promotion_status(node: KnowledgeNode) -> str:
    raw = (getattr(node, "promotion_status", None) or PROMOTION_PENDING).strip().lower()
    return raw if raw in PROMOTION_STATUSES else PROMOTION_PENDING


def is_concept_candidate_node(node: KnowledgeNode) -> bool:
    """A node that is conceptually allowed to be promoted to a wiki concept.
    Paper nodes are deliberately excluded — they have their own rendering
    path."""
    return (node.node_type or "") in AUTO_CONCEPT_NODE_TYPES


def is_publishable_concept_node(
    node: KnowledgeNode,
    processed_paper_ids: Optional[set] = None,
) -> bool:
    """The single gate that decides whether a concept page gets compiled.
    After the concept-first redesign, this is just a thin wrapper around
    `promotion_status == promoted` plus a sanity check that at least one
    source paper is still processed (otherwise the concept has nothing to
    cite)."""
    if node_is_hidden(node):
        return False
    if not is_concept_candidate_node(node):
        return False
    if promotion_status(node) != PROMOTION_PROMOTED:
        return False

    source_ids = normalize_source_paper_ids(node.source_paper_ids)
    if processed_paper_ids is not None:
        # Coerce both sides to strings so callers can pass legacy INT
        # sets, post-migration UUID sets, or mixed sets without surprises.
        processed_str = {str(p) for p in processed_paper_ids}
        source_ids = [pid for pid in source_ids if pid in processed_str]
    return bool(source_ids)


def _find_existing_node(db: Session, name: str, aliases: list) -> Optional[KnowledgeNode]:
    """Find by exact title (case-insensitive) or alias intersection."""
    candidates = {_normalize_name(name)} | {_normalize_name(a) for a in aliases}
    candidates.discard("")
    if not candidates:
        return None

    for cand in candidates:
        node = db.query(KnowledgeNode).filter(
            func.lower(KnowledgeNode.title) == cand,
            KnowledgeNode.node_origin != MANUAL_NODE_ORIGIN,
        ).first()
        if node:
            return node

    # Alias overlap via tags (we store aliases in tags)
    all_nodes = db.query(KnowledgeNode).filter(
        KnowledgeNode.node_origin != MANUAL_NODE_ORIGIN,
    ).all()
    for n in all_nodes:
        existing = {_normalize_name(a) for a in (n.tags or []) if a}
        existing.add(_normalize_name(n.title))
        if candidates & existing:
            return n
    return None


def _id_sortkey(node) -> int:
    """Stable integer sort key from a KnowledgeNode's id, post-W3.2 UUID.

    Several ranker tuples here historically used ``int(node.id)`` as a
    final tiebreaker. After the multitenant migration, ``node.id`` is a
    UUID string and ``int()`` blows up. ``legacy_id`` (preserved by the
    migrator) is the original INT id when present; for rows born after
    the migration we fall back to a deterministic, in-process-stable
    hash of the UUID. The exact value doesn't matter — only that
    comparing two rows yields a consistent order.
    """
    legacy = getattr(node, "legacy_id", None)
    if isinstance(legacy, int):
        return legacy
    uid = getattr(node, "id", None)
    if uid is None:
        return 10**9
    if isinstance(uid, int):
        return uid
    # Mask to positive int range; hash() is stable within a process which
    # is all the sort needs.
    return hash(str(uid)) & 0x7fffffff


def _find_existing_paper_node(
    db: Session,
    *,
    paper_id: str,
    title: str,
    exclude_node_id: Optional[int] = None,
) -> Optional[KnowledgeNode]:
    best: Optional[KnowledgeNode] = None
    best_rank: Optional[tuple[int, int, int, int]] = None
    for node in db.query(KnowledgeNode).all():
        if exclude_node_id is not None and getattr(node, "id", None) == exclude_node_id:
            continue
        if (node.node_type or "") != "paper":
            continue
        source_ids = normalize_source_paper_ids(node.source_paper_ids)
        has_paper_id = paper_id in source_ids
        title_match = _paper_title_matches(node.title or "", title)
        if not has_paper_id and not title_match:
            continue
        rank = (
            0 if has_paper_id else 1,
            0 if title_match else 1,
            len(source_ids) if source_ids else 10**9,
            _id_sortkey(node),
        )
        if best is None or rank < best_rank:
            best = node
            best_rank = rank
    return best


def _paper_node_payload_from_paper(paper: Optional[Paper]) -> tuple[str, str]:
    if paper is None:
        return "", ""

    extraction = None
    if paper.raw_llm_response:
        try:
            extraction = parse_extraction_response(paper.raw_llm_response)
        except Exception:
            extraction = None

    title = (paper.title or (extraction or {}).get("title") or paper.filename or f"paper #{paper.id}").strip()
    abstract = str((extraction or {}).get("abstract_summary") or "").strip()
    venue = str((extraction or {}).get("venue") or "").strip()
    year = (extraction or {}).get("year")

    content = abstract
    if venue or year:
        prefix = " ".join(part for part in [venue, str(year or "").strip()] if part).strip()
        content = f"[{prefix}] {abstract}".strip() if abstract else prefix
    if not content:
        content = title
    return title, content


def _find_single_source_paper_node(
    db: Session,
    *,
    paper_id: str,
    title: str,
    exclude_node_id: Optional[int] = None,
) -> Optional[KnowledgeNode]:
    best: Optional[KnowledgeNode] = None
    best_rank: Optional[tuple[int, int]] = None
    for node in db.query(KnowledgeNode).all():
        if exclude_node_id is not None and getattr(node, "id", None) == exclude_node_id:
            continue
        if (node.node_type or "") != "paper":
            continue
        source_ids = normalize_source_paper_ids(node.source_paper_ids)
        if source_ids != [paper_id]:
            continue
        rank = (
            0 if _paper_title_matches(node.title or "", title) else 1,
            _id_sortkey(node),
        )
        if best is None or rank < best_rank:
            best = node
            best_rank = rank
    return best


def _ensure_single_source_paper_node(
    db: Session,
    *,
    paper_id: str,
    papers_by_id: dict[int, Paper],
    exclude_node_id: Optional[int] = None,
) -> KnowledgeNode:
    title, content = _paper_node_payload_from_paper(papers_by_id.get(paper_id))
    existing = _find_single_source_paper_node(
        db,
        paper_id=paper_id,
        title=title,
        exclude_node_id=exclude_node_id,
    )
    if existing:
        if title and existing.title != title:
            existing.title = title
        if content and len(content) > len(existing.content or ""):
            existing.content = content
        existing.source_paper_ids = [paper_id]
        return existing

    node = KnowledgeNode(
        title=title or f"paper #{paper_id}",
        content=content or title or f"paper #{paper_id}",
        node_type="paper",
        node_origin=AUTO_NODE_ORIGIN,
        hidden=False,
        tags=[],
        embedding=None,
        source_paper_ids=[paper_id],
        promotion_status=PROMOTION_PROMOTED,
    )
    db.add(node)
    db.flush()
    return node


def _select_canonical_paper_id(
    node: KnowledgeNode,
    source_ids: list[str],
    papers_by_id: dict[str, Paper],
) -> tuple[Optional[str], bool]:
    normalized_title = (node.title or "").strip()
    for paper_id in source_ids:
        paper = papers_by_id.get(paper_id)
        if paper and _paper_title_matches(normalized_title, paper.title or ""):
            return paper_id, True
    return (source_ids[0], False) if source_ids else (None, False)


def repair_merged_paper_nodes(
    db: Session,
    similarity_threshold: float = 0.6,
) -> int:
    """Repair legacy paper-node merges caused by title-only upserts.

    Paper nodes should be one-to-one with `Paper.id`. Older builds sometimes
    appended multiple paper ids onto the same node, which polluted paper
    titles, the right-side wiki panel, and paper→concept edges. This repair:
      1. shrinks merged paper nodes back to a single canonical paper id
      2. redistributes obvious edges to the correct single-paper nodes
      3. drops ambiguous/stale edges rather than keeping wrong ones
      4. rebuilds `similar` edges for the repaired canonical node

    Idempotent: once a DB is clean, rerunning is a no-op.
    """
    papers_by_id = {paper.id: paper for paper in db.query(Paper).all()}
    repaired = 0

    merged_nodes = [
        node
        for node in db.query(KnowledgeNode).all()
        if (node.node_type or "") == "paper"
        and len(normalize_source_paper_ids(node.source_paper_ids)) > 1
    ]

    for node in merged_nodes:
        source_ids = list(dict.fromkeys(normalize_source_paper_ids(node.source_paper_ids)))
        canonical_paper_id, matched_by_title = _select_canonical_paper_id(
            node,
            source_ids,
            papers_by_id,
        )
        if canonical_paper_id is None:
            continue

        extra_paper_ids = [paper_id for paper_id in source_ids if paper_id != canonical_paper_id]
        target_nodes = {
            paper_id: _ensure_single_source_paper_node(
                db,
                paper_id=paper_id,
                papers_by_id=papers_by_id,
                exclude_node_id=node.id,
            )
            for paper_id in extra_paper_ids
        }

        incident_edges = (
            db.query(KnowledgeEdge)
            .filter(
                or_(
                    KnowledgeEdge.source_id == node.id,
                    KnowledgeEdge.target_id == node.id,
                )
            )
            .all()
        )

        for edge in incident_edges:
            other_id = edge.target_id if edge.source_id == node.id else edge.source_id
            other = db.query(KnowledgeNode).filter(KnowledgeNode.id == other_id).first()
            if other is None:
                db.delete(edge)
                continue

            if edge.relation_type == "similar":
                db.delete(edge)
                continue

            other_source_ids = set(normalize_source_paper_ids(other.source_paper_ids))
            keep_on_canonical = (
                edge.relation_type == MANUAL_LINK_RELATION
                or (
                    (other.node_type or "") != "paper"
                    and canonical_paper_id in other_source_ids
                )
            )

            for paper_id in extra_paper_ids:
                if paper_id not in other_source_ids:
                    continue
                target_node = target_nodes[paper_id]
                if target_node.id == other.id:
                    continue
                if edge.source_id == node.id:
                    src_id, tgt_id = target_node.id, other.id
                else:
                    src_id, tgt_id = other.id, target_node.id
                _add_edge(db, src_id, tgt_id, edge.relation_type, edge.weight or 0.0)

            if not keep_on_canonical:
                db.delete(edge)

        node.source_paper_ids = [canonical_paper_id]
        canonical_title, canonical_content = _paper_node_payload_from_paper(
            papers_by_id.get(canonical_paper_id)
        )
        if canonical_title:
            node.title = canonical_title
        if canonical_content:
            node.content = canonical_content
        if not matched_by_title:
            node.embedding = None

        db.flush()
        if isinstance(node.embedding, list) and node.embedding:
            _add_similarity_edges(db, node, similarity_threshold)

        repaired += 1

    if repaired:
        db.commit()
    return repaired


def find_existing_concept_node(
    db: Session,
    name: str,
    aliases: Optional[list[str]] = None,
    *,
    exclude_node_id: Optional[int] = None,
    include_hidden: bool = False,
    include_tags: bool = False,
) -> Optional[KnowledgeNode]:
    """Find an existing concept-like node, including manual concepts.

    Used by user-authored flows (Ask synthesis, future manual-create guards)
    where we want to avoid creating another concept with the same visible
    identity. By default this matches only visible titles / explicit aliases;
    generic tags are only considered when a caller opts in. Ranking prefers:
      1. visible nodes over hidden ones
      2. manual concepts over auto-extracted nodes
      3. promoted nodes over pending / rejected
      4. `concept` nodes over technique / dataset / problem_area
    """
    candidates = {_normalize_name(name)}
    for alias in aliases or []:
        candidates.add(_normalize_name(alias))
    candidates.discard("")
    if not candidates:
        return None

    best: Optional[KnowledgeNode] = None
    best_rank: Optional[tuple[int, int, int, int]] = None
    for node in db.query(KnowledgeNode).all():
        if exclude_node_id is not None and getattr(node, "id", None) == exclude_node_id:
            continue
        if (node.node_type or "") == "paper":
            continue
        if not include_hidden and node_is_hidden(node):
            continue

        existing = {_normalize_name(node.title)}
        if include_tags:
            existing.update(_normalize_name(tag) for tag in (node.tags or []) if tag)
        existing.discard("")
        if not (candidates & existing):
            continue

        rank = (
            0 if node_origin(node) == MANUAL_NODE_ORIGIN else 1,
            0 if promotion_status(node) == PROMOTION_PROMOTED else 1,
            0 if (node.node_type or "") == "concept" else 1,
            _id_sortkey(node),
        )
        if best is None or rank < best_rank:
            best = node
            best_rank = rank
    return best


def _upsert_node(
    db: Session,
    title: str,
    content: str,
    node_type: str,
    aliases: list,
    tags: list,
    paper_id: str,
    api_key: str,
    embedding_model: str,
) -> KnowledgeNode:
    raw_title = (title or "").strip()
    title = raw_title if node_type == "paper" else _truncate_title(raw_title)
    existing = (
        _find_existing_paper_node(db, paper_id=paper_id, title=raw_title)
        if node_type == "paper"
        else _find_existing_node(db, title, aliases)
    )
    if existing:
        before_ids = normalize_source_paper_ids(existing.source_paper_ids)
        ids = list(existing.source_paper_ids or [])
        ids_changed = paper_id not in ids
        if ids_changed:
            ids.append(paper_id)
            existing.source_paper_ids = ids
        if node_type == "paper" and raw_title and existing.title != raw_title:
            existing.title = raw_title
        previous_tags = list(existing.tags or [])
        existing.tags = list({*(existing.tags or []), *tags, *aliases})
        # Enrich content if existing is shorter
        content_updated = False
        if content and len(content) > len(existing.content or ""):
            existing.content = content
            content_updated = True
        # New source paper changes the candidate's evidence base — the
        # promotion service uses this to decide who needs re-evaluation
        # (see _select_for_eval). User-overridden status is preserved.
        if ids_changed and promotion_status(existing) == PROMOTION_PENDING:
            existing.last_promotion_eval_at = None
        logger.info(
            "node_merge_resolved node_id=%s node_type=%s incoming_title=%r source_papers_before=%s source_papers_after=%s merged_paper_id=%s tags_before=%s tags_after=%s content_updated=%s",
            existing.id,
            node_type,
            raw_title,
            before_ids,
            normalize_source_paper_ids(existing.source_paper_ids),
            paper_id,
            previous_tags,
            list(existing.tags or []),
            content_updated,
        )
        db.commit()
        return existing

    try:
        embedding = get_embedding(f"{title}: {content}", api_key, embedding_model)
    except Exception:
        embedding = None

    # Concept-eligible types start at `pending` and need promotion review.
    # Paper is auto-promoted because it isn't curated.
    initial_status = (
        PROMOTION_PENDING
        if node_type in AUTO_CONCEPT_NODE_TYPES
        else PROMOTION_PROMOTED
    )
    node = KnowledgeNode(
        title=title,
        content=content,
        node_type=node_type,
        node_origin=AUTO_NODE_ORIGIN,
        hidden=False,
        tags=list({*tags, *aliases}),
        embedding=embedding,
        source_paper_ids=[paper_id],
        promotion_status=initial_status,
    )
    db.add(node)
    db.flush()
    return node


def _add_edge(
    db: Session,
    src_id: int,
    tgt_id: int,
    relation: str,
    weight: float,
) -> Optional[KnowledgeEdge]:
    if src_id == tgt_id:
        return None
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
        return None
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
            return None
    edge = KnowledgeEdge(
        source_id=src_id, target_id=tgt_id,
        relation_type=relation, weight=weight,
        created_at=datetime.now(timezone.utc),
    )
    db.add(edge)
    return edge


def _add_similarity_edges(
    db: Session,
    node: KnowledgeNode,
    threshold: float,
    *,
    context: str = "incremental_build",
) -> dict[str, int]:
    summary = {"candidate_edges": 0, "final_edges": 0}
    if not isinstance(node.embedding, list) or not node.embedding:
        return summary
    others = db.query(KnowledgeNode).filter(
        KnowledgeNode.id != node.id,
        KnowledgeNode.embedding.isnot(None),
    ).all()
    for other in others:
        if not isinstance(other.embedding, list) or not other.embedding:
            continue
        summary["candidate_edges"] += 1
        sim = cosine_similarity(node.embedding, other.embedding)
        if sim >= threshold:
            rounded = round(sim, 4)
            edge = _add_edge(db, node.id, other.id, "similar", rounded)
            if edge is None:
                continue
            summary["final_edges"] += 1
            logger.info(
                "similar_edge_created context=%s source_id=%s source_title=%r target_id=%s target_title=%r threshold=%.4f similarity=%.4f",
                context,
                node.id,
                getattr(node, "title", None),
                other.id,
                getattr(other, "title", None),
                threshold,
                rounded,
            )
    return summary


def rebuild_similarity_edges(db: Session, threshold: float) -> dict:
    """Rebuild only `similar` edges from existing embeddings.

    Safe operation:
      - does not re-run extraction/embedding calls
      - does not touch non-similar edges
      - does not touch any nodes (manual or auto)
    """
    total_nodes = db.query(KnowledgeNode).count()
    removed_similar_edges = (
        db.query(KnowledgeEdge)
        .filter(KnowledgeEdge.relation_type == "similar")
        .delete(synchronize_session=False)
    )

    embedding_nodes = [
        node
        for node in db.query(KnowledgeNode).filter(KnowledgeNode.embedding.isnot(None)).all()
        if isinstance(node.embedding, list) and node.embedding
    ]

    candidate_edges = 0
    final_edges = 0
    for idx, source in enumerate(embedding_nodes):
        for target in embedding_nodes[idx + 1:]:
            candidate_edges += 1
            sim = cosine_similarity(source.embedding, target.embedding)
            if sim < threshold:
                continue
            rounded = round(sim, 4)
            edge = _add_edge(db, source.id, target.id, "similar", rounded)
            if edge is None:
                continue
            final_edges += 1
            logger.info(
                "similar_edge_created context=rebuild source_id=%s source_title=%r target_id=%s target_title=%r threshold=%.4f similarity=%.4f",
                source.id,
                source.title,
                target.id,
                target.title,
                threshold,
                rounded,
            )

    db.commit()
    total_edges = db.query(KnowledgeEdge).count()
    summary = {
        "threshold": threshold,
        "total_nodes": total_nodes,
        "embedding_nodes": len(embedding_nodes),
        "candidate_edges": candidate_edges,
        "final_edges": final_edges,
        "removed_similar_edges": removed_similar_edges,
        "total_edges": total_edges,
    }
    logger.info(
        "similar_rebuild_summary total_nodes=%s embedding_nodes=%s candidate_edges=%s final_edges=%s removed_similar_edges=%s threshold=%.4f total_edges=%s",
        summary["total_nodes"],
        summary["embedding_nodes"],
        summary["candidate_edges"],
        summary["final_edges"],
        summary["removed_similar_edges"],
        threshold,
        summary["total_edges"],
    )
    return summary


def remove_nodes_for_paper(db: Session, paper_id: str) -> int:
    """Detach or delete graph nodes that were created from one paper.

    Shared nodes stay in the graph with this paper id removed from their
    source list. Paper-only nodes are deleted with their connected edges so a
    reprocess or manual repair does not duplicate them.
    """
    nodes_to_delete: list[KnowledgeNode] = []
    removed = 0

    for node in db.query(KnowledgeNode).all():
        if node_origin(node) == MANUAL_NODE_ORIGIN:
            continue
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
    paper_id: str,
    api_key: str,
    embedding_model: str,
    similarity_threshold: float,
    db: Session,
) -> list:
    """
    Convert paper extraction into knowledge nodes + edges.
    Node types: paper, technique, dataset, problem_area, keyword
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

    # `finding` nodes were dropped — they bloated the graph with one
    # node per per-paper bullet, no cross-paper merging, and added little
    # signal that wasn't already in the paper page itself. The
    # `key_findings` field in the extraction JSON is still used by the
    # paper-page LLM compile to build the "关键发现" section.

    db.commit()

    # --- Similarity edges for newly-touched nodes ---
    touched = list({n.id: n for n in name_to_node.values()}.values())
    for node in touched:
        _add_similarity_edges(db, node, similarity_threshold)
    db.commit()

    return [n.id for n in touched]


def _processed_paper_ids(db: Session) -> set[int]:
    return {
        row[0] for row in db.query(Paper.id).filter(Paper.processed.is_(True)).all()
    }


def _serialize_graph_node(node: KnowledgeNode, processed_paper_ids: set[int]) -> dict:
    last_eval = getattr(node, "last_promotion_eval_at", None)
    source_paper_ids = normalize_source_paper_ids(node.source_paper_ids)
    paper_id = source_paper_ids[0] if (node.node_type or "") == "paper" and len(source_paper_ids) == 1 else None
    # Post-W3.2 the canonical id is a UUID string; concept_id is a
    # legacy int the frontend still uses to key into wiki / lint
    # payloads. We hand back the migrated `legacy_id` when present,
    # else null. New post-migration rows have no INT id at all — the
    # frontend already falls back to `id` (the UUID string) in that
    # case (see NodeDetail.tsx / WikiLintModal.tsx).
    if (node.node_type or "") == "paper":
        concept_id = None
    else:
        legacy = getattr(node, "legacy_id", None)
        concept_id = legacy if isinstance(legacy, int) else None
    return {
        "id": str(node.id),
        "title": node.title,
        "content": node.content,
        "node_type": node.node_type,
        "origin": node_origin(node),
        "hidden": node_is_hidden(node),
        "concept_candidate": is_concept_candidate_node(node),
        "publishable_concept": is_publishable_concept_node(node, processed_paper_ids),
        "promotion_status": promotion_status(node),
        "promoted_by": getattr(node, "promoted_by", None),
        "promotion_reason": getattr(node, "promotion_reason", None),
        "last_promotion_eval_at": last_eval.isoformat() if last_eval else None,
        "tags": node.tags or [],
        "source_paper_ids": source_paper_ids,
        "paper_id": paper_id,
        "concept_id": concept_id,
        "created_at": node.created_at.isoformat() if node.created_at else None,
    }


def _paper_nodes_by_paper_id(nodes: list[KnowledgeNode]) -> dict[int, KnowledgeNode]:
    out: dict[int, KnowledgeNode] = {}
    for node in nodes:
        if node.node_type != "paper":
            continue
        for pid in normalize_source_paper_ids(node.source_paper_ids):
            out.setdefault(pid, node)
    return out


def _paper_category_by_id(db: Session, paper_ids: set[int]) -> dict[int, str]:
    if not paper_ids:
        return {}
    out: dict[int, str] = {}
    papers = db.query(Paper).filter(Paper.id.in_(list(paper_ids))).all()
    for paper in papers:
        extraction = None
        if paper.raw_llm_response:
            try:
                extraction = json.loads(paper.raw_llm_response)
            except (TypeError, json.JSONDecodeError):
                extraction = None
        out[paper.id] = effective_paper_category(paper, extraction)
    return out


def _majority_category(source_paper_ids: list[int], paper_categories: dict[int, str]) -> str:
    counts: dict[str, int] = {}
    for pid in source_paper_ids:
        category = paper_categories.get(pid)
        if not category:
            continue
        counts[category] = counts.get(category, 0) + 1
    if not counts:
        return PAPER_CATEGORY_OTHER
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))[0][0]


def _manual_synthetic_edges(nodes: list[KnowledgeNode]) -> list[dict]:
    paper_nodes = _paper_nodes_by_paper_id(nodes)
    out: list[dict] = []
    for node in nodes:
        if node_origin(node) != MANUAL_NODE_ORIGIN or node.node_type != "concept":
            continue
        for pid in normalize_source_paper_ids(node.source_paper_ids):
            paper_node = paper_nodes.get(pid)
            if not paper_node:
                continue
            out.append({
                "id": f"manual:{node.id}:{paper_node.id}",
                "source": str(node.id),
                "target": str(paper_node.id),
                "relation_type": MANUAL_LINK_RELATION,
                "weight": 1.0,
                "created_at": None,
            })
    return out


def _node_visible_in_curated_graph(node: KnowledgeNode) -> bool:
    """Curated graph view = papers always + concept nodes only when
    promoted. Pending/rejected concept nodes plus hidden ones are
    filtered out. Unknown / legacy types (no longer created but may
    persist in old DBs) are kept visible to avoid silent data loss."""
    if node_is_hidden(node):
        return False
    if (node.node_type or "") == "paper":
        return True
    if not is_concept_candidate_node(node):
        return True
    return promotion_status(node) == PROMOTION_PROMOTED


def get_graph_data(db: Session, *, include_candidates: bool = False) -> dict:
    """Curated graph by default; pass include_candidates=True to also surface
    pending/rejected concept nodes for the review UI."""
    processed_ids = _processed_paper_ids(db)
    all_nodes = db.query(KnowledgeNode).all()
    if include_candidates:
        nodes = [n for n in all_nodes if not node_is_hidden(n)]
    else:
        nodes = [n for n in all_nodes if _node_visible_in_curated_graph(n)]
    node_ids = {n.id for n in nodes}
    edges = [
        e for e in db.query(KnowledgeEdge).all()
        if e.source_id in node_ids and e.target_id in node_ids
    ]
    # Post-W3.2: node ids are UUID strings, not ints. Coerce both sides
    # to strings before comparison so this works whether the synthetic
    # edge factory produces ints (legacy) or strings (current).
    str_node_ids = {str(nid) for nid in node_ids}
    synthetic_edges = [
        edge for edge in _manual_synthetic_edges(nodes)
        if str(edge["source"]) in str_node_ids and str(edge["target"]) in str_node_ids
    ]
    return {
        "nodes": [_serialize_graph_node(n, processed_ids) for n in nodes],
        "edges": [
            {
                "id": str(e.id),
                "source": str(e.source_id),
                "target": str(e.target_id),
                "relation_type": e.relation_type,
                "weight": e.weight,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in edges
        ] + synthetic_edges,
    }


def get_hidden_graph_nodes(db: Session) -> list[dict]:
    processed_ids = _processed_paper_ids(db)
    hidden_nodes = [
        node for node in db.query(KnowledgeNode).all()
        if node_is_hidden(node) and (node.node_type or "") != "paper"
    ]
    paper_categories = _paper_category_by_id(
        db,
        {
            pid
            for node in hidden_nodes
            for pid in normalize_source_paper_ids(node.source_paper_ids)
        },
    )
    hidden_nodes.sort(key=lambda node: ((node.node_type or ""), (node.title or "").lower()))
    serialized = []
    for node in hidden_nodes:
        data = _serialize_graph_node(node, processed_ids)
        data["category"] = _majority_category(
            normalize_source_paper_ids(node.source_paper_ids),
            paper_categories,
        )
        serialized.append(data)
    return serialized


def get_node_detail_data(db: Session, node_id: str) -> Optional[dict]:
    node = db.query(KnowledgeNode).filter(KnowledgeNode.id == node_id).first()
    if not node:
        return None

    processed_ids = _processed_paper_ids(db)
    visible_nodes = [n for n in db.query(KnowledgeNode).all() if not node_is_hidden(n) or n.id == node.id]
    node_map = {n.id: n for n in visible_nodes}

    stored_edges = [
        e for e in db.query(KnowledgeEdge).all()
        if (e.source_id == node.id and e.target_id in node_map)
        or (e.target_id == node.id and e.source_id in node_map)
    ]
    synthetic_edges = [
        e for e in _manual_synthetic_edges(visible_nodes)
        if e["source"] == str(node.id) or e["target"] == str(node.id)
    ]

    connected_ids = set()
    for edge in stored_edges:
        if edge.source_id == node.id:
            connected_ids.add(edge.target_id)
        else:
            connected_ids.add(edge.source_id)
    # _manual_synthetic_edges produces str(node.id) for both endpoints
    # (line ~951–952). Pre-W3.2 those happened to look like ints in
    # disguise — int() worked. After UUID migration the cast blows up;
    # comparing as strings is the right thing.
    node_id_str = str(node.id)
    for edge in synthetic_edges:
        source = str(edge["source"])
        target = str(edge["target"])
        connected_ids.add(target if source == node_id_str else source)

    linked_papers = []
    for paper in db.query(Paper).filter(Paper.id.in_(normalize_source_paper_ids(node.source_paper_ids))).all():
        linked_papers.append({
            "id": paper.id,
            "title": paper.title or paper.filename,
            "filename": paper.filename,
            "processed": bool(paper.processed),
        })
    linked_papers.sort(key=lambda item: item["id"])

    return {
        **_serialize_graph_node(node, processed_ids),
        "connected_nodes": [
            {
                "id": n.id,
                "title": n.title,
                "node_type": n.node_type,
                "origin": node_origin(n),
            }
            for n in (node_map[cid] for cid in connected_ids if cid in node_map and cid != node.id)
        ],
        "edges": [
            {
                "id": e.id,
                "source": e.source_id,
                "target": e.target_id,
                "relation_type": e.relation_type,
                "weight": e.weight,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in stored_edges
        ] + [
            {
                "id": e["id"],
                # _manual_synthetic_edges endpoints are str(node.id) (UUIDs
                # post-migration); int() blows up on them. Keep as strings,
                # consistent with stored_edges + the connected_ids handling
                # above.
                "source": str(e["source"]),
                "target": str(e["target"]),
                "relation_type": e["relation_type"],
                "weight": e["weight"],
                "created_at": e.get("created_at"),
            }
            for e in synthetic_edges
        ],
        "linked_papers": linked_papers,
        "can_hide": node.node_type != "paper",
        "can_edit": node_origin(node) == MANUAL_NODE_ORIGIN,
    }
