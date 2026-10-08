"""Canonical graph export, order-independent signatures and graph diffs."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from sqlalchemy.orm import Session

from models import KnowledgeEdge, KnowledgeNode, Paper
from services.edge_provenance import serialize_edge_provenance
from services.graph_service import normalize_source_paper_ids, node_origin
from services.pipeline_manifest import load_manifest, stable_hash


GRAPH_EXPORT_SCHEMA = "knowra.graph.v1"


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _node(node: KnowledgeNode, *, include_embeddings: bool) -> dict[str, Any]:
    value = {
        "id": str(node.id),
        "title": node.title,
        "content": node.content,
        "node_type": node.node_type,
        "origin": node_origin(node),
        "hidden": bool(node.hidden),
        "promotion_status": node.promotion_status,
        "promoted_by": node.promoted_by,
        "promotion_reason": node.promotion_reason,
        "tags": sorted(str(tag) for tag in (node.tags or [])),
        "source_paper_ids": sorted(normalize_source_paper_ids(node.source_paper_ids)),
        "created_at": _iso(node.created_at),
    }
    if include_embeddings:
        value["embedding"] = node.embedding
    return value


def _edge(edge: KnowledgeEdge) -> dict[str, Any]:
    value = serialize_edge_provenance(edge)
    return {
        "id": str(edge.id),
        "source": str(edge.source_id),
        "target": str(edge.target_id),
        "relation_type": str(edge.relation_type or "related"),
        "weight": float(edge.weight or 0.0),
        "origin": value.get("origin"),
        "confidence": value.get("confidence"),
        "source_paper_id": value.get("source_paper_id"),
        "source_field": value.get("source_field"),
        "evidence": value.get("evidence"),
        "metadata": value.get("metadata") or {},
        "extractor_version": value.get("extractor_version"),
        "provenance": value.get("provenance") or [],
        "created_at": _iso(edge.created_at),
    }


def canonical_graph_payload(
    db: Session,
    *,
    include_embeddings: bool = False,
    include_hidden: bool = True,
) -> dict[str, Any]:
    node_query = db.query(KnowledgeNode)
    if not include_hidden:
        node_query = node_query.filter(KnowledgeNode.hidden.is_(False))
    nodes = list(node_query.all())
    node_ids = {str(node.id) for node in nodes}
    edges = [
        edge
        for edge in db.query(KnowledgeEdge).all()
        if str(edge.source_id) in node_ids and str(edge.target_id) in node_ids
    ]
    identities = {}
    for paper_id, in db.query(Paper.id).all():
        identity = load_manifest(str(paper_id)).get("identity") or {}
        if identity:
            identities[str(paper_id)] = identity
    return {
        "schema": GRAPH_EXPORT_SCHEMA,
        "nodes": sorted(
            (_node(node, include_embeddings=include_embeddings) for node in nodes),
            key=lambda item: item["id"],
        ),
        "edges": sorted(
            (_edge(edge) for edge in edges),
            key=lambda item: (
                item["source"],
                item["target"],
                item["relation_type"],
                item["id"],
            ),
        ),
        "paper_identities": dict(sorted(identities.items())),
    }


def graph_signature(payload: Mapping[str, Any]) -> str:
    stable = {
        "schema": payload.get("schema"),
        "nodes": sorted(
            (payload.get("nodes") or []), key=lambda item: str(item.get("id") or "")
        ),
        "edges": sorted(
            (payload.get("edges") or []),
            key=lambda item: (
                str(item.get("source") or ""),
                str(item.get("target") or ""),
                str(item.get("relation_type") or ""),
                str(item.get("id") or ""),
            ),
        ),
        "paper_identities": dict(sorted((payload.get("paper_identities") or {}).items())),
    }
    return stable_hash(stable)


def export_graph(
    db: Session,
    *,
    include_embeddings: bool = False,
    include_hidden: bool = True,
) -> dict[str, Any]:
    payload = canonical_graph_payload(
        db,
        include_embeddings=include_embeddings,
        include_hidden=include_hidden,
    )
    return {
        **payload,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "signature": graph_signature(payload),
        "counts": {
            "nodes": len(payload["nodes"]),
            "edges": len(payload["edges"]),
            "paper_identities": len(payload["paper_identities"]),
        },
    }


def _without_volatile(item: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in item.items() if key not in {"created_at"}}


def _changed(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    left = _without_volatile(before)
    right = _without_volatile(after)
    fields = sorted(key for key in set(left) | set(right) if left.get(key) != right.get(key))
    return {
        "id": str(after.get("id") or before.get("id")),
        "fields": fields,
        "before": {key: left.get(key) for key in fields},
        "after": {key: right.get(key) for key in fields},
    }


def _change_source(item: Mapping[str, Any]) -> str:
    origin = str(item.get("origin") or "")
    if origin == "manual":
        return "user"
    if origin == "embedding":
        return "embedding"
    if origin in {"explicit", "inferred"} or item.get("extractor_version"):
        return "model_or_rule"
    return "legacy_or_unknown"


def diff_graphs(before: Mapping[str, Any], after: Mapping[str, Any]) -> dict[str, Any]:
    if before.get("schema") != GRAPH_EXPORT_SCHEMA or after.get("schema") != GRAPH_EXPORT_SCHEMA:
        raise ValueError(f"Both snapshots must use schema {GRAPH_EXPORT_SCHEMA}")

    before_nodes = {str(item["id"]): item for item in before.get("nodes") or []}
    after_nodes = {str(item["id"]): item for item in after.get("nodes") or []}
    before_edges = {str(item["id"]): item for item in before.get("edges") or []}
    after_edges = {str(item["id"]): item for item in after.get("edges") or []}

    added_node_ids = sorted(set(after_nodes) - set(before_nodes))
    removed_node_ids = sorted(set(before_nodes) - set(after_nodes))
    added_edge_ids = sorted(set(after_edges) - set(before_edges))
    removed_edge_ids = sorted(set(before_edges) - set(after_edges))
    changed_nodes = [
        _changed(before_nodes[key], after_nodes[key])
        for key in sorted(set(before_nodes) & set(after_nodes))
        if _without_volatile(before_nodes[key]) != _without_volatile(after_nodes[key])
    ]
    changed_edges = [
        _changed(before_edges[key], after_edges[key])
        for key in sorted(set(before_edges) & set(after_edges))
        if _without_volatile(before_edges[key]) != _without_volatile(after_edges[key])
    ]

    added_nodes = [after_nodes[key] for key in added_node_ids]
    removed_nodes = [before_nodes[key] for key in removed_node_ids]
    added_edges = [after_edges[key] for key in added_edge_ids]
    removed_edges = [before_edges[key] for key in removed_edge_ids]
    sources: dict[str, int] = {}
    for item in [*added_nodes, *removed_nodes, *added_edges, *removed_edges]:
        source = _change_source(item)
        sources[source] = sources.get(source, 0) + 1

    before_signature = str(before.get("signature") or graph_signature(before))
    after_signature = str(after.get("signature") or graph_signature(after))
    return {
        "schema": "knowra.graph-diff.v1",
        "before_signature": before_signature,
        "after_signature": after_signature,
        "changed": before_signature != after_signature,
        "counts": {
            "added_nodes": len(added_nodes),
            "removed_nodes": len(removed_nodes),
            "changed_nodes": len(changed_nodes),
            "added_edges": len(added_edges),
            "removed_edges": len(removed_edges),
            "changed_edges": len(changed_edges),
        },
        "added_nodes": added_nodes,
        "removed_nodes": removed_nodes,
        "changed_nodes": changed_nodes,
        "added_edges": added_edges,
        "removed_edges": removed_edges,
        "changed_edges": changed_edges,
        "change_sources": dict(sorted(sources.items())),
    }
