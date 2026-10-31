"""Deterministic claim packages for traceable Wiki compilation.

The LLM remains responsible for readable synthesis, but it no longer receives
an arbitrary prefix of the extraction JSON. This module gives every supported
source field an explicit budget, stable extraction-claim identity and optional
candidate evidence links. Merely pointing at an existing chunk is not treated
as entailment: page-level support remains unavailable until a later stage also
validates the quoted span.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from typing import Any

from services.paper_evidence_service import (
    EvidenceValidationError,
    validate_evidence_manifest,
)


CLAIM_SCHEMA_VERSION = 1
CLAIM_COMPILER_CONTRACT = "wiki_claim_compiler_v1"

# Ordered by usefulness for cross-paper research. A budget is per root field,
# so a long code sample or abstract cannot silently consume the space reserved
# for innovations, experiments, history or limitations.
FIELD_BUDGETS: tuple[tuple[str, int], ...] = (
    ("title", 500),
    ("authors", 2_000),
    ("venue", 500),
    ("year", 100),
    ("paper_category", 300),
    ("problem_area", 500),
    ("tech_stack_position", 500),
    ("keywords", 2_000),
    ("core_contribution", 2_000),
    ("problem", 1_500),
    ("motivation", 2_000),
    ("innovations", 6_000),
    ("experimental_gains", 4_000),
    ("historical_position", 6_000),
    ("limitations", 4_000),
    ("abstract_summary", 4_000),
    ("principle", 8_000),
    ("contributions", 4_000),
    ("key_findings", 8_000),
    ("techniques", 8_000),
    ("datasets", 5_000),
    ("baselines", 3_000),
    ("pytorch_snippet", 1_500),
)
FIELD_ORDER = {name: index for index, (name, _) in enumerate(FIELD_BUDGETS)}
FIELD_BUDGET_BY_ROOT = dict(FIELD_BUDGETS)

CONCEPT_CLAIM_ROOTS = frozenset({
    "title",
    "problem_area",
    "core_contribution",
    "problem",
    "motivation",
    "innovations",
    "experimental_gains",
    "historical_position",
    "limitations",
    "abstract_summary",
    "principle",
    "contributions",
    "key_findings",
    "techniques",
    "datasets",
    "baselines",
})

_EVIDENCE_ID_RE = re.compile(r"^evidence:[0-9a-f]{64}$")


class ClaimPackageError(ValueError):
    """Raised when a claim package would overstate its provenance."""


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _claim_text(value: Any) -> str | None:
    if value is None or value == "" or value == [] or value == {}:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (str, int, float)):
        return str(value).strip() or None
    return _stable_json(value)


def _walk_leaves(value: Any, path: str) -> Iterable[tuple[str, str]]:
    if isinstance(value, Mapping):
        for key in sorted(value, key=lambda item: str(item)):
            child_path = f"{path}.{key}" if path else str(key)
            yield from _walk_leaves(value[key], child_path)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk_leaves(item, f"{path}[{index}]")
        return
    text = _claim_text(value)
    if text is not None:
        yield path, text


def _root(path: str) -> str:
    return re.split(r"[.[]", path, maxsplit=1)[0]


def _truncated_text(text: str, limit: int) -> str:
    marker = " … [truncated]"
    if limit <= len(marker):
        return marker[:limit]
    return text[: limit - len(marker)].rstrip() + marker


def _valid_evidence_ids(
    manifest: Mapping[str, Any] | None,
    *,
    expected_paper_id: str,
) -> set[str] | None:
    if manifest is None:
        return None
    if not isinstance(manifest, dict):
        raise ClaimPackageError("evidence manifest must be an object")
    try:
        validate_evidence_manifest(manifest)
    except EvidenceValidationError as error:
        raise ClaimPackageError("evidence manifest failed validation") from error
    if str(manifest.get("paper_id")) != expected_paper_id:
        raise ClaimPackageError("evidence manifest belongs to another paper")
    chunks = manifest.get("chunks")
    if not isinstance(chunks, list):
        raise ClaimPackageError("evidence manifest must contain chunks")
    identifiers: set[str] = set()
    for chunk in chunks:
        if not isinstance(chunk, Mapping):
            raise ClaimPackageError("evidence chunks must be objects")
        identifier = chunk.get("id")
        if not isinstance(identifier, str) or not _EVIDENCE_ID_RE.fullmatch(identifier):
            raise ClaimPackageError("evidence manifest contains an invalid chunk id")
        if identifier in identifiers:
            raise ClaimPackageError("evidence manifest contains duplicate chunk ids")
        identifiers.add(identifier)
    return identifiers


def build_claim_catalog(
    *,
    paper_id: str | int,
    extraction: Mapping[str, Any],
    evidence_links: Mapping[str, list[str]] | None = None,
    evidence_manifest: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, budgeted catalog from structured extraction.

    ``evidence_links`` keys may be a field path or a computed claim id. Links
    are accepted only when an evidence manifest is supplied and contains each
    referenced chunk. No lexical or semantic matching is treated as support.
    """
    paper_key = str(paper_id)
    if isinstance(paper_id, bool) or not isinstance(paper_id, (str, int)):
        raise ClaimPackageError("paper_id must be a string or integer")
    if not paper_key.strip() or paper_key != paper_key.strip():
        raise ClaimPackageError("paper_id must be non-empty")
    if not isinstance(extraction, Mapping):
        raise ClaimPackageError("extraction must be an object")
    if evidence_links is not None and not isinstance(evidence_links, Mapping):
        raise ClaimPackageError("evidence_links must be an object")

    valid_evidence = _valid_evidence_ids(
        evidence_manifest,
        expected_paper_id=paper_key,
    )
    if evidence_links and valid_evidence is None:
        raise ClaimPackageError("evidence links require a validated manifest")

    leaves: list[tuple[str, str]] = []
    ignored_roots: set[str] = set()
    for key, value in extraction.items():
        root = str(key)
        if root not in FIELD_BUDGET_BY_ROOT:
            if _claim_text(value) is not None:
                ignored_roots.add(root)
            continue
        if root == "pytorch_snippet" and isinstance(value, Mapping):
            value = {
                key: item
                for key, item in value.items()
                if str(key) != "code"
            }
        leaves.extend(_walk_leaves(value, root))
    leaves.sort(key=lambda item: (FIELD_ORDER[_root(item[0])], item[0]))

    remaining = dict(FIELD_BUDGETS)
    claims: list[dict[str, Any]] = []
    dropped_paths: list[str] = []
    truncated_paths: list[str] = []
    source_characters = 0
    retained_characters = 0
    links = dict(evidence_links or {})
    for path, full_text in leaves:
        root = _root(path)
        source_characters += len(full_text)
        available = remaining[root]
        if available <= 0:
            dropped_paths.append(path)
            continue
        retained = full_text if len(full_text) <= available else _truncated_text(
            full_text, available
        )
        was_truncated = retained != full_text
        remaining[root] -= len(retained)
        retained_characters += len(retained)
        if was_truncated:
            truncated_paths.append(path)
        full_hash = _sha256_text(full_text)
        identity = "|".join((
            str(CLAIM_SCHEMA_VERSION),
            paper_key,
            path,
            full_hash,
        ))
        claim_id = f"extraction-claim:{_sha256_text(identity)}"
        evidence_ids = links.get(claim_id, links.get(path, []))
        if not isinstance(evidence_ids, list) or any(
            not isinstance(item, str) for item in evidence_ids
        ):
            raise ClaimPackageError(f"invalid evidence links for {path}")
        evidence_ids = list(dict.fromkeys(evidence_ids))
        if valid_evidence is not None:
            missing = set(evidence_ids) - valid_evidence
            if missing:
                raise ClaimPackageError(f"unknown evidence id for {path}")
        claims.append({
            "id": claim_id,
            "paper_id": paper_key,
            "field_path": path,
            "root_field": root,
            "text": retained,
            "origin": "structured_extraction",
            "support_level": "extraction_attribution",
            "source_text_hash": full_hash,
            "retained_text_hash": _sha256_text(retained),
            "truncated": was_truncated,
            "candidate_evidence_ids": evidence_ids,
            "evidence_status": "candidate_evidence" if evidence_ids else "unavailable",
        })

    catalog: dict[str, Any] = {
        "schema_version": CLAIM_SCHEMA_VERSION,
        "compiler_contract": CLAIM_COMPILER_CONTRACT,
        "paper_id": paper_key,
        "evidence_manifest_id": (
            evidence_manifest.get("manifest_id") if evidence_manifest else None
        ),
        "claims": claims,
        "retention": {
            "source_claim_count": len(leaves),
            "retained_claim_count": len(claims),
            "dropped_claim_count": len(dropped_paths),
            "source_characters": source_characters,
            "retained_characters": retained_characters,
            "truncated_paths": truncated_paths,
            "dropped_paths": dropped_paths,
            "ignored_roots": sorted(ignored_roots),
        },
    }
    catalog["catalog_signature"] = _sha256_text(_stable_json(catalog))
    validate_claim_catalog(catalog)
    return catalog


