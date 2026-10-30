"""Shadow-only claim/span adjudication and conservative answer rendering.

Compiler candidate links and retrieval matches are not entailment. This module
requires explicit span assessments, revalidates the original claim catalogs
and evidence manifests for every use, and never marks an answer as production
eligible. Production CLI/API adapters refuse to emit shadow answer text.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

from services.paper_evidence_service import EvidenceValidationError, validate_evidence_manifest
from services.wiki_claims import ClaimPackageError, validate_claim_catalog


SUPPORT_LEDGER_VERSION = 2
ANSWER_PLAN_VERSION = 2
MAX_CATALOGS = 100
MAX_MANIFESTS = 100
MAX_CLAIMS = 10_000
MAX_CHUNKS = 10_000
MAX_SOURCE_CHARACTERS = 2_000_000
MAX_ASSESSMENTS = 2_000
MAX_RETRIEVED_RESULTS = 100
MAX_QUOTE_CHARACTERS = 3_000
MIN_QUOTE_NONSPACE = 8
MAX_RATIONALE_CHARACTERS = 1_000
MAX_STAGES = 10
MAX_STAGE_SUPPORTS = 10
MAX_CITATIONS = 20
MAX_OUTPUT_CHARACTERS = 30_000
_EVIDENCE_ID = re.compile(r"^evidence:[0-9a-f]{64}$")
_CLAIM_ID = re.compile(r"^extraction-claim:[0-9a-f]{64}$")
_SUPPORT_ID = re.compile(r"^support:[0-9a-f]{64}$")
_ASSESSOR = re.compile(r"^[A-Za-z0-9._/-]+@[A-Za-z0-9._/-]+$")
_MARKDOWN = re.compile(r"([\\`*_[\]{}()#+.!|>-])")


class ClaimSupportError(ValueError):
    pass


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _content_id(value: Mapping[str, Any], excluded: str) -> str:
    return _sha256(_stable_json({key: item for key, item in value.items() if key != excluded}))


def _safe_inline(value: str) -> str:
    collapsed = " ".join(value.split())
    escaped = html.escape(collapsed, quote=True)
    return _MARKDOWN.sub(r"\\\1", escaped)


def _source_projection(
    claim_catalogs: Sequence[Mapping[str, Any]],
    evidence_manifests: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if not 1 <= len(claim_catalogs) <= MAX_CATALOGS:
        raise ClaimSupportError("claim catalog count exceeds the support budget")
    if not 1 <= len(evidence_manifests) <= MAX_MANIFESTS:
        raise ClaimSupportError("evidence manifest count exceeds the support budget")

    manifests: dict[str, Mapping[str, Any]] = {}
    chunks: dict[str, dict[str, Any]] = {}
    total_characters = 0
    for manifest in evidence_manifests:
        if not isinstance(manifest, dict):
            raise ClaimSupportError("evidence manifest must be an object")
        try:
            validate_evidence_manifest(manifest)
        except EvidenceValidationError as error:
            raise ClaimSupportError("evidence manifest failed validation") from error
        manifest_id = manifest["manifest_id"]
        if manifest_id in manifests:
            raise ClaimSupportError("evidence manifest ids must be unique")
        manifests[manifest_id] = manifest
        for chunk in manifest["chunks"]:
            retained = {
                "id": chunk["id"],
                "paper_id": chunk["paper_id"],
                "page_number": chunk["page_number"],
                "page_char_start": chunk["page_char_start"],
                "page_char_end": chunk["page_char_end"],
                "section_kind": chunk["section_kind"],
                "section_heading": chunk["section_heading"],
                "text": chunk["text"],
                "text_hash": chunk["text_hash"],
                "manifest_id": manifest_id,
                "source_file_hash": manifest["source"]["file_hash_sha256"],
            }
            previous = chunks.get(chunk["id"])
            if previous is not None and previous != retained:
                raise ClaimSupportError("evidence chunks conflict for one stable id")
            chunks[chunk["id"]] = retained
            total_characters += len(chunk["text"])
    if len(chunks) > MAX_CHUNKS or total_characters > MAX_SOURCE_CHARACTERS:
        raise ClaimSupportError("evidence corpus exceeds the support budget")

    claims: dict[str, dict[str, Any]] = {}
    catalogs: dict[str, Mapping[str, Any]] = {}
    paper_years: dict[str, int] = {}
    for catalog in claim_catalogs:
        if not isinstance(catalog, Mapping):
            raise ClaimSupportError("claim catalog must be an object")
        try:
            validate_claim_catalog(catalog)
        except ClaimPackageError as error:
            raise ClaimSupportError("claim catalog failed validation") from error
        signature = catalog["catalog_signature"]
        if signature in catalogs:
            raise ClaimSupportError("claim catalog signatures must be unique")
        catalogs[signature] = catalog
        manifest_id = catalog.get("evidence_manifest_id")
        manifest = manifests.get(manifest_id)
        if manifest_id is not None and (
            manifest is None or manifest["paper_id"] != catalog["paper_id"]
        ):
            raise ClaimSupportError("claim catalog does not match its evidence manifest")
        for claim in catalog["claims"]:
            retained = {
                "id": claim["id"],
                "paper_id": claim["paper_id"],
                "field_path": claim["field_path"],
                "root_field": claim["root_field"],
                "text": claim["text"],
                "retained_text_hash": claim["retained_text_hash"],
                "source_text_hash": claim["source_text_hash"],
                "compiler_evidence_status": claim["evidence_status"],
                "candidate_evidence_ids": claim["candidate_evidence_ids"],
                "catalog_signature": signature,
                "evidence_manifest_id": manifest_id,
                "source_file_hash": (
                    manifest["source"]["file_hash_sha256"] if manifest else None
                ),
            }
            previous = claims.get(claim["id"])
            if previous is not None and previous != retained:
                raise ClaimSupportError("claim catalogs conflict for one stable claim")
            claims[claim["id"]] = retained
            if claim["field_path"] == "year":
                try:
                    year = int(claim["text"])
                except (TypeError, ValueError) as error:
                    raise ClaimSupportError("paper year claim must be an integer") from error
                if not 1000 <= year <= 3000:
                    raise ClaimSupportError("paper year is out of range")
                previous_year = paper_years.get(claim["paper_id"])
                if previous_year is not None and previous_year != year:
                    raise ClaimSupportError("paper catalogs disagree on the publication year")
                paper_years[claim["paper_id"]] = year
    if len(claims) > MAX_CLAIMS:
        raise ClaimSupportError("claim corpus exceeds the support budget")
    return {
        "claims": claims,
        "chunks": chunks,
        "paper_years": paper_years,
        "identity": {
            "claim_catalog_signatures": sorted(catalogs),
            "evidence_manifest_ids": sorted(manifests),
        },
    }


def build_support_ledger(
    *,
    claim_catalogs: Sequence[Mapping[str, Any]],
    evidence_manifests: Sequence[Mapping[str, Any]],
    assessments: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    if len(assessments) > MAX_ASSESSMENTS:
        raise ClaimSupportError("assessment count exceeds the support budget")
    source = _source_projection(claim_catalogs, evidence_manifests)
    records: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for assessment in assessments:
        if not isinstance(assessment, Mapping):
            raise ClaimSupportError("assessment must be an object")
        claim_id = assessment.get("claim_id")
        evidence_id = assessment.get("evidence_id")
        relationship = assessment.get("relationship")
        method = assessment.get("method")
        assessor = assessment.get("assessor_version")
        if not isinstance(claim_id, str) or not _CLAIM_ID.fullmatch(claim_id):
            raise ClaimSupportError("assessment claim id is invalid")
        if not isinstance(evidence_id, str) or not _EVIDENCE_ID.fullmatch(evidence_id):
            raise ClaimSupportError("assessment evidence id is invalid")
        if relationship not in {"supports", "contradicts", "insufficient"}:
            raise ClaimSupportError("assessment relationship is invalid")
        if method not in {"synthetic_fixture", "declared_manual_review"}:
            raise ClaimSupportError("assessment method is not allowed in shadow mode")
        if not isinstance(assessor, str) or not _ASSESSOR.fullmatch(assessor):
            raise ClaimSupportError("assessor version must include an immutable revision")
        claim = source["claims"].get(claim_id)
        chunk = source["chunks"].get(evidence_id)
        if claim is None or chunk is None:
            raise ClaimSupportError("assessment references unknown claim or evidence")
        if relationship in {"supports", "insufficient"} and (
            claim["evidence_manifest_id"] != chunk["manifest_id"]
            or claim["source_file_hash"] != chunk["source_file_hash"]
        ):
            raise ClaimSupportError("assessment source version does not match the claim catalog")
        if relationship == "supports" and claim["paper_id"] != chunk["paper_id"]:
            raise ClaimSupportError("supporting evidence must belong to the claim paper")
        start = assessment.get("quote_start")
        end = assessment.get("quote_end")
        if (
            isinstance(start, bool) or not isinstance(start, int)
            or isinstance(end, bool) or not isinstance(end, int)
            or start < 0 or end <= start or end > len(chunk["text"])
            or end - start > MAX_QUOTE_CHARACTERS
        ):
            raise ClaimSupportError("assessment quote span is invalid")
        quote = chunk["text"][start:end]
        if len("".join(quote.split())) < MIN_QUOTE_NONSPACE:
            raise ClaimSupportError("assessment quote is not substantive")
        if assessment.get("quote") not in {None, quote}:
            raise ClaimSupportError("assessment quote does not match source bytes")
        rationale = assessment.get("rationale", "")
        if not isinstance(rationale, str) or len(rationale) > MAX_RATIONALE_CHARACTERS:
            raise ClaimSupportError("assessment rationale exceeds its budget")
        pair = (claim_id, evidence_id)
        if pair in seen_pairs:
            raise ClaimSupportError("claim/evidence pair has multiple adjudications")
        seen_pairs.add(pair)
        record = {
            "claim_id": claim_id,
            "evidence_id": evidence_id,
            "relationship": relationship,
            "method": method,
            "assessor_version": assessor,
            "rationale": rationale,
            "discovery_status": (
                "compiler_candidate"
                if evidence_id in claim["candidate_evidence_ids"]
                else "explicit_assessment_only"
            ),
            "claim_text_hash": claim["retained_text_hash"],
            "evidence_text_hash": chunk["text_hash"],
            "quote": quote,
            "quote_hash": _sha256(quote),
            "locator": {
                "paper_id": chunk["paper_id"],
                "page_number": chunk["page_number"],
                "section_kind": chunk["section_kind"],
                "section_heading": chunk["section_heading"],
                "chunk_char_start": start,
                "chunk_char_end": end,
                "page_char_start": chunk["page_char_start"] + start,
                "page_char_end": chunk["page_char_start"] + end,
                "manifest_id": chunk["manifest_id"],
                "source_file_hash": chunk["source_file_hash"],
            },
        }
        record["id"] = f"support:{_content_id(record, 'id')}"
        records.append(record)

    used_chunks = {item["evidence_id"] for item in records}
    methods = {item["method"] for item in records}
    trust_scope = (
        "no_adjudication" if not records
        else "synthetic_fixture" if methods == {"synthetic_fixture"}
        else "declared_manual_review" if methods == {"declared_manual_review"}
        else "mixed_shadow_review"
    )
    ledger: dict[str, Any] = {
        "schema_version": SUPPORT_LEDGER_VERSION,
        "trust_scope": trust_scope,
        "production_eligible": False,
        "claims": sorted(source["claims"].values(), key=lambda item: item["id"]),
        "source_chunks": [source["chunks"][item] for item in sorted(used_chunks)],
        "paper_years": source["paper_years"],
        "support_records": sorted(records, key=lambda item: item["id"]),
        "source_identity": source["identity"],
    }
    ledger["ledger_signature"] = _content_id(ledger, "ledger_signature")
    validate_support_ledger(
        ledger,
        claim_catalogs=claim_catalogs,
        evidence_manifests=evidence_manifests,
    )
    return ledger


def validate_support_ledger(
    ledger: Mapping[str, Any],
    *,
    claim_catalogs: Sequence[Mapping[str, Any]],
    evidence_manifests: Sequence[Mapping[str, Any]],
) -> None:
    if not isinstance(ledger, Mapping) or ledger.get("schema_version") != SUPPORT_LEDGER_VERSION:
        raise ClaimSupportError("unsupported support ledger")
    source = _source_projection(claim_catalogs, evidence_manifests)
    claims = ledger.get("claims")
    chunks = ledger.get("source_chunks")
    records = ledger.get("support_records")
    if not isinstance(claims, list) or not isinstance(chunks, list) or not isinstance(records, list):
        raise ClaimSupportError("support ledger collections are invalid")
    if len(records) > MAX_ASSESSMENTS:
        raise ClaimSupportError("support record count exceeds the support budget")
    if claims != sorted(source["claims"].values(), key=lambda item: item["id"]):
        raise ClaimSupportError("support ledger claims differ from validated catalogs")
    if ledger.get("paper_years") != source["paper_years"]:
        raise ClaimSupportError("support ledger years differ from validated catalogs")
    if ledger.get("source_identity") != source["identity"]:
        raise ClaimSupportError("support ledger source identity mismatch")
    chunk_by_id = {item["id"]: item for item in chunks if isinstance(item, Mapping) and "id" in item}
    if len(chunk_by_id) != len(chunks):
        raise ClaimSupportError("support ledger source chunks are invalid or duplicated")
    for identifier, chunk in chunk_by_id.items():
        if chunk != source["chunks"].get(identifier):
            raise ClaimSupportError("support ledger chunk differs from validated manifest")
    claim_by_id = {item["id"]: item for item in claims}
    seen_ids: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()
    methods: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping):
            raise ClaimSupportError("support record must be an object")
        identifier = record.get("id")
        if not isinstance(identifier, str) or not _SUPPORT_ID.fullmatch(identifier) or identifier in seen_ids:
            raise ClaimSupportError("support record id is invalid or duplicated")
        seen_ids.add(identifier)
        if identifier != f"support:{_content_id(record, 'id')}":
            raise ClaimSupportError("support record identity mismatch")
        claim = claim_by_id.get(record.get("claim_id"))
        chunk = chunk_by_id.get(record.get("evidence_id"))
        if claim is None or chunk is None:
            raise ClaimSupportError("support record source is missing")
        pair = (record["claim_id"], record["evidence_id"])
        if pair in seen_pairs:
            raise ClaimSupportError("claim/evidence pair has multiple adjudications")
        seen_pairs.add(pair)
        method = record.get("method")
        assessor = record.get("assessor_version")
        if method not in {"synthetic_fixture", "declared_manual_review"} or not isinstance(assessor, str) or not _ASSESSOR.fullmatch(assessor):
            raise ClaimSupportError("support adjudicator metadata is invalid")
        methods.add(method)
        relationship = record.get("relationship")
        if relationship not in {"supports", "contradicts", "insufficient"}:
            raise ClaimSupportError("support relationship is invalid")
        if relationship in {"supports", "insufficient"} and (
            claim["evidence_manifest_id"] != chunk["manifest_id"]
            or claim["source_file_hash"] != chunk["source_file_hash"]
        ):
            raise ClaimSupportError("support source version mismatch")
        if relationship == "supports" and claim["paper_id"] != chunk["paper_id"]:
            raise ClaimSupportError("support record crosses papers")
        locator = record.get("locator")
        quote = record.get("quote")
        rationale = record.get("rationale")
        expected_discovery = (
            "compiler_candidate"
            if record["evidence_id"] in claim["candidate_evidence_ids"]
            else "explicit_assessment_only"
        )
        if (
            not isinstance(locator, Mapping)
            or not isinstance(quote, str)
            or len(quote) > MAX_QUOTE_CHARACTERS
            or len("".join(quote.split())) < MIN_QUOTE_NONSPACE
            or not isinstance(rationale, str)
            or len(rationale) > MAX_RATIONALE_CHARACTERS
            or record.get("discovery_status") != expected_discovery
        ):
            raise ClaimSupportError("support quote/locator is invalid")
        start = locator.get("chunk_char_start")
        end = locator.get("chunk_char_end")
        if (
            isinstance(start, bool) or not isinstance(start, int)
            or isinstance(end, bool) or not isinstance(end, int)
            or start < 0 or end <= start or end > len(chunk["text"])
            or chunk["text"][start:end] != quote
            or _sha256(quote) != record.get("quote_hash")
            or record.get("claim_text_hash") != claim["retained_text_hash"]
            or record.get("evidence_text_hash") != chunk["text_hash"]
            or locator.get("paper_id") != chunk["paper_id"]
            or locator.get("page_number") != chunk["page_number"]
            or locator.get("section_kind") != chunk["section_kind"]
            or locator.get("section_heading") != chunk["section_heading"]
            or locator.get("manifest_id") != chunk["manifest_id"]
            or locator.get("source_file_hash") != chunk["source_file_hash"]
            or locator.get("page_char_start") != chunk["page_char_start"] + start
            or locator.get("page_char_end") != chunk["page_char_start"] + end
        ):
            raise ClaimSupportError("support quote/locator differs from validated source")
    expected_scope = (
        "no_adjudication" if not records
        else "synthetic_fixture" if methods == {"synthetic_fixture"}
        else "declared_manual_review" if methods == {"declared_manual_review"}
        else "mixed_shadow_review"
    )
    if ledger.get("trust_scope") != expected_scope or ledger.get("production_eligible") is not False:
        raise ClaimSupportError("support trust scope was upgraded")
    if ledger.get("ledger_signature") != _content_id(ledger, "ledger_signature"):
        raise ClaimSupportError("support ledger signature mismatch")


def _normalize_retrieved(results: Sequence[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    if len(results) > MAX_RETRIEVED_RESULTS:
        raise ClaimSupportError("retrieved result count exceeds the answer budget")
    normalized: dict[str, dict[str, str]] = {}
    for result in results:
        if not isinstance(result, Mapping):
            raise ClaimSupportError("retrieved result must be an object")
        identifier = result.get("id")
        paper_id = result.get("paper_id")
        kind = result.get("kind")
        if (
            not isinstance(identifier, str) or identifier in normalized
            or not isinstance(paper_id, str) or not paper_id
            or kind not in {"claim", "evidence"}
        ):
            raise ClaimSupportError("retrieved result provenance is invalid")
        normalized[identifier] = {"id": identifier, "paper_id": paper_id, "kind": kind}
    return normalized


def _readiness(
    *,
    answer_kind: str,
    stages: Sequence[Mapping[str, Any]],
    conflicts: Sequence[str],
    retrieved: Mapping[str, Mapping[str, str]],
    claims: Mapping[str, Mapping[str, Any]],
    supports: Mapping[str, Mapping[str, Any]],
    paper_years: Mapping[str, int],
) -> tuple[list[str], list[str]]:
    reasons: list[str] = []
    citations: list[str] = []
    stage_claims: set[str] = set()
    stage_papers: list[str] = []
    for index, stage in enumerate(stages, start=1):
        claim_id = stage["claim_id"]
        claim = claims[claim_id]
        stage_claims.add(claim_id)
        stage_papers.append(claim["paper_id"])
        retrieved_claim = retrieved.get(claim_id)
        if retrieved_claim is None:
            reasons.append(f"stage_{index}_claim_not_retrieved")
        elif retrieved_claim != {"id": claim_id, "paper_id": claim["paper_id"], "kind": "claim"}:
            raise ClaimSupportError("retrieved claim provenance mismatch")
        if not stage["support_record_ids"]:
            reasons.append(f"stage_{index}_lacks_adjudicated_support")
        for support_id in stage["support_record_ids"]:
            record = supports[support_id]
            if record["claim_id"] != claim_id:
                raise ClaimSupportError("answer support belongs to another claim")
            if record["relationship"] != "supports":
                reasons.append(f"stage_{index}_lacks_adjudicated_support")
                continue
            retrieved_evidence = retrieved.get(record["evidence_id"])
            expected = {
                "id": record["evidence_id"],
                "paper_id": record["locator"]["paper_id"],
                "kind": "evidence",
            }
            if retrieved_evidence is None:
                reasons.append(f"stage_{index}_evidence_not_retrieved")
                continue
            if retrieved_evidence != expected:
                raise ClaimSupportError("retrieved evidence provenance mismatch")
            if support_id not in citations:
                citations.append(support_id)
    if answer_kind in {"comparison", "evolution"} and len(stages) < 2:
        reasons.append("multiple_stages_required")
    if answer_kind in {"comparison", "evolution"} and len(set(stage_papers)) < 2:
        reasons.append("independent_papers_required")
    source_hashes = {
        claims[stage["claim_id"]]["source_file_hash"] for stage in stages
    }
    if answer_kind in {"comparison", "evolution"} and len(source_hashes) < 2:
        reasons.append("independent_source_files_required")
    if answer_kind == "evolution":
        years = [paper_years.get(paper_id) for paper_id in stage_papers]
        if any(year is None for year in years):
            reasons.append("stage_years_required")
        elif any(left >= right for left, right in zip(years, years[1:])):
            reasons.append("stage_order_not_chronological")
        for index, (paper_id, stage) in enumerate(zip(stage_papers, stages), start=1):
            stage_claim = claims[stage["claim_id"]]
            year_claim_ids = {
                identifier
                for identifier, claim in claims.items()
                if claim["paper_id"] == paper_id
                and claim["field_path"] == "year"
                and claim["catalog_signature"] == stage_claim["catalog_signature"]
                and claim["evidence_manifest_id"] == stage_claim["evidence_manifest_id"]
                and claim["source_file_hash"] == stage_claim["source_file_hash"]
            }
            stage_claims.update(year_claim_ids)
            year_supports = [
                record
                for record in supports.values()
                if record["relationship"] == "supports"
                and record["claim_id"] in year_claim_ids
            ]
            if not year_supports:
                reasons.append(f"stage_{index}_year_lacks_adjudicated_support")
                continue
            for record in year_supports:
                retrieved_evidence = retrieved.get(record["evidence_id"])
                expected = {
                    "id": record["evidence_id"],
                    "paper_id": record["locator"]["paper_id"],
                    "kind": "evidence",
                }
                if retrieved_evidence is None:
                    reasons.append(f"stage_{index}_year_evidence_not_retrieved")
                elif retrieved_evidence != expected:
                    raise ClaimSupportError("retrieved year evidence provenance mismatch")
                elif record["id"] not in citations:
                    citations.append(record["id"])
    targeted_conflicts = {
        identifier
        for identifier, record in supports.items()
        if record["relationship"] == "contradicts"
        and record["claim_id"] in stage_claims
    }
    supplied_conflicts = set(conflicts)
    if not supplied_conflicts.issubset(targeted_conflicts):
        raise ClaimSupportError("answer conflict does not target a stage claim")
    if targeted_conflicts - supplied_conflicts:
        reasons.append("unresolved_adjudicated_conflict")
    for identifier in supplied_conflicts:
        record = supports[identifier]
        retrieved_evidence = retrieved.get(record["evidence_id"])
        expected = {
            "id": record["evidence_id"],
            "paper_id": record["locator"]["paper_id"],
            "kind": "evidence",
        }
        if retrieved_evidence is None:
            reasons.append("conflict_evidence_not_retrieved")
        elif retrieved_evidence != expected:
            raise ClaimSupportError("retrieved conflict evidence provenance mismatch")
    if answer_kind == "conflict" and not supplied_conflicts:
        reasons.append("adjudicated_conflict_required")
    for identifier in conflicts:
        if identifier not in citations:
            citations.append(identifier)
    if len(citations) > MAX_CITATIONS:
        reasons.append("citation_budget_exceeded")
    return list(dict.fromkeys(reasons)), citations


def build_answer_plan(
    *,
    question: str,
    answer_kind: str,
    retrieved_results: Sequence[Mapping[str, Any]],
    support_ledger: Mapping[str, Any],
    claim_catalogs: Sequence[Mapping[str, Any]],
    evidence_manifests: Sequence[Mapping[str, Any]],
    stages: Sequence[Mapping[str, Any]],
    conflict_record_ids: Sequence[str] = (),
) -> dict[str, Any]:
    validate_support_ledger(
        support_ledger,
        claim_catalogs=claim_catalogs,
        evidence_manifests=evidence_manifests,
    )
    if not isinstance(question, str) or not question.strip() or len(question) > 2_000:
        raise ClaimSupportError("answer question is invalid")
    if answer_kind not in {"single_claim", "comparison", "evolution", "conflict"}:
        raise ClaimSupportError("unsupported answer kind")
    if not 1 <= len(stages) <= MAX_STAGES:
        raise ClaimSupportError("answer stage count exceeds its budget")
    if len(conflict_record_ids) > MAX_CITATIONS:
        raise ClaimSupportError("conflict count exceeds its budget")
    retrieved = _normalize_retrieved(retrieved_results)
    claims = {item["id"]: item for item in support_ledger["claims"]}
    supports = {item["id"]: item for item in support_ledger["support_records"]}
    normalized_stages: list[dict[str, Any]] = []
    for stage in stages:
        if not isinstance(stage, Mapping):
            raise ClaimSupportError("answer stage must be an object")
        label = stage.get("label")
        claim_id = stage.get("claim_id")
        support_ids = stage.get("support_record_ids")
        if not isinstance(label, str) or not label.strip() or len(label) > 200:
            raise ClaimSupportError("answer stage label is invalid")
        if not isinstance(claim_id, str) or claim_id not in claims:
            raise ClaimSupportError("answer stage references an unknown claim")
        if (
            not isinstance(support_ids, list)
            or len(support_ids) > MAX_STAGE_SUPPORTS
            or any(not isinstance(item, str) or item not in supports for item in support_ids)
        ):
            raise ClaimSupportError("answer stage support ids are invalid")
        normalized_stages.append({
            "label": label.strip(),
            "claim_id": claim_id,
            "support_record_ids": list(dict.fromkeys(support_ids)),
        })
    conflicts = list(dict.fromkeys(conflict_record_ids))
    reasons, citations = _readiness(
        answer_kind=answer_kind,
        stages=normalized_stages,
        conflicts=conflicts,
        retrieved=retrieved,
        claims=claims,
        supports=supports,
        paper_years=support_ledger["paper_years"],
    )
    plan: dict[str, Any] = {
        "schema_version": ANSWER_PLAN_VERSION,
        "question": question.strip(),
        "answer_kind": answer_kind,
        "ledger_signature": support_ledger["ledger_signature"],
        "trust_scope": support_ledger["trust_scope"],
        "retrieved_results": [retrieved[item] for item in sorted(retrieved)],
        "stages": normalized_stages,
        "conflict_record_ids": conflicts,
        "citation_record_ids": citations,
        "shadow_answerable": not reasons,
        "answerable": False,
        "production_eligible": False,
        "refusal_reasons": reasons,
    }
    plan["plan_id"] = _content_id(plan, "plan_id")
    validate_answer_plan(
        plan,
        support_ledger,
        claim_catalogs=claim_catalogs,
        evidence_manifests=evidence_manifests,
    )
    return plan


def validate_answer_plan(
    plan: Mapping[str, Any],
    ledger: Mapping[str, Any],
    *,
    claim_catalogs: Sequence[Mapping[str, Any]],
    evidence_manifests: Sequence[Mapping[str, Any]],
) -> None:
    validate_support_ledger(
        ledger,
        claim_catalogs=claim_catalogs,
        evidence_manifests=evidence_manifests,
    )
    if not isinstance(plan, Mapping) or plan.get("schema_version") != ANSWER_PLAN_VERSION:
        raise ClaimSupportError("unsupported answer plan")
    if plan.get("ledger_signature") != ledger["ledger_signature"] or plan.get("trust_scope") != ledger["trust_scope"]:
        raise ClaimSupportError("answer plan uses another support ledger or trust scope")
    if plan.get("plan_id") != _content_id(plan, "plan_id"):
        raise ClaimSupportError("answer plan identity mismatch")
    if plan.get("answerable") is not False or plan.get("production_eligible") is not False:
        raise ClaimSupportError("shadow answer plan attempted a production upgrade")
    retrieved = _normalize_retrieved(plan.get("retrieved_results", []))
    question = plan.get("question")
    answer_kind = plan.get("answer_kind")
    stages = plan.get("stages")
    conflicts = plan.get("conflict_record_ids")
    claims = {item["id"]: item for item in ledger["claims"]}
    supports = {item["id"]: item for item in ledger["support_records"]}
    if not isinstance(question, str) or not question.strip() or len(question) > 2_000:
        raise ClaimSupportError("answer plan question is invalid")
    if answer_kind not in {"single_claim", "comparison", "evolution", "conflict"}:
        raise ClaimSupportError("answer plan kind is invalid")
    if (
        not isinstance(stages, list)
        or not 1 <= len(stages) <= MAX_STAGES
        or not isinstance(conflicts, list)
        or len(conflicts) > MAX_CITATIONS
        or len(set(conflicts)) != len(conflicts)
        or any(not isinstance(item, str) or item not in supports for item in conflicts)
    ):
        raise ClaimSupportError("answer plan collections are invalid")
    for stage in stages:
        if (
            not isinstance(stage, Mapping)
            or not isinstance(stage.get("label"), str)
            or not stage["label"].strip()
            or len(stage["label"]) > 200
            or stage.get("claim_id") not in claims
            or not isinstance(stage.get("support_record_ids"), list)
            or len(stage["support_record_ids"]) > MAX_STAGE_SUPPORTS
            or len(set(stage["support_record_ids"])) != len(stage["support_record_ids"])
            or any(item not in supports for item in stage["support_record_ids"])
        ):
            raise ClaimSupportError("answer plan stage is invalid")
    reasons, citations = _readiness(
        answer_kind=answer_kind,
        stages=stages,
        conflicts=conflicts,
        retrieved=retrieved,
        claims=claims,
        supports=supports,
        paper_years=ledger["paper_years"],
    )
    if (
        plan.get("refusal_reasons") != reasons
        or plan.get("citation_record_ids") != citations
        or plan.get("shadow_answerable") != (not reasons)
    ):
        raise ClaimSupportError("answer plan readiness was altered")


def render_shadow_answer(
    plan: Mapping[str, Any],
    ledger: Mapping[str, Any],
    *,
    claim_catalogs: Sequence[Mapping[str, Any]],
    evidence_manifests: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Render exact source-bound content for offline evaluation only."""
    validate_answer_plan(
        plan,
        ledger,
        claim_catalogs=claim_catalogs,
        evidence_manifests=evidence_manifests,
    )
    if not plan["shadow_answerable"]:
        return {
            "answerable": False,
            "shadow_answerable": False,
            "production_eligible": False,
            "trust_scope": plan["trust_scope"],
            "markdown": "证据不足，无法形成 claim 级回答。",
            "citations": [],
            "refusal_reasons": plan["refusal_reasons"],
        }
    claims = {item["id"]: item for item in ledger["claims"]}
    supports = {item["id"]: item for item in ledger["support_records"]}
    numbers = {
        identifier: index
        for index, identifier in enumerate(plan["citation_record_ids"], start=1)
    }
    lines: list[str] = []
    for stage in plan["stages"]:
        markers = "".join(f"[^{numbers[item]}]" for item in stage["support_record_ids"])
        lines.extend((f"## {_safe_inline(stage['label'])}", f"{_safe_inline(claims[stage['claim_id']]['text'])}{markers}", ""))
    if plan["conflict_record_ids"]:
        lines.append("## 冲突证据")
        for identifier in plan["conflict_record_ids"]:
            lines.append(f"- {_safe_inline(supports[identifier]['quote'])}[^{numbers[identifier]}]")
        lines.append("")
    citations: list[dict[str, Any]] = []
    for identifier in plan["citation_record_ids"]:
        record = supports[identifier]
        locator = record["locator"]
        number = numbers[identifier]
        section = locator["section_heading"] or locator["section_kind"]
        lines.append(
            f"[^{number}]: paper {_safe_inline(locator['paper_id'])}, "
            f"p.{locator['page_number']}, {_safe_inline(section)}, "
            f"chars {locator['page_char_start']}-{locator['page_char_end']}; "
            f"“{_safe_inline(record['quote'])}”"
        )
        citations.append({
            "number": number,
            "support_record_id": identifier,
            "claim_id": record["claim_id"],
            "evidence_id": record["evidence_id"],
            "relationship": record["relationship"],
            "quote": record["quote"],
            "locator": locator,
            "assessor_version": record["assessor_version"],
        })
    markdown = "\n".join(lines).rstrip() + "\n"
    if len(markdown) > MAX_OUTPUT_CHARACTERS:
        raise ClaimSupportError("shadow answer exceeds the output budget")
    return {
        "answerable": False,
        "shadow_answerable": True,
        "production_eligible": False,
        "trust_scope": plan["trust_scope"],
        "markdown": markdown,
        "citations": citations,
        "refusal_reasons": [],
    }


def _production_refusal(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "answerable": False,
        "production_eligible": False,
        "markdown": "该回答仅通过 shadow 证据评测，尚未获得受信任的生产审核凭据。",
        "citations": [],
        "refusal_reasons": ["trusted_review_receipt_required"],
        "shadow_plan_id": plan.get("plan_id"),
    }


def execute_cli_answer(
    plan: Mapping[str, Any],
    ledger: Mapping[str, Any],
    *,
    claim_catalogs: Sequence[Mapping[str, Any]],
    evidence_manifests: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    validate_answer_plan(
        plan, ledger, claim_catalogs=claim_catalogs, evidence_manifests=evidence_manifests
    )
    return _production_refusal(plan)


def execute_api_answer(
    plan: Mapping[str, Any],
    ledger: Mapping[str, Any],
    *,
    claim_catalogs: Sequence[Mapping[str, Any]],
    evidence_manifests: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    validate_answer_plan(
        plan, ledger, claim_catalogs=claim_catalogs, evidence_manifests=evidence_manifests
    )
    return _production_refusal(plan)
