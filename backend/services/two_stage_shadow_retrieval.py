"""Shared bounded claim→paper→source retrieval for shadow Ask evaluation."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from time import perf_counter
from typing import Any

from services.hybrid_claim_retrieval import HybridClaimRetriever, RRF_K


MAX_QUERY_ROUTES = 4
MAX_CLAIMS = 10
MAX_EVIDENCE = 6
MAX_PAPERS = 8
MAX_TEMPORAL_REPAIR_CALLS = 2
MAX_GLOBAL_EVIDENCE_CALLS = MAX_QUERY_ROUTES
MAX_SOURCE_DRILLDOWN_CALLS = 5
_SOURCE_SECTION_QUERY = "abstract introduction conclusion"
_REFERENCE_HEADING = re.compile(r"^(?:references|bibliography|参考文献)\b", re.IGNORECASE)
_TEMPORAL_MARKERS = ("演进", "变化", "经历", "发展", "evolution", "over time", "history")
_LATIN_TERM = re.compile(r"[a-z][a-z0-9-]{2,}", re.IGNORECASE)
_CJK_TERM = re.compile(r"[\u3400-\u9fff]{2,}")
_GENERIC_ANCHOR_TERMS = {
    "approach", "approaches", "architecture", "architectures", "change", "changes",
    "chain", "chains", "early", "evolution", "improvement", "improvements",
    "learned", "learning", "major", "markov", "mechanism", "mechanisms", "method", "methods",
    "model", "models", "overview", "review", "reverse",
}
_GENERIC_CJK_ANCHORS = {
    "在建模", "建模上", "模上经", "上经历", "经历了",
    "历了哪", "了哪些", "哪些变", "些变化",
}


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


def _normalized_latin_term(term: str) -> str:
    normalized = term.casefold()
    if normalized.endswith("ies") and len(normalized) > 5:
        normalized = normalized[:-3] + "y"
    elif normalized.endswith(("ches", "shes", "xes", "zes", "oes")):
        normalized = normalized[:-2]
    elif (
        normalized.endswith("s")
        and not normalized.endswith(("ss", "us", "is"))
        and len(normalized) > 4
    ):
        normalized = normalized[:-1]
    return normalized


def _primary_anchor_groups(query: str) -> tuple[list[str], list[str]]:
    latin = list(dict.fromkeys(
        normalized
        for term in _LATIN_TERM.findall(query)
        if (normalized := _normalized_latin_term(term)) not in _GENERIC_ANCHOR_TERMS
    ))
    cjk: list[str] = []
    for run in _CJK_TERM.findall(query):
        if run in {"演进", "变化"}:
            continue
        width = 3 if len(run) >= 3 else len(run)
        cjk.extend(run[index:index + width] for index in range(len(run) - width + 1))
    cjk = list(dict.fromkeys(term for term in cjk if term not in _GENERIC_CJK_ANCHORS))
    return latin, cjk


def _evidence_anchor_query(queries: Sequence[str]) -> str:
    primary = queries[0]
    latin, _ = _primary_anchor_groups(primary)
    if latin:
        return primary
    translated = next(
        (query for query in queries[1:] if _primary_anchor_groups(query)[0]), ""
    )
    return f"{primary} {translated}".strip()


def _matches_primary_anchors(query: str, item: dict[str, Any]) -> bool:
    latin, cjk = _primary_anchor_groups(query)
    if not latin and not cjk:
        return True
    text = f"{item.get('title') or ''} {item.get('text') or ''}".casefold()
    text_latin = {_normalized_latin_term(term) for term in _LATIN_TERM.findall(text)}
    latin_matches = sum(term in text_latin for term in latin)
    cjk_matches = sum(term in text for term in cjk)
    return (
        bool(latin) and latin_matches >= min(2, len(latin))
    ) or (
        bool(cjk) and cjk_matches >= min(2, len(cjk))
    )


def _ranked_routes(
    retriever: HybridClaimRetriever,
    queries: Sequence[str],
    *,
    kinds: list[str],
    paper_ids: list[str] | None = None,
    anchor_query: str | None = None,
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
            if anchor_query is not None and not _matches_primary_anchors(anchor_query, item):
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
        if len(selected) == MAX_PAPERS:
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
        item["paper_id"]
        for item in _diverse(evidence_ranked, MAX_EVIDENCE)
        if item.get("selection_mode") != "source_drilldown"
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
        match = next((
            item for item in result["results"]
            if _quality_evidence(item)
            and _matches_primary_anchors(_evidence_anchor_query(queries), item)
        ), None)
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


def _source_drilldown(
    retriever: HybridClaimRetriever, papers: Sequence[str]
) -> tuple[list[dict[str, Any]], int]:
    """Read one bounded overview chunk per claim-selected paper.

    This is a lexical cross-language fallback: paper relevance still comes from
    the anchored claim stage, while the returned material is original source
    text rather than the extracted claim.  It never treats the claim as evidence.
    """
    evidence: list[dict[str, Any]] = []
    calls = 0
    for paper_id in papers[:MAX_SOURCE_DRILLDOWN_CALLS]:
        result = retriever.search(
            _SOURCE_SECTION_QUERY,
            kinds=["evidence"],
            paper_ids=[paper_id],
            limit=10,
        )
        calls += 1
        candidates = [item for item in result["results"] if _quality_evidence(item)]
        match = min(
            candidates,
            key=lambda item: (
                0 if str(item.get("field_path") or "").endswith("/abstract") else 1,
                int((item.get("locator") or {}).get("page_number") or 10**9),
                -int(item.get("text_characters") or len(str(item.get("text") or ""))),
                item["id"],
            ),
            default=None,
        )
        if match is not None:
            evidence.append({
                **match,
                "route_indexes": [],
                "route_score": 0.0,
                "selection_mode": "source_drilldown",
            })
    return evidence, calls


def _corroborated_global_evidence(
    evidence: Sequence[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Keep bounded source rescue that has more than one relevance signal.

    A global source hit may bypass a lossy or truncated claim catalogue, but a
    single hit from the broad primary route is too weak to select a new paper.
    A paper is admitted when the source evidence is repeated, appears through
    multiple routes, or comes from a planned specialised route.
    """
    paper_counts = Counter(item["paper_id"] for item in evidence)
    return [
        {
            **item,
            "selection_mode": "global_evidence_rescue",
        }
        for item in evidence
        if (
            paper_counts[item["paper_id"]] >= 2
            or len(set(item.get("route_indexes") or [])) >= 2
            or any(index > 0 for index in item.get("route_indexes") or [])
        )
    ]


