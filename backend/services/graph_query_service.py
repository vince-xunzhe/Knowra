"""Deterministic, bounded, read-only queries over Knowra's curated graph."""
from __future__ import annotations

import re
from collections import deque
from time import perf_counter
from typing import Any, Iterable, Optional

from sqlalchemy.orm import Session, defer

from models import KnowledgeEdge, KnowledgeNode
from services.edge_provenance import serialize_edge_provenance

MAX_RESULTS = 50
MAX_DEPTH = 4
MAX_QUERY_MS = 500
MAX_TAGS_PER_NODE = 12
MAX_SOURCES_PER_NODE = 20
MAX_EVIDENCE_CHARS = 500
MAX_PROVENANCE_ITEMS = 10
_TOKEN_RE = re.compile(r"[\w\-]+", re.UNICODE)
_CONCEPT_CANDIDATE_TYPES = {"technique", "dataset", "problem_area", "concept"}


class GraphQueryError(ValueError):
    pass


def _limit(value: Optional[int], default: int = 20) -> int:
    return max(1, min(int(value or default), MAX_RESULTS))


def _depth(value: Optional[int], default: int = 1) -> int:
    return max(1, min(int(value or default), MAX_DEPTH))


def _visible(node: KnowledgeNode, include_diagnostics: bool = False) -> bool:
    if include_diagnostics:
        return True
    if bool(node.hidden):
        return False
    return (
        node.node_type == "paper"
        or node.node_type not in _CONCEPT_CANDIDATE_TYPES
        or node.promotion_status == "promoted"
    )


def _node_json(node: KnowledgeNode) -> dict[str, Any]:
    return {
        "id": str(node.id),
        "title": (node.title or "")[:240],
        "node_type": node.node_type or "",
        "promotion_status": node.promotion_status or "pending",
        "hidden": bool(node.hidden),
        "tags": [str(item)[:120] for item in (node.tags or [])[:MAX_TAGS_PER_NODE]],
        "source_paper_ids": [
            str(item) for item in (node.source_paper_ids or [])[:MAX_SOURCES_PER_NODE]
        ],
    }


def _edge_json(edge: KnowledgeEdge) -> dict[str, Any]:
    provenance = serialize_edge_provenance(edge)
    if provenance.get("evidence"):
        provenance["evidence"] = str(provenance["evidence"])[:MAX_EVIDENCE_CHARS]
    metadata = provenance.get("metadata")
    if isinstance(metadata, dict) and isinstance(metadata.get("provenance"), list):
        items = []
        for raw in metadata["provenance"][:MAX_PROVENANCE_ITEMS]:
            if not isinstance(raw, dict):
                continue
            item = dict(raw)
            if item.get("evidence"):
                item["evidence"] = str(item["evidence"])[:MAX_EVIDENCE_CHARS]
            items.append(item)
        provenance["metadata"] = {**metadata, "provenance": items}
    return {
        "id": str(edge.id),
        "source": str(edge.source_id),
        "target": str(edge.target_id),
        "relation_type": edge.relation_type or "related",
        "weight": float(edge.weight or 0.0),
        **provenance,
    }


def _snapshot(db: Session, include_diagnostics: bool = False):
    nodes = _visible_nodes(db, include_diagnostics)
    edges = [
        edge
        for edge in db.query(KnowledgeEdge).all()
        if str(edge.source_id) in nodes and str(edge.target_id) in nodes
    ]
    edges.sort(key=lambda edge: (str(edge.relation_type or ""), str(edge.id)))
    adjacency: dict[str, list[tuple[str, KnowledgeEdge]]] = {node_id: [] for node_id in nodes}
    for edge in edges:
        source, target = str(edge.source_id), str(edge.target_id)
        adjacency[source].append((target, edge))
        adjacency[target].append((source, edge))
    for node_id in adjacency:
        adjacency[node_id].sort(key=lambda item: item[0])
    return nodes, edges, adjacency


def _visible_nodes(db: Session, include_diagnostics: bool = False):
    return {
        str(node.id): node
        for node in db.query(KnowledgeNode).options(defer(KnowledgeNode.embedding)).all()
        if _visible(node, include_diagnostics)
    }


def _check_deadline(started: float) -> None:
    if (perf_counter() - started) * 1000 > MAX_QUERY_MS:
        raise GraphQueryError(f"graph query exceeded {MAX_QUERY_MS} ms")


def find_nodes(
    db: Session,
    query: str,
    node_type: Optional[str] = None,
    limit: int = 20,
    include_diagnostics: bool = False,
) -> dict[str, Any]:
    started = perf_counter()
    q = (query or "").strip().casefold()
    if not q:
        raise GraphQueryError("query must not be empty")
    terms = set(_TOKEN_RE.findall(q))
    nodes = _visible_nodes(db, include_diagnostics)
    _check_deadline(started)
    ranked = []
    for node in nodes.values():
        _check_deadline(started)
        if node_type and node.node_type != node_type:
            continue
        title = (node.title or "").casefold()
        tags = [str(tag).casefold() for tag in (node.tags or [])]
        title_terms = set(_TOKEN_RE.findall(title))
        score = 0
        if title == q:
            score = 100
        elif q in title:
            score = 80
        elif q in tags:
            score = 70
        else:
            overlap = len(terms & title_terms)
            tag_overlap = max((len(terms & set(_TOKEN_RE.findall(tag))) for tag in tags), default=0)
            score = overlap * 10 + tag_overlap * 8
        if score:
            ranked.append((score, title, str(node.id), node))
    ranked.sort(key=lambda item: (-item[0], item[1], item[2]))
    result = [_node_json(item[3]) for item in ranked[: _limit(limit)]]
    return {"query": query, "nodes": result, "count": len(result)}