def validate_claim_catalog(catalog: Mapping[str, Any]) -> None:
    if catalog.get("schema_version") != CLAIM_SCHEMA_VERSION:
        raise ClaimPackageError("unsupported claim schema")
    if catalog.get("compiler_contract") != CLAIM_COMPILER_CONTRACT:
        raise ClaimPackageError("unsupported compiler contract")
    paper_id = catalog.get("paper_id")
    if not isinstance(paper_id, str) or not paper_id.strip():
        raise ClaimPackageError("invalid catalog paper_id")
    manifest_id = catalog.get("evidence_manifest_id")
    if manifest_id is not None and (
        not isinstance(manifest_id, str)
        or not re.fullmatch(r"manifest:[0-9a-f]{64}", manifest_id)
    ):
        raise ClaimPackageError("invalid evidence manifest id")
    claims = catalog.get("claims")
    if not isinstance(claims, list):
        raise ClaimPackageError("claims must be a list")
    seen: set[str] = set()
    for claim in claims:
        if not isinstance(claim, Mapping):
            raise ClaimPackageError("each claim must be an object")
        claim_id = claim.get("id")
        path = claim.get("field_path")
        text = claim.get("text")
        if (
            not isinstance(claim_id, str)
            or not re.fullmatch(r"extraction-claim:[0-9a-f]{64}", claim_id)
            or claim_id in seen
        ):
            raise ClaimPackageError("invalid or duplicate claim id")
        if not isinstance(path, str) or _root(path) not in FIELD_BUDGET_BY_ROOT:
            raise ClaimPackageError("invalid claim field path")
        if claim.get("root_field") != _root(path):
            raise ClaimPackageError("claim root mismatch")
        if claim.get("paper_id") != paper_id:
            raise ClaimPackageError("claim paper mismatch")
        if not isinstance(text, str) or not text:
            raise ClaimPackageError("claim text must be non-empty")
        if claim.get("origin") != "structured_extraction":
            raise ClaimPackageError("claim origin mismatch")
        if claim.get("support_level") != "extraction_attribution":
            raise ClaimPackageError("claim support level mismatch")
        if _sha256_text(text) != claim.get("retained_text_hash"):
            raise ClaimPackageError("claim retained hash mismatch")
        if not re.fullmatch(r"[0-9a-f]{64}", str(claim.get("source_text_hash") or "")):
            raise ClaimPackageError("invalid claim source hash")
        evidence_ids = claim.get("candidate_evidence_ids")
        if not isinstance(evidence_ids, list) or any(
            not isinstance(item, str) or not _EVIDENCE_ID_RE.fullmatch(item)
            for item in evidence_ids
        ):
            raise ClaimPackageError("invalid supporting evidence ids")
        expected_status = "candidate_evidence" if evidence_ids else "unavailable"
        if claim.get("evidence_status") != expected_status:
            raise ClaimPackageError("claim evidence status mismatch")
        seen.add(claim_id)
    retention = catalog.get("retention")
    if not isinstance(retention, Mapping):
        raise ClaimPackageError("retention must be an object")
    if retention.get("retained_claim_count") != len(claims):
        raise ClaimPackageError("retention claim count mismatch")
    content = dict(catalog)
    claimed_signature = content.pop("catalog_signature", None)
    if claimed_signature != _sha256_text(_stable_json(content)):
        raise ClaimPackageError("catalog signature mismatch")