def _coverage_is_incomplete(
    evidence: Sequence[dict[str, Any]],
    *,
    years: dict[str, int],
    temporal: bool,
    required_papers: int,
    required_evidence: int,
) -> bool:
    papers = {item["paper_id"] for item in evidence}
    evidence_years = {years[paper] for paper in papers if paper in years}
    return (
        len(evidence) < required_evidence
        or len(papers) < required_papers
        or (temporal and len(evidence_years) < 2)
    )


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
        retriever,
        normalized,
        kinds=["claim"],
        anchor_query=_evidence_anchor_query(normalized),
    )
    claims = _diverse(claims_ranked, MAX_CLAIMS)
    papers, years, temporal = _candidate_papers(
        retriever, claims, question=question
    )
    evidence_ranked, evidence_calls = ([], 0)
    if papers:
        anchor_query = _evidence_anchor_query(normalized)
        evidence_ranked, evidence_calls = _ranked_routes(
            retriever,
            normalized,
            kinds=["evidence"],
            paper_ids=papers,
            anchor_query=anchor_query,
        )
    supplements: list[dict[str, Any]] = []
    repair_calls = 0
    if temporal and papers:
        supplements, repair_calls = _temporal_evidence_supplements(
            retriever, normalized, claims, papers, years, evidence_ranked
        )
    supplement_ids = {item["id"] for item in supplements}
    preliminary_evidence = _diverse(
        [
            *supplements,
            *[item for item in evidence_ranked if item["id"] not in supplement_ids],
        ],
        MAX_EVIDENCE,
    )
    rescue_calls = 0
    global_evidence_rescue_used = False
    if _coverage_is_incomplete(
        preliminary_evidence,
        years=years,
        temporal=temporal,
        required_papers=required_papers,
        required_evidence=required_evidence,
    ):
        global_ranked, rescue_calls = _ranked_routes(
            retriever,
            normalized,
            kinds=["evidence"],
            anchor_query=_evidence_anchor_query(normalized),
        )
        rescued = _corroborated_global_evidence(global_ranked)
        if rescued:
            existing_ids = {item["id"] for item in evidence_ranked}
            evidence_ranked = [
                *evidence_ranked,
                *[item for item in rescued if item["id"] not in existing_ids],
            ]
            rescued_papers = list(dict.fromkeys(item["paper_id"] for item in rescued))
            years.update(retriever.paper_years(rescued_papers))
            global_evidence_rescue_used = True
    drilldown_calls = 0
    source_drilldown_used = False
    if papers and not evidence_ranked and not supplements:
        evidence_ranked, drilldown_calls = _source_drilldown(retriever, papers)
        source_drilldown_used = bool(evidence_ranked)
    evidence = _diverse(
        [*supplements, *[item for item in evidence_ranked if item["id"] not in supplement_ids]],
        MAX_EVIDENCE,
    )
    anchored_evidence = [
        item for item in evidence if item.get("selection_mode") != "source_drilldown"
    ]
    evidence_papers = {item["paper_id"] for item in anchored_evidence}
    evidence_years = {
        years[item["paper_id"]]
        for item in anchored_evidence
        if item["paper_id"] in years
    }
    temporal_years_sufficient = not temporal or len(evidence_years) >= 2
    answerable = (
        len(anchored_evidence) >= required_evidence
        and len(evidence_papers) >= required_papers
        and temporal_years_sufficient
    )
    return {
        "question": question.strip(),
        "queries": normalized,
        "claims": claims,
        "evidence": evidence,
        "answerable": answerable,
        "coverage": {
            "claim_papers": len(set(item["paper_id"] for item in claims)),
            "evidence_records": len(anchored_evidence),
            "candidate_source_records": len(evidence),
            "evidence_papers": len(evidence_papers),
            "required_papers": required_papers,
            "required_evidence": required_evidence,
            "temporal_intent": temporal,
            "temporal_years_sufficient": temporal_years_sufficient,
            "source_drilldown_used": source_drilldown_used,
            "global_evidence_rescue_used": global_evidence_rescue_used,
            "candidate_years": sorted({years[paper] for paper in papers if paper in years}),
            "evidence_years": sorted(evidence_years),
        },
        "budget": {
            "search_calls": (
                claim_calls + evidence_calls + rescue_calls + drilldown_calls + repair_calls
            ),
            "max_search_calls": (
                MAX_QUERY_ROUTES * 2
                + MAX_GLOBAL_EVIDENCE_CALLS
                + MAX_SOURCE_DRILLDOWN_CALLS
                + MAX_TEMPORAL_REPAIR_CALLS
            ),
            "elapsed_ms": round((perf_counter() - started) * 1000, 3),
        },
    }