def get_node(db: Session, node_id: str, include_diagnostics: bool = False) -> dict[str, Any]:
    node = db.get(KnowledgeNode, str(node_id))
    if node is None or not _visible(node, include_diagnostics):
        raise GraphQueryError(f"node not found: {node_id}")
    return {"node": _node_json(node)}


def get_neighbors(
    db: Session,
    node_id: str,
    relation_type: Optional[str] = None,
    depth: int = 1,
    limit: int = 20,
    include_diagnostics: bool = False,
) -> dict[str, Any]:
    started = perf_counter()
    nodes, _, adjacency = _snapshot(db, include_diagnostics)
    _check_deadline(started)
    root = str(node_id)
    if root not in nodes:
        raise GraphQueryError(f"node not found: {node_id}")
    max_depth, result_limit = _depth(depth), _limit(limit)
    queue = deque([(root, 0)])
    seen = {root}
    hits: list[tuple[int, str]] = []
    edge_ids: set[str] = set()
    matched_edges: list[KnowledgeEdge] = []
    while queue and len(hits) < result_limit:
        current, current_depth = queue.popleft()
        if current_depth >= max_depth:
            continue
        for neighbor, edge in adjacency[current]:
            _check_deadline(started)
            if relation_type and edge.relation_type != relation_type:
                continue
            if str(edge.id) not in edge_ids:
                edge_ids.add(str(edge.id))
                matched_edges.append(edge)
            if neighbor in seen:
                continue
            seen.add(neighbor)
            hits.append((current_depth + 1, neighbor))
            queue.append((neighbor, current_depth + 1))
            if len(hits) >= result_limit:
                break
    return {
        "root": _node_json(nodes[root]),
        "nodes": [{**_node_json(nodes[node]), "depth": level} for level, node in hits],
        "edges": [_edge_json(edge) for edge in matched_edges],
        "depth": max_depth,
        "count": len(hits),
    }


def shortest_path(
    db: Session,
    source_id: str,
    target_id: str,
    max_depth: int = 4,
    include_diagnostics: bool = False,
) -> dict[str, Any]:
    started = perf_counter()
    nodes, _, adjacency = _snapshot(db, include_diagnostics)
    _check_deadline(started)
    source, target = str(source_id), str(target_id)
    if source not in nodes or target not in nodes:
        raise GraphQueryError("source or target node not found")
    depth_limit = _depth(max_depth, MAX_DEPTH)
    queue = deque([(source, [source], [])])
    seen = {source}
    while queue:
        current, path_nodes, path_edges = queue.popleft()
        _check_deadline(started)
        if current == target:
            return {
                "found": True,
                "nodes": [_node_json(nodes[item]) for item in path_nodes],
                "edges": [_edge_json(edge) for edge in path_edges],
                "hop_count": len(path_edges),
            }
        if len(path_edges) >= depth_limit:
            continue
        for neighbor, edge in adjacency[current]:
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append((neighbor, [*path_nodes, neighbor], [*path_edges, edge]))
    return {"found": False, "nodes": [], "edges": [], "hop_count": None}


def find_shared_neighbors(
    db: Session,
    node_ids: Iterable[str],
    relation_type: Optional[str] = None,
    limit: int = 20,
    include_diagnostics: bool = False,
) -> dict[str, Any]:
    started = perf_counter()
    nodes, _, adjacency = _snapshot(db, include_diagnostics)
    _check_deadline(started)
    requested = [str(item) for item in node_ids]
    if len(requested) < 2 or any(item not in nodes for item in requested):
        raise GraphQueryError("at least two existing node_ids are required")
    neighbor_sets = []
    for node_id in requested:
        neighbor_sets.append({
            neighbor for neighbor, edge in adjacency[node_id]
            if not relation_type or edge.relation_type == relation_type
        })
    shared = sorted(set.intersection(*neighbor_sets))[: _limit(limit)]
    return {"node_ids": requested, "nodes": [_node_json(nodes[item]) for item in shared], "count": len(shared)}


def find_papers_for_concept(db: Session, concept_id: str, limit: int = 20) -> dict[str, Any]:
    result = get_neighbors(db, concept_id, depth=1, limit=MAX_RESULTS)
    papers = [node for node in result["nodes"] if node["node_type"] == "paper"][: _limit(limit)]
    return {"concept_id": str(concept_id), "nodes": papers, "count": len(papers)}


def find_concepts_for_paper(db: Session, paper_id: str, limit: int = 20) -> dict[str, Any]:
    result = get_neighbors(db, paper_id, depth=1, limit=MAX_RESULTS)
    concepts = [node for node in result["nodes"] if node["node_type"] != "paper"][: _limit(limit)]
    return {"paper_id": str(paper_id), "nodes": concepts, "count": len(concepts)}


def explain_edge(db: Session, edge_id: str, include_diagnostics: bool = False) -> dict[str, Any]:
    started = perf_counter()
    nodes, edges, _ = _snapshot(db, include_diagnostics)
    _check_deadline(started)
    edge = next((item for item in edges if str(item.id) == str(edge_id)), None)
    if edge is None:
        raise GraphQueryError(f"edge not found: {edge_id}")
    return {
        "edge": _edge_json(edge),
        "source": _node_json(nodes[str(edge.source_id)]),
        "target": _node_json(nodes[str(edge.target_id)]),
    }
