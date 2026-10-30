"""Deterministic, privacy-safe quality evaluation for the Ask evidence pipeline.

The evaluator deliberately separates three questions that production tests used
to conflate:

* did extraction preserve a source-supported claim?
* did the compiled Wiki retain that claim?
* did production retrieval rank a document containing the claim?

Fixtures contain synthetic claim identifiers rather than private paper text.
The real ``wiki_search`` implementation builds and queries a disposable FTS5
index, so retrieval scores exercise production code without touching ``data/``.
"""

from __future__ import annotations

import json
import math
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from time import perf_counter
from typing import Any, Iterator

from services import wiki_search
from services.wiki_compiler import _render_frontmatter


SCHEMA_VERSION = 1
DEFAULT_K = 5
_EVALUATION_LOCK = threading.RLock()


class FixtureError(ValueError):
    """Raised when an evaluation fixture cannot produce trustworthy scores."""


def _as_unique_strings(value: Any, *, field: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise FixtureError(f"{field} must be a list of strings")
    if any(not item.strip() for item in value):
        raise FixtureError(f"{field} contains an empty or whitespace-only value")
    if any(item != item.strip() for item in value):
        raise FixtureError(f"{field} contains leading or trailing whitespace")
    if len(set(value)) != len(value):
        raise FixtureError(f"{field} contains duplicates")
    return value


def validate_fixture(fixture: dict[str, Any]) -> None:
    if fixture.get("fixture_schema_version") != SCHEMA_VERSION:
        raise FixtureError(f"fixture_schema_version must be {SCHEMA_VERSION}")
    documents = fixture.get("documents")
    queries = fixture.get("queries")
    if not isinstance(documents, list) or not documents:
        raise FixtureError("documents must be a non-empty list")
    if not isinstance(queries, list) or not queries:
        raise FixtureError("queries must be a non-empty list")

    document_ids: set[str] = set()
    documents_by_id: dict[str, dict[str, Any]] = {}
    filenames: set[str] = set()
    for index, document in enumerate(documents):
        if not isinstance(document, dict):
            raise FixtureError(f"documents[{index}] must be an object")
        document_id = document.get("id")
        filename = document.get("filename")
        kind = document.get("kind")
        if (
            not isinstance(document_id, str)
            or not document_id.strip()
            or document_id != document_id.strip()
        ):
            raise FixtureError(f"documents[{index}].id must be a non-empty string")
        if document_id in document_ids:
            raise FixtureError(f"duplicate document id: {document_id}")
        document_ids.add(document_id)
        documents_by_id[document_id] = document
        if not isinstance(filename, str) or not filename.endswith(".md"):
            raise FixtureError(f"documents[{index}].filename must end in .md")
        if Path(filename).name != filename or filename in filenames:
            raise FixtureError(f"invalid or duplicate filename: {filename}")
        filenames.add(filename)
        if kind not in {"paper", "concept"}:
            raise FixtureError(f"documents[{index}].kind must be paper or concept")
        for field in ("title", "body"):
            if not isinstance(document.get(field), str) or not document[field].strip():
                raise FixtureError(f"documents[{index}].{field} must be non-empty")
        _as_unique_strings(
            document.get("aliases", []), field=f"documents[{index}].aliases"
        )
        claims = {
            field: set(
                _as_unique_strings(
                    document.get(field, []), field=f"documents[{index}].{field}"
                )
            )
            for field in ("source_claim_ids", "extracted_claim_ids", "wiki_claim_ids")
        }
        unsupported_extraction = claims["extracted_claim_ids"] - claims["source_claim_ids"]
        if unsupported_extraction:
            raise FixtureError(
                f"documents[{index}] extraction claims lack source support: "
                f"{sorted(unsupported_extraction)}"
            )
        unsupported_wiki = claims["wiki_claim_ids"] - claims["extracted_claim_ids"]
        if unsupported_wiki:
            raise FixtureError(
                f"documents[{index}] Wiki claims lack extraction support: "
                f"{sorted(unsupported_wiki)}"
            )

    query_ids: set[str] = set()
    for index, query in enumerate(queries):
        if not isinstance(query, dict):
            raise FixtureError(f"queries[{index}] must be an object")
        query_id = query.get("id")
        if (
            not isinstance(query_id, str)
            or not query_id.strip()
            or query_id != query_id.strip()
        ):
            raise FixtureError(f"queries[{index}].id must be a non-empty string")
        if query_id in query_ids:
            raise FixtureError(f"duplicate query id: {query_id}")
        query_ids.add(query_id)
        if not isinstance(query.get("question"), str) or not query["question"].strip():
            raise FixtureError(f"queries[{index}].question must be non-empty")
        relevant = _as_unique_strings(
            query.get("relevant_document_ids", []),
            field=f"queries[{index}].relevant_document_ids",
        )
        missing = set(relevant) - document_ids
        if missing:
            raise FixtureError(
                f"queries[{index}] references missing documents: {sorted(missing)}"
            )
        should_answer = query.get("should_answer")
        if not isinstance(should_answer, bool):
            raise FixtureError(f"queries[{index}].should_answer must be boolean")
        if should_answer and not relevant:
            raise FixtureError(
                f"queries[{index}] is answerable but has no relevant documents"
            )
        if not should_answer and relevant:
            raise FixtureError(
                f"queries[{index}] is unanswerable but has relevant documents"
            )
        required_claims = _as_unique_strings(
            query.get("required_claim_ids", []),
            field=f"queries[{index}].required_claim_ids",
        )
        k = query.get("k", DEFAULT_K)
        if isinstance(k, bool) or not isinstance(k, int) or k < 1 or k > 100:
            raise FixtureError(f"queries[{index}].k must be between 1 and 100")
        if should_answer and not required_claims:
            raise FixtureError(
                f"queries[{index}] is answerable but has no required claims"
            )
        if not should_answer and required_claims:
            raise FixtureError(
                f"queries[{index}] is unanswerable but has required claims"
            )
        if should_answer:
            source_claims: set[str] = set()
            for document_id in relevant:
                source_claims.update(documents_by_id[document_id]["source_claim_ids"])
            missing_source_claims = set(required_claims) - source_claims
            if missing_source_claims:
                raise FixtureError(
                    f"queries[{index}] required claims lack relevant source support: "
                    f"{sorted(missing_source_claims)}"
                )


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def _ranking_scores(
    ranked_ids: list[str], relevant_ids: list[str], *, k: int
) -> dict[str, float | int]:
    ranked = ranked_ids[:k]
    relevant = set(relevant_ids)
    relevance = [1 if document_id in relevant else 0 for document_id in ranked]
    hits = sum(relevance)
    first_rank = next(
        (rank for rank, value in enumerate(relevance, start=1) if value),
        None,
    )
    dcg = sum(
        value / math.log2(rank + 1)
        for rank, value in enumerate(relevance, start=1)
    )
    ideal_hits = min(len(relevant), k)
    ideal_dcg = sum(1 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return {
        "retrieved": len(ranked),
        "relevant_retrieved": hits,
        "recall_at_k": round(hits / len(relevant), 6) if relevant else 0.0,
        "precision_at_k": round(hits / k, 6),
        "precision_in_returned": round(hits / len(ranked), 6) if ranked else 0.0,
        "reciprocal_rank": round(1 / first_rank, 6) if first_rank else 0.0,
        "ndcg_at_k": round(dcg / ideal_dcg, 6) if ideal_dcg else 0.0,
    }


def _claim_coverage(
    document_ids: list[str],
    documents: dict[str, dict[str, Any]],
    required_claim_ids: list[str],
    field: str,
) -> dict[str, Any]:
    required = set(required_claim_ids)
    available: set[str] = set()
    for document_id in document_ids:
        available.update(documents[document_id].get(field, []))
    covered = sorted(required & available)
    missing = sorted(required - available)
    return {
        "required": len(required),
        "covered": len(covered),
        "coverage": _ratio(len(covered), len(required)),
        "covered_claim_ids": covered,
        "missing_claim_ids": missing,
    }


def _write_document(root: Path, document: dict[str, Any]) -> None:
    subdir = "papers" if document["kind"] == "paper" else "concepts"
    path = root / subdir / document["filename"]
    path.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "title": document["title"],
        "aliases": document.get("aliases", []),
        "slug": Path(document["filename"]).stem,
        "compiled_at": "2026-10-30T00:00:00+00:00",
    }
    path.write_text(
        _render_frontmatter(meta) + f"\n# {document['title']}\n\n{document['body'].strip()}\n",
        encoding="utf-8",
    )


@contextmanager
def _disposable_search_index(
    documents: list[dict[str, Any]],
) -> Iterator[dict[str, str]]:
    old_db_path = wiki_search.DB_PATH
    old_wiki_dir = wiki_search.WIKI_DIR
    old_entity_cache = wiki_search._ENTITY_CACHE
    with tempfile.TemporaryDirectory(prefix="knowra-ask-quality-") as temporary:
        root = Path(temporary)
        wiki_dir = root / "wiki"
        db_path = root / "wiki_search.sqlite"
        for document in documents:
            _write_document(wiki_dir, document)
        try:
            wiki_search.DB_PATH = db_path
            wiki_search.WIKI_DIR = wiki_dir
            wiki_search._clear_entity_cache()
            wiki_search.rebuild_index()
            yield {document["filename"]: document["id"] for document in documents}
        finally:
            wiki_search.DB_PATH = old_db_path
            wiki_search.WIKI_DIR = old_wiki_dir
            wiki_search._ENTITY_CACHE = old_entity_cache


def _evaluate_strategy(
    fixture: dict[str, Any],
    *,
    strategy: str,
    filename_to_id: dict[str, str],
) -> dict[str, Any]:
    documents = {document["id"]: document for document in fixture["documents"]}
    cases: list[dict[str, Any]] = []
    latencies: list[float] = []
    for query in fixture["queries"]:
        started = perf_counter()
        if strategy == "strict":
            hits = wiki_search.search(query["question"], limit=query.get("k", DEFAULT_K))
            mode = "strict"
            attempts = 1
        elif strategy == "production":
            result = wiki_search.search_with_fallback(
                query["question"], limit=query.get("k", DEFAULT_K)
            )
            hits = result["hits"]
            mode = result["mode"]
            attempts = len(result["attempts"])
        else:
            raise ValueError(f"unknown strategy: {strategy}")
        elapsed_ms = (perf_counter() - started) * 1000
        latencies.append(elapsed_ms)
        ranked_ids = [
            filename_to_id[hit["filename"]]
            for hit in hits
            if hit.get("filename") in filename_to_id
        ]
        answerable = query["should_answer"]
        case: dict[str, Any] = {
            "id": query["id"],
            "category": query.get("category", "unspecified"),
            "should_answer": answerable,
            "mode": mode,
            "attempts": attempts,
            "ranked_document_ids": ranked_ids,
            "latency_ms": round(elapsed_ms, 3),
        }
        if answerable:
            case["ranking"] = _ranking_scores(
                ranked_ids,
                query["relevant_document_ids"],
                k=query.get("k", DEFAULT_K),
            )
            case["retrieved_wiki_claim_coverage"] = _claim_coverage(
                ranked_ids,
                documents,
                query.get("required_claim_ids", []),
                "wiki_claim_ids",
            )
        else:
            case["abstained"] = not ranked_ids
        cases.append(case)

    answerable_cases = [case for case in cases if case["should_answer"]]
    no_answer_cases = [case for case in cases if not case["should_answer"]]
    latency_sorted = sorted(latencies)
    p95_index = max(0, math.ceil(len(latency_sorted) * 0.95) - 1)
    return {
        "strategy": strategy,
        "query_count": len(cases),
        "answerable_count": len(answerable_cases),
        "no_answer_count": len(no_answer_cases),
        "metrics": {
            "mean_recall_at_k": _mean(
                [case["ranking"]["recall_at_k"] for case in answerable_cases]
            ),
            "mean_precision_at_k": _mean(
                [case["ranking"]["precision_at_k"] for case in answerable_cases]
            ),
            "mean_reciprocal_rank": _mean(
                [case["ranking"]["reciprocal_rank"] for case in answerable_cases]
            ),
            "mean_ndcg_at_k": _mean(
                [case["ranking"]["ndcg_at_k"] for case in answerable_cases]
            ),
            "mean_retrieved_wiki_claim_coverage": _mean(
                [
                    case["retrieved_wiki_claim_coverage"]["coverage"]
                    for case in answerable_cases
                    if case["retrieved_wiki_claim_coverage"]["coverage"] is not None
                ]
            ),
            "no_answer_accuracy": _mean(
                [1.0 if case["abstained"] else 0.0 for case in no_answer_cases]
            ),
        },
        "latency_ms": {
            "scope": "diagnostic query wall time; not a stable benchmark",
            "mean": round(sum(latencies) / len(latencies), 3),
            "p50": round(latency_sorted[(len(latency_sorted) - 1) // 2], 3),
            "p95": round(latency_sorted[p95_index], 3),
            "max": round(max(latencies), 3),
        },
        "cases": cases,
    }


def _oracle_report(fixture: dict[str, Any]) -> dict[str, Any]:
    documents = {document["id"]: document for document in fixture["documents"]}
    cases = []
    for query in fixture["queries"]:
        if not query["should_answer"]:
            continue
        relevant = query["relevant_document_ids"]
        cases.append(
            {
                "id": query["id"],
                "relevant_document_count": len(relevant),
                "source_claim_upper_bound": _claim_coverage(
                    relevant,
                    documents,
                    query.get("required_claim_ids", []),
                    "source_claim_ids",
                ),
                "extraction_claim_upper_bound": _claim_coverage(
                    relevant,
                    documents,
                    query.get("required_claim_ids", []),
                    "extracted_claim_ids",
                ),
                "wiki_claim_upper_bound": _claim_coverage(
                    relevant,
                    documents,
                    query.get("required_claim_ids", []),
                    "wiki_claim_ids",
                ),
            }
        )
    return {
        "meaning": (
            "Upper bounds assume a perfect retriever supplies every annotated relevant "
            "document. Gaps therefore precede ranking and must not be blamed on search."
        ),
        "cases": cases,
    }


def evaluate_fixture(fixture: dict[str, Any]) -> dict[str, Any]:
    """Evaluate production Wiki retrieval and layer-specific oracle bounds."""
    validate_fixture(fixture)
    with _EVALUATION_LOCK:
        with _disposable_search_index(fixture["documents"]) as filename_to_id:
            strict = _evaluate_strategy(
                fixture, strategy="strict", filename_to_id=filename_to_id
            )
            production = _evaluate_strategy(
                fixture, strategy="production", filename_to_id=filename_to_id
            )
    return {
        "report_schema_version": 1,
        "fixture_schema_version": fixture["fixture_schema_version"],
        "dataset": fixture.get("dataset", "unspecified"),
        "privacy_declaration": fixture.get("privacy", "unspecified"),
        "mode": "offline_deterministic_production_fts",
        "scope": {
            "measured": [
                "source/extraction/Wiki claim-label upper bounds",
                "production FTS retrieval recall, ranking, abstention, and latency",
                "retrieved Wiki claim-label coverage",
            ],
            "not_measured": [
                "live-model answer wording or correctness",
                "real-corpus relevance without private human judgments",
                "claim entailment beyond fixture annotations",
            ],
        },
        "oracle": _oracle_report(fixture),
        "strategies": {"strict": strict, "production": production},
    }


def load_and_evaluate(path: Path) -> dict[str, Any]:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(fixture, dict):
        raise FixtureError("fixture root must be an object")
    return evaluate_fixture(fixture)