def render_claim_blocks(
    catalog: Mapping[str, Any],
    *,
    roots: set[str] | frozenset[str] | None = None,
    max_characters: int | None = None,
) -> tuple[str, list[str], list[str]]:
    """Render whole claim blocks without cutting a claim in the middle."""
    validate_claim_catalog(catalog)
    lines: list[str] = []
    included: list[str] = []
    omitted: list[str] = []
    used = 0
    for claim in catalog["claims"]:
        if roots is not None and claim["root_field"] not in roots:
            continue
        evidence = ",".join(claim["candidate_evidence_ids"]) or "unavailable"
        block = (
            f"[{claim['id']}] "
            f"field={claim['field_path']} evidence={evidence}\n{claim['text']}"
        )
        addition = len(block) + (2 if lines else 0)
        if max_characters is not None and used + addition > max_characters:
            omitted.append(claim["id"])
            continue
        lines.append(block)
        included.append(claim["id"])
        used += addition
    return "\n\n".join(lines), included, omitted


def render_claim_appendix(
    catalogs: Iterable[Mapping[str, Any]],
    *,
    roots: set[str] | frozenset[str] | None = None,
) -> str:
    """Render a collapsible, search-indexable ledger for compiled Wiki pages."""
    rows: list[str] = []
    for catalog in catalogs:
        validate_claim_catalog(catalog)
        for claim in catalog["claims"]:
            if roots is not None and claim["root_field"] not in roots:
                continue
            evidence = " ".join(
                f"`{identifier}`" for identifier in claim["candidate_evidence_ids"]
            ) or "原文 evidence 不可用"
            rows.append(
                f"- `{claim['id']}` · paper `{claim['paper_id']}` · "
                f"`{claim['field_path']}` · {evidence}\n  {claim['text']}"
            )
    if not rows:
        return ""
    return (
        "<details>\n<summary>结构化 Claim 与证据索引</summary>\n\n"
        + "\n".join(rows)
        + "\n\n</details>"
    )
