"""Privacy-safe knowledge-graph baseline collection for architecture phases.

The collector deliberately emits aggregate counts, schema names, and latency
statistics only. It never serializes paper titles, node labels, raw LLM output,
wiki bodies, identifiers, prompts, or credentials.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import time
from collections import Counter, deque
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable


BASELINE_SCHEMA_VERSION = 1
AUTO_CONCEPT_NODE_TYPES = {"technique", "dataset", "problem_area", "concept"}
LATENCY_TASKS = ("paper_extract", "wiki_compile", "ask_agent")

# Frozen Phase-0 response contract. Later phases may add optional fields, but
# removing or renaming one of these requires an explicit compatibility change.
GRAPH_RESPONSE_CONTRACT = {
    "top_level_required": ["nodes", "edges"],
    "node_required": [
        "id",
        "title",
        "content",
        "node_type",
        "origin",
        "hidden",
        "concept_candidate",
        "publishable_concept",
        "promotion_status",
        "promoted_by",
        "promotion_reason",
        "last_promotion_eval_at",
        "tags",
        "source_paper_ids",
        "paper_id",
        "concept_id",
        "created_at",
    ],
    "edge_required": [
        "id",
        "source",
        "target",
        "relation_type",
        "weight",
        "created_at",
    ],
}


def _connect_readonly(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    if not _table_exists(conn, table):
        return []
    return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")]


def _rounded(value: float) -> float:
    return round(float(value), 3)


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return _rounded(ordered[lower] * (1 - fraction) + ordered[upper] * fraction)


def _latency_summary(values: Iterable[float]) -> dict[str, Any]:
    samples = [float(value) for value in values]
    if not samples:
        return {
            "samples": 0,
            "mean_ms": None,
            "p50_ms": None,
            "p95_ms": None,
            "min_ms": None,
            "max_ms": None,
        }
    return {
        "samples": len(samples),
        "mean_ms": _rounded(sum(samples) / len(samples)),
        "p50_ms": _percentile(samples, 0.50),
        "p95_ms": _percentile(samples, 0.95),
        "min_ms": _rounded(min(samples)),
        "max_ms": _rounded(max(samples)),
    }


def _component_metrics(node_ids: set[str], edges: list[tuple[str, str]]) -> dict[str, int]:
    adjacency: dict[str, set[str]] = {node_id: set() for node_id in node_ids}
    for source, target in edges:
        if source not in adjacency or target not in adjacency:
            continue
        adjacency[source].add(target)
        adjacency[target].add(source)

    seen: set[str] = set()
    sizes: list[int] = []
    for node_id in sorted(node_ids):
        if node_id in seen:
            continue
        queue = deque([node_id])
        seen.add(node_id)
        size = 0
        while queue:
            current = queue.popleft()
            size += 1
            for neighbor in adjacency[current]:
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
        sizes.append(size)
    return {
        "connected_components": len(sizes),
        "largest_component_nodes": max(sizes, default=0),
    }


def _scope_metrics(node_ids: set[str], edges: list[tuple[str, str]]) -> dict[str, Any]:
    valid = [(source, target) for source, target in edges if source in node_ids and target in node_ids]
    incident: Counter[str] = Counter()
    for source, target in valid:
        incident[source] += 1
        incident[target] += 1
    node_count = len(node_ids)
    return {
        "nodes": node_count,
        "edges": len(valid),
        "orphan_nodes": sum(1 for node_id in node_ids if incident[node_id] == 0),
        "average_degree": _rounded((2 * len(valid) / node_count) if node_count else 0.0),
        **_component_metrics(node_ids, valid),
    }


def collect_graph_metrics(database_path: Path) -> dict[str, Any]:
    """Collect aggregate graph, schema, migration, and model-call metrics."""
    with closing(_connect_readonly(database_path)) as conn:
        nodes = [
            {
                "id": str(row["id"]),
                "node_type": str(row["node_type"] or ""),
                "hidden": bool(row["hidden"]),
                "promotion_status": str(row["promotion_status"] or ""),
            }
            for row in conn.execute(
                "SELECT id, node_type, hidden, promotion_status FROM knowledge_nodes"
            )
        ]
        raw_edges = [
            (
                str(row["source_id"]),
                str(row["target_id"]),
                str(row["relation_type"] or "related"),
            )
            for row in conn.execute(
                "SELECT source_id, target_id, relation_type FROM knowledge_edges"
            )
        ]

        all_ids = {node["id"] for node in nodes}
        edge_pairs = [(source, target) for source, target, _ in raw_edges]
        curated_ids = {
            node["id"]
            for node in nodes
            if not node["hidden"]
            and (
                node["node_type"] == "paper"
                or node["node_type"] not in AUTO_CONCEPT_NODE_TYPES
                or node["promotion_status"] == "promoted"
            )
        }
        edge_keys = Counter(raw_edges)
        broken_edges = sum(
            1 for source, target, _ in raw_edges if source not in all_ids or target not in all_ids
        )
        self_loops = sum(1 for source, target, _ in raw_edges if source == target)
        duplicate_rows = sum(count - 1 for count in edge_keys.values() if count > 1)

        model_latencies: dict[str, Any] = {}
        if _table_exists(conn, "llm_calls"):
            for task in LATENCY_TASKS:
                rows = conn.execute(
                    "SELECT success, latency_ms FROM llm_calls WHERE task=?", (task,)
                ).fetchall()
                successful = [
                    float(row["latency_ms"])
                    for row in rows
                    if bool(row["success"]) and row["latency_ms"] is not None
                ]
                model_latencies[task] = {
                    "calls": len(rows),
                    "succeeded": sum(1 for row in rows if bool(row["success"])),
                    "failed": sum(1 for row in rows if not bool(row["success"])),
                    "zero_latency_samples": sum(1 for value in successful if value == 0),
                    "successful_latency": _latency_summary(successful),
                    "positive_successful_latency": _latency_summary(
                        value for value in successful if value > 0
                    ),
                }

        migration_markers: list[dict[str, str]] = []
        if _table_exists(conn, "_meta"):
            migration_markers = [
                {"key": str(row["key"]), "set_at": str(row["set_at"] or "")}
                for row in conn.execute("SELECT key, set_at FROM _meta ORDER BY key")
            ]

        return {
            "graph": {
                "all": _scope_metrics(all_ids, edge_pairs),
                "curated": _scope_metrics(curated_ids, edge_pairs),
                "node_type_distribution": dict(
                    sorted(Counter(node["node_type"] for node in nodes).items())
                ),
                "promotion_status_distribution": dict(
                    sorted(Counter(node["promotion_status"] for node in nodes).items())
                ),
                "relation_type_distribution": dict(
                    sorted(Counter(relation for _, _, relation in raw_edges).items())
                ),
                "hidden_nodes": sum(1 for node in nodes if node["hidden"]),
                "broken_edge_references": broken_edges,
                "self_loops": self_loops,
                "duplicate_edge_rows": duplicate_rows,
            },
            "model_call_latency": model_latencies,
            "local_schema": {
                "knowledge_nodes": _table_columns(conn, "knowledge_nodes"),
                "knowledge_edges": _table_columns(conn, "knowledge_edges"),
                "llm_calls": _table_columns(conn, "llm_calls"),
            },
            "migration_markers": migration_markers,
        }


def collect_job_latency(tasks_database_path: Path) -> dict[str, Any]:
    if not tasks_database_path.exists():
        return {"available": False, "reason": "tasks database not found", "by_kind": {}}
    with closing(_connect_readonly(tasks_database_path)) as conn:
        if not _table_exists(conn, "jobs"):
            return {"available": False, "reason": "jobs table not found", "by_kind": {}}
        rows = conn.execute(
            "SELECT kind, status, created, updated FROM jobs ORDER BY created"
        ).fetchall()
    grouped: dict[str, list[float]] = {}
    status_counts: dict[str, Counter[str]] = {}
    for row in rows:
        kind = str(row["kind"])
        status = str(row["status"])
        status_counts.setdefault(kind, Counter())[status] += 1
        if status == "completed":
            grouped.setdefault(kind, []).append(
                max(0.0, (float(row["updated"]) - float(row["created"])) * 1000)
            )
    return {
        "available": True,
        "by_kind": {
            kind: {
                "status_counts": dict(sorted(status_counts[kind].items())),
                "completed_duration": _latency_summary(grouped.get(kind, [])),
            }
            for kind in sorted(status_counts)
        },
    }


def benchmark_fts_rebuild(wiki_dir: Path, trials: int = 3) -> dict[str, Any]:
    """Benchmark the real FTS rebuild implementation against a temporary DB."""
    from services import wiki_search

    if trials < 1:
        raise ValueError("trials must be at least 1")
    original_db_path = wiki_search.DB_PATH
    original_wiki_dir = wiki_search.WIKI_DIR
    durations: list[float] = []
    result: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="knowra-fts-baseline-") as tmp:
        try:
            wiki_search.DB_PATH = Path(tmp) / "wiki_search.sqlite"
            wiki_search.WIKI_DIR = wiki_dir.resolve()
            for _ in range(trials):
                started = time.perf_counter()
                result = wiki_search.rebuild_index()
                durations.append((time.perf_counter() - started) * 1000)
        finally:
            wiki_search.DB_PATH = original_db_path
            wiki_search.WIKI_DIR = original_wiki_dir
    return {
        "trials": trials,
        "indexed_documents": int(result.get("indexed", 0)),
        "paper_documents": int(result.get("paper", 0)),
        "concept_documents": int(result.get("concept", 0)),
        "duration": _latency_summary(durations),
        "uses_temporary_database": True,
    }


def fixture_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def collect_cloud_migrations(migrations_dir: Path) -> list[str]:
    if not migrations_dir.is_dir():
        return []
    return sorted(path.name for path in migrations_dir.glob("*.sql"))


def compare_baselines(reference: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Return deterministic deltas for the stable Phase-0 headline metrics."""
    paths = (
        ("graph", "all", "nodes"),
        ("graph", "all", "edges"),
        ("graph", "all", "orphan_nodes"),
        ("graph", "all", "average_degree"),
        ("graph", "curated", "nodes"),
        ("graph", "curated", "edges"),
        ("graph", "broken_edge_references"),
        ("graph", "self_loops"),
        ("graph", "duplicate_edge_rows"),
    )

    def get(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
        value: Any = payload
        for part in path:
            value = value[part]
        return value

    deltas = {}
    for path in paths:
        old = get(reference, path)
        new = get(current, path)
        deltas[".".join(path)] = {"reference": old, "current": new, "delta": new - old}
    old_contract = reference.get("api_contract")
    new_contract = current.get("api_contract")
    return {
        "metric_deltas": deltas,
        "api_contract_changed": old_contract != new_contract,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
