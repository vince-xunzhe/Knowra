from __future__ import annotations

import json
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any, Optional

from sqlalchemy.orm import Session

from models import Paper
from services.graph_service import normalize_source_paper_ids
from services.paper_category_service import (
    PAPER_CATEGORY_OPTIONS,
    PAPER_CATEGORY_OTHER,
    effective_paper_category,
    legacy_classify_paper_category,
    sync_paper_category_fields,
)
from services.wiki_compiler import (
    list_concept_pages,
    list_paper_pages,
    list_publishable_concept_nodes,
)

_CATEGORY_ORDER = list(PAPER_CATEGORY_OPTIONS)
_TYPE_Y_OFFSET = {
    "problem_area": -210,
    "concept": -110,
    "technique": 120,
    "dataset": 220,
}


def _safe_parse(raw: Optional[str]) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _coerce_year(value: Any) -> Optional[int]:
    try:
        year = int(value)
    except (TypeError, ValueError):
        return None
    return year if 1900 <= year <= 2100 else None


def _paper_year(paper: Paper, extraction: dict[str, Any]) -> int:
    year = _coerce_year(extraction.get("year"))
    if year is not None:
        return year
    if paper.processed_at is not None:
        return paper.processed_at.year
    if paper.created_at is not None:
        return paper.created_at.year
    return paper.id


def classify_paper_category(paper: Paper, extraction: dict[str, Any]) -> str:
    return effective_paper_category(paper, extraction or {}) or PAPER_CATEGORY_OTHER


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _paper_node_id(paper_id: int) -> str:
    return f"paper:{paper_id}"


def _concept_node_id(concept_id: int) -> str:
    return f"concept:{concept_id}"


def _category_rank(name: str) -> int:
    try:
        return _CATEGORY_ORDER.index(name)
    except ValueError:
        return len(_CATEGORY_ORDER)


