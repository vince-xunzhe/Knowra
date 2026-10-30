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
MAX_TEMPORAL_REPAIR_CALLS = 2
_REFERENCE_HEADING = re.compile(r"^(?:references|bibliography|参考文献)\b", re.IGNORECASE)
_TEMPORAL_MARKERS = ("演进", "变化", "经历", "发展", "evolution", "over time", "history")


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


def _candidate_papers(
    retriever: HybridClaimRetriever,
    claims: Sequence[dict[str, Any]],
    *,
    question: str,
) -> tuple[list[str], dict[str, int], bool]:
    ranked = list(dict.fromkeys(item["paper_id"] for item in claims))
    years = retriever.paper_years(ranked)
    temporal = any(marker in question.casefold() for marker in _TEMPORAL_MARKERS)
    if not temporal or len(set(years.values())) < 2:
        return ranked[:MAX_PAPERS], years, temporal
    dated = [paper_id for paper_id in ranked if paper_id in years]
    earliest = min(dated, key=lambda paper_id: (years[paper_id], ranked.index(paper_id)))
    latest = max(dated, key=lambda paper_id: (years[paper_id], -ranked.index(paper_id)))
    selected = [earliest]
    if latest != earliest:
        selected.append(latest)
    for paper_id in ranked:
        if paper_id not in selected:
            selected.append(paper_id)
        if len(selected) == min(5, MAX_PAPERS):
            break
    return selected, years, temporal


def _temporal_endpoint_papers(
    papers: Sequence[str], years: dict[str, int]
) -> list[str]:
    dated = [paper_id for paper_id in papers if paper_id in years]
    if len({years[paper_id] for paper_id in dated}) < 2:
        return []
    earliest = min(dated, key=lambda paper_id: (years[paper_id], papers.index(paper_id)))
    latest = max(dated, key=lambda paper_id: (years[paper_id], -papers.index(paper_id)))
    return [earliest] if earliest == latest else [earliest, latest]


def _temporal_evidence_supplements(
    retriever: HybridClaimRetriever,
    queries: Sequence[str],
    claims: Sequence[dict[str, Any]],
    papers: Sequence[str],
    years: dict[str, int],
    evidence_ranked: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Reserve evidence only when a relevant temporal endpoint really matches.

    A global top-10 window can be filled by one recent paper even after the claim
    stage identified an older relevant source.  At most one paper-filtered lookup
    per missing endpoint repairs that ranking loss.  Empty or low-quality results
    stay empty, so this cannot manufacture chronological coverage.
    """
    represented = {
        item["paper_id"] for item in _diverse(evidence_ranked, MAX_EVIDENCE)
    }
    claim_routes: dict[str, list[int]] = {}
    for claim in claims:
        claim_routes.setdefault(claim["paper_id"], []).extend(claim.get("route_indexes", []))
    supplements: list[dict[str, Any]] = []
    calls = 0
    for paper_id in _temporal_endpoint_papers(papers, years):
        if paper_id in represented or calls >= MAX_TEMPORAL_REPAIR_CALLS:
            continue
        route_indexes = list(dict.fromkeys(claim_routes.get(paper_id, [])))
        if not route_indexes:
            continue
        route_index = route_indexes[0]
        result = retriever.search(
            queries[route_index], kinds=["evidence"], paper_ids=[paper_id], limit=10
        )
        calls += 1
        match = next((item for item in result["results"] if _quality_evidence(item)), None)
        if match is not None:
            supplements.append(
                {
                    **match,
                    "route_indexes": [route_index],
                    "route_score": 1 / (RRF_K + 1),
                }
            )
            represented.add(paper_id)
    return supplements, calls


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
    papers, years, temporal = _candidate_papers(
        retriever, claims, question=question
    )
    evidence_ranked, evidence_calls = ([], 0)
    if papers:
        evidence_ranked, evidence_calls = _ranked_routes(
            retriever, normalized, kinds=["evidence"], paper_ids=papers
        )
    supplements: list[dict[str, Any]] = []
    repair_calls = 0
    if temporal and papers:
        supplements, repair_calls = _temporal_evidence_supplements(
            retriever, normalized, claims, papers, years, evidence_ranked
        )
    supplement_ids = {item["id"] for item in supplements}
    evidence = _diverse(
        [*supplements, *[item for item in evidence_ranked if item["id"] not in supplement_ids]],
        MAX_EVIDENCE,
    )
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
            "temporal_intent": temporal,
            "candidate_years": sorted({years[paper] for paper in papers if paper in years}),
            "evidence_years": sorted({years[item["paper_id"]] for item in evidence if item["paper_id"] in years}),
        },
        "budget": {
            "search_calls": claim_calls + evidence_calls + repair_calls,
            "max_search_calls": MAX_QUERY_ROUTES * 2 + MAX_TEMPORAL_REPAIR_CALLS,
            "elapsed_ms": round((perf_counter() - started) * 1000, 3),
        },
    }
