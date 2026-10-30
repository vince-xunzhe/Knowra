"""Shared bounded claim→paper→source retrieval for shadow Ask evaluation."""

from __future__ import annotations

import re
from collections.abc import Sequence
from time import perf_counter
from typing import Any

from services.hybrid_claim_retrieval import HybridClaimRetriever, RRF_K


MAX_QUERY_ROUTES = 4
MAX_CLAIMS = 10
MAX_EVIDENCE = 6
MAX_PAPERS = 8
_REFERENCE_HEADING = re.compile(r"^(?:references|bibliography|参考文献)\b", re.IGNORECASE)


class TwoStageRetrievalError(ValueError):
    pass


def _quality_evidence(item: dict[str, Any]) -> bool:
    field = str(item.get("field_path") or "")
    title = str(item.get("title") or "").strip()
    text = str(item.get("text") or "").strip()
    return (
        len(text) >= 200
        and not field.endswith("/references")
        and not _REFERENCE_HEADING.match(title)
        and not _REFERENCE_HEADING.match(text[:120].strip())
    )


def _ranked_routes(
    retriever: HybridClaimRetriever,
    queries: Sequence[str],
    *,
    kinds: list[str],
    paper_ids: list[str] | None = None,
) -> tuple[list[dict[str, Any]], int]:
    aggregate: dict[str, dict[str, Any]] = {}
    calls = 0
    for route_index, query in enumerate(queries):
        result = retriever.search(
            query, kinds=kinds, paper_ids=paper_ids, limit=10
        )
        calls += 1
        for rank, item in enumerate(result["results"], 1):
            if item["kind"] == "evidence" and not _quality_evidence(item):
                continue
            entry = aggregate.setdefault(
                item["id"], {**item, "route_indexes": [], "route_score": 0.0}
            )
            entry["route_indexes"].append(route_index)
            entry["route_score"] = max(entry["route_score"], 1 / (RRF_K + rank))
    ranked = sorted(
        aggregate.values(),
        key=lambda item: (-item["route_score"], -len(set(item["route_indexes"])), item["id"]),
    )
    return ranked, calls


def _diverse(items: Sequence[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    papers: set[str] = set()
    for item in items:
        if item["paper_id"] not in papers:
            selected.append(item)
            selected_ids.add(item["id"])
            papers.add(item["paper_id"])
        if len(selected) == limit:
            return selected
    for item in items:
        if item["id"] not in selected_ids:
            selected.append(item)
        if len(selected) == limit:
            break
    return selected


def execute_two_stage_shadow_retrieval(
    retriever: HybridClaimRetriever,
    *,
    question: str,
    queries: Sequence[str],
    required_papers: int = 2,
    required_evidence: int = 2,
) -> dict[str, Any]:
    started = perf_counter()
    normalized = list(dict.fromkeys(str(query).strip() for query in queries if str(query).strip()))
    if not question.strip() or not 1 <= len(normalized) <= MAX_QUERY_ROUTES:
        raise TwoStageRetrievalError("invalid question or query route count")
    claims_ranked, claim_calls = _ranked_routes(
        retriever, normalized, kinds=["claim"]
    )
    claims = _diverse(claims_ranked, MAX_CLAIMS)
    papers = list(dict.fromkeys(item["paper_id"] for item in claims))[:MAX_PAPERS]
    evidence_ranked, evidence_calls = ([], 0)
    if papers:
        evidence_ranked, evidence_calls = _ranked_routes(
            retriever, normalized, kinds=["evidence"], paper_ids=papers
        )
    evidence = _diverse(evidence_ranked, MAX_EVIDENCE)
    evidence_papers = {item["paper_id"] for item in evidence}
    answerable = (
        len(evidence) >= required_evidence
        and len(evidence_papers) >= required_papers
    )
    return {
        "question": question.strip(),
        "queries": normalized,
        "claims": claims,
        "evidence": evidence,
        "answerable": answerable,
        "coverage": {
            "claim_papers": len(set(item["paper_id"] for item in claims)),
            "evidence_records": len(evidence),
            "evidence_papers": len(evidence_papers),
            "required_papers": required_papers,
            "required_evidence": required_evidence,
        },
        "budget": {
            "search_calls": claim_calls + evidence_calls,
            "max_search_calls": MAX_QUERY_ROUTES * 2,
            "elapsed_ms": round((perf_counter() - started) * 1000, 3),
        },
    }