def build_wiki_graph(
    db: Session,
    active_kind: Optional[str] = None,
    active_id: Optional[int] = None,
) -> dict[str, Any]:
    paper_pages = [item for item in list_paper_pages() if isinstance(item.get("paper_id"), int)]
    concept_pages = [
        item for item in list_concept_pages()
        if isinstance(item.get("concept_id"), int)
    ]
    concept_page_by_id: dict[int, dict[str, Any]] = {}
    for item in concept_pages:
        concept_id = item.get("concept_id")
        if isinstance(concept_id, int) and concept_id not in concept_page_by_id:
            concept_page_by_id[concept_id] = item
    concept_nodes = [
        node for node in list_publishable_concept_nodes(db)
        if (node.node_type or "") in {"concept", "technique", "dataset", "problem_area"}
    ]

    if not paper_pages and not concept_nodes:
        return {"nodes": [], "edges": [], "categories": [], "updated_at": datetime.utcnow().isoformat()}

    paper_ids = [int(item["paper_id"]) for item in paper_pages]
    papers = db.query(Paper).filter(Paper.id.in_(paper_ids)).all()
    paper_by_id = {paper.id: paper for paper in papers}

    compiled_papers: list[dict[str, Any]] = []
    changed = False
    for item in paper_pages:
        paper_id = int(item["paper_id"])
        paper = paper_by_id.get(paper_id)
        if not paper:
            continue
        extraction = _safe_parse(paper.raw_llm_response)
        if sync_paper_category_fields(paper, extraction):
            changed = True
        compiled_papers.append({
            "paper_id": paper_id,
            "filename": item["filename"],
            "title": item.get("title") or paper.title or paper.filename,
            "compiled_at": item.get("compiled_at"),
            "year": _paper_year(paper, extraction),
            "category": effective_paper_category(paper, extraction) or legacy_classify_paper_category(paper, extraction),
        })
    if changed and hasattr(db, "commit"):
        db.commit()

    categories_in_use = sorted(
        {paper["category"] for paper in compiled_papers},
        key=_category_rank,
    )
    lane_y = {name: 120 + idx * 330 for idx, name in enumerate(categories_in_use)}

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    paper_positions: dict[int, tuple[float, float]] = {}
    paper_category: dict[int, str] = {}

    for category in categories_in_use:
        category_papers = sorted(
            [paper for paper in compiled_papers if paper["category"] == category],
            key=lambda paper: (
                paper["year"],
                _parse_iso(paper["compiled_at"]).timestamp() if _parse_iso(paper["compiled_at"]) else 0.0,
                paper["paper_id"],
            ),
        )
        y = lane_y[category]
        nodes.append({
            "id": f"group:{category}",
            "kind": "group",
            "title": category,
            "subtitle": f"{len(category_papers)} 篇论文",
            "x": 110,
            "y": y,
            "category": category,
            "active": False,
        })

        previous: Optional[dict[str, Any]] = None
        for idx, paper in enumerate(category_papers):
            x = 320 + idx * 250
            paper_positions[paper["paper_id"]] = (x, y)
            paper_category[paper["paper_id"]] = category
            nodes.append({
                "id": _paper_node_id(paper["paper_id"]),
                "kind": "paper",
                "title": paper["title"],
                "subtitle": f'{paper["year"]} · {category}',
                "year": paper["year"],
                "filename": paper["filename"],
                "page_kind": "papers",
                "paper_id": paper["paper_id"],
                "category": category,
                "compiled_at": paper["compiled_at"],
                "x": x,
                "y": y,
                "active": active_kind == "paper" and active_id == paper["paper_id"],
            })
            if previous is not None:
                edges.append({
                    "id": f'timeline:{category}:{previous["paper_id"]}:{paper["paper_id"]}',
                    "source": _paper_node_id(previous["paper_id"]),
                    "target": _paper_node_id(paper["paper_id"]),
                    "relation_type": "timeline",
                    "category": category,
                })
            previous = paper

    concept_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for node in concept_nodes:
        concept_id = int(node.id)
        page_meta = concept_page_by_id.get(concept_id) or {}
        linked_papers = [
            pid for pid in normalize_source_paper_ids(node.source_paper_ids)
            if pid in paper_positions
        ]
        if not linked_papers:
            continue

        category_counts = Counter(paper_category[pid] for pid in linked_papers if pid in paper_category)
        category = category_counts.most_common(1)[0][0] if category_counts else "其他"
        avg_x = sum(paper_positions[pid][0] for pid in linked_papers) / len(linked_papers)
        concept_groups[(category, node.node_type or "concept")].append({
            "concept_id": concept_id,
            "title": node.title,
            "filename": page_meta.get("filename"),
            "node_type": node.node_type or "concept",
            "source_paper_ids": linked_papers,
            "compiled_at": page_meta.get("compiled_at"),
            "category": category,
            "avg_x": avg_x,
        })

    for (category, node_type), bucket in concept_groups.items():
        base_y = lane_y.get(category, 120)
        offset = _TYPE_Y_OFFSET.get(node_type, -80)
        sorted_bucket = sorted(bucket, key=lambda item: (item["avg_x"], item["title"].lower()))
        for idx, item in enumerate(sorted_bucket):
            column = idx % 4
            band = idx // 4
            wave_x = (column - 1.5) * 48
            wave_y = band * 54 * (1 if offset >= 0 else -1)
            x = item["avg_x"] + wave_x
            y = base_y + offset + wave_y
            nodes.append({
                "id": _concept_node_id(item["concept_id"]),
                "kind": "concept",
                "title": item["title"],
                "subtitle": f'{node_type} · {len(item["source_paper_ids"])} 篇',
                "filename": item["filename"],
                "page_kind": "concepts" if item["filename"] else None,
                "concept_id": item["concept_id"],
                "node_type": node_type,
                "category": category,
                "compiled_at": item["compiled_at"],
                "x": x,
                "y": y,
                "active": active_kind == "concept" and active_id == item["concept_id"],
            })
            for paper_id in item["source_paper_ids"]:
                edges.append({
                    "id": f'link:{item["concept_id"]}:{paper_id}',
                    "source": _concept_node_id(item["concept_id"]),
                    "target": _paper_node_id(paper_id),
                    "relation_type": "supports",
                    "node_type": node_type,
                    "category": category,
                })

    return {
        "updated_at": datetime.utcnow().isoformat(),
        "categories": [
            {
                "name": category,
                "paper_count": sum(1 for paper in compiled_papers if paper["category"] == category),
                "concept_count": sum(
                    1 for node in nodes if node.get("kind") == "concept" and node.get("category") == category
                ),
            }
            for category in categories_in_use
        ],
        "nodes": nodes,
        "edges": edges,
    }
