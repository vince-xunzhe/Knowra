"""Deterministic structural analysis for the curated knowledge graph."""
from __future__ import annotations

import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from services.graph_service import get_graph_data
from services.pipeline_manifest import stable_hash


ANALYSIS_VERSION = "topology-baseline-v1"
ANALYSIS_CACHE_PATH = Path(__file__).resolve().parents[2] / "data" / "graph-analysis.json"


def _signature(graph: dict[str, Any]) -> str:
    topology = {
        "nodes": sorted(
            (
                str(node["id"]),
                str(node.get("node_type") or ""),
                str(node.get("promotion_status") or ""),
            )
            for node in graph.get("nodes") or []
        ),
        "edges": sorted(
            (
                min(str(edge["source"]), str(edge["target"])),
                max(str(edge["source"]), str(edge["target"])),
                str(edge.get("relation_type") or "related"),
                round(float(edge.get("weight") or 0.0), 6),
            )
            for edge in graph.get("edges") or []
        ),
    }
    return stable_hash(topology)


def _load_cache(path: Path) -> Optional[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _save_cache(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".graph-analysis-", suffix=".json", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass


def analyze_graph(
    db: Session,
    *,
    use_cache: bool = True,
    cache_path: Optional[Path] = None,
) -> dict[str, Any]:
    graph = get_graph_data(db)
    signature = _signature(graph)
    path = cache_path or ANALYSIS_CACHE_PATH
    cached = _load_cache(path) if use_cache else None
    if (
        cached
        and cached.get("analysis_version") == ANALYSIS_VERSION
        and cached.get("graph_signature") == signature
    ):
        return {**cached, "cache_hit": True}

    nodes = {str(node["id"]): node for node in graph.get("nodes") or []}
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in nodes}
    weighted: dict[str, float] = {node_id: 0.0 for node_id in nodes}
    edge_counts: dict[tuple[str, str], int] = {}
    for edge in graph.get("edges") or []:
        source, target = str(edge["source"]), str(edge["target"])
        if source not in nodes or target not in nodes or source == target:
            continue
        adjacency[source].add(target)
        adjacency[target].add(source)
        weight = float(edge.get("weight") or 0.0)
        weighted[source] += weight
        weighted[target] += weight
        pair = tuple(sorted((source, target)))
        edge_counts[pair] = edge_counts.get(pair, 0) + 1

    components: list[list[str]] = []
    unseen = set(nodes)
    while unseen:
        start = min(unseen)
        queue = [start]
        unseen.remove(start)
        component: list[str] = []
        while queue:
            current = queue.pop(0)
            component.append(current)
            for neighbor in sorted(adjacency[current]):
                if neighbor in unseen:
                    unseen.remove(neighbor)
                    queue.append(neighbor)
        components.append(sorted(component))
    components.sort(key=lambda item: (-len(item), item[0] if item else ""))

    degrees = [len(adjacency[node_id]) for node_id in sorted(nodes)]
    mean_degree = (sum(degrees) / len(degrees)) if degrees else 0.0
    variance = (
        sum((degree - mean_degree) ** 2 for degree in degrees) / len(degrees)
        if degrees
        else 0.0
    )
    superhub_threshold = max(8, int(math.ceil(mean_degree + 3 * math.sqrt(variance))))

    metrics: list[dict[str, Any]] = []
    component_rows: list[dict[str, Any]] = []
    missing_concept_components: list[dict[str, Any]] = []
    component_by_node: dict[str, str] = {}
    for members in components:
        component_id = f"component:{stable_hash(members)[:12]}"
        for node_id in members:
            component_by_node[node_id] = component_id
        member_set = set(members)
        internal_edges = sum(1 for pair in edge_counts if pair[0] in member_set and pair[1] in member_set)
        possible = len(members) * (len(members) - 1) / 2
        papers = [node_id for node_id in members if nodes[node_id].get("node_type") == "paper"]
        concepts = [node_id for node_id in members if nodes[node_id].get("node_type") != "paper"]
        promoted = [
            node_id
            for node_id in concepts
            if nodes[node_id].get("promotion_status") == "promoted"
        ]
        row = {
            "component_id": component_id,
            "node_count": len(members),
            "edge_count": internal_edges,
            "paper_count": len(papers),
            "concept_count": len(concepts),
            "promoted_concept_count": len(promoted),
            "density": round(internal_edges / possible, 6) if possible else 0.0,
            "node_ids": members,
        }
        component_rows.append(row)
        if len(papers) >= 3 and not promoted:
            missing_concept_components.append({
                **row,
                "evidence_node_ids": papers[:20],
                "reason": "component_has_multiple_papers_without_promoted_concept",
            })

    for node_id in sorted(nodes):
        degree = len(adjacency[node_id])
        metrics.append({
            "node_id": node_id,
            "title": nodes[node_id].get("title"),
            "node_type": nodes[node_id].get("node_type"),
            "component_id": component_by_node[node_id],
            "community_id": component_by_node[node_id],
            "degree": degree,
            "weighted_degree": round(weighted[node_id], 6),
            "is_orphan": degree == 0,
            "is_super_hub": degree >= superhub_threshold,
        })

    orphans = [item for item in metrics if item["is_orphan"]]
    super_hubs = sorted(
        (item for item in metrics if item["is_super_hub"]),
        key=lambda item: (-item["degree"], item["node_id"]),
    )
    result = {
        "analysis_version": ANALYSIS_VERSION,
        "graph_signature": signature,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "cache_hit": False,
        "counts": {
            "nodes": len(nodes),
            "edges": len(graph.get("edges") or []),
            "components": len(component_rows),
            "orphans": len(orphans),
            "super_hubs": len(super_hubs),
            "missing_concept_components": len(missing_concept_components),
        },
        "thresholds": {"super_hub_degree": superhub_threshold},
        "nodes": metrics,
        "components": component_rows,
        "findings": {
            "orphans": orphans,
            "super_hubs": super_hubs,
            "components_without_promoted_concepts": missing_concept_components,
        },
    }
    _save_cache(path, result)
    return result
