"""Explicit, non-production shadow compiler for claim-attributed Wiki v2.

This module never reads application data, chooses ``data/wiki`` as a target,
calls a model, refreshes FTS, reconciles pages or writes a database. Callers
must provide normalized synthetic/approved inputs, structured model output and
an explicit output root that does not overlap the production Wiki directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from services.wiki_claims import (
    CLAIM_COMPILER_CONTRACT,
    CONCEPT_CLAIM_ROOTS,
    build_claim_catalog,
    render_claim_blocks,
    validate_claim_catalog,
)


SHADOW_COMPILER_VERSION = "wiki_shadow_compiler_v2"
PAPER_TEMPLATE_VERSION = "paper_claim_attribution_v2"
CONCEPT_TEMPLATE_VERSION = "concept_claim_attribution_v2"
BUDGET_POLICY_VERSION = "per_field_and_fair_per_paper_v2"
RENDERER_VERSION = "claim_markdown_renderer_v2"
CONCEPT_TOTAL_CHARACTERS = 48_000
CONCEPT_MAX_PER_PAPER = 8_000
NOTE_MAX_CHARACTERS = 4_000

_CONCEPT_PATH_PRIORITY = (
    "core_contribution",
    "innovations.this_work",
    "experimental_gains",
    "historical_position.overall",
    "limitations",
    "problem",
    "motivation",
    "innovations.previous_work",
    "innovations.why_better",
    "historical_position.builds_on",
    "historical_position.inspired",
)
_CONCEPT_PATH_RANK = {
    path: index for index, path in enumerate(_CONCEPT_PATH_PRIORITY)
}

PAPER_SYSTEM_PROMPT = (
    "根据结构化 extraction claims 写论文 Wiki。只返回约定的 paragraphs JSON；"
    "每段必须列出实际使用的 source_claim_ids。claim 仅表示结构化抽取归因，"
    "evidence_status=unavailable/candidate_evidence 均不能表述为已核验原文。"
)
CONCEPT_SYSTEM_PROMPT = (
    "综合多篇论文的结构化 extraction claims。只返回约定的 paragraphs JSON；"
    "每段列出实际使用的 source_claim_ids。比较、演进和分歧必须归因到相应论文 claim；"
    "不得把 extraction attribution 冒充页级原文证据。"
)


class ShadowCompileError(ValueError):
    """Raised when a shadow artifact cannot preserve attribution safely."""


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _generation_config(value: Mapping[str, Any] | None) -> dict[str, Any]:
    config = {
        "temperature": 0.3,
        "max_tokens": 1_800,
        "response_format": "claim_paragraphs_v1",
    }
    if value:
        config.update(dict(value))
    if (
        isinstance(config.get("max_tokens"), bool)
        or not isinstance(config.get("max_tokens"), int)
        or config["max_tokens"] < 1
    ):
        raise ShadowCompileError("max_tokens must be a positive integer")
    temperature = config.get("temperature")
    if isinstance(temperature, bool) or not isinstance(temperature, (int, float)):
        raise ShadowCompileError("temperature must be numeric")
    if config.get("response_format") != "claim_paragraphs_v1":
        raise ShadowCompileError("unsupported response format")
    return config


def _bounded_note(note: str) -> tuple[str, dict[str, Any]]:
    if not isinstance(note, str):
        raise ShadowCompileError("notes must be a string")
    normalized = note.strip()
    retained = normalized[:NOTE_MAX_CHARACTERS]
    note_id = f"user-note:{_sha256_text(normalized)}" if normalized else None
    return retained, {
        "source_id": note_id,
        "origin": "user_note",
        "original_characters": len(normalized),
        "retained_characters": len(retained),
        "truncated": retained != normalized,
        "text_hash": _sha256_text(normalized),
    }


def _claim_sources(catalog: Mapping[str, Any]) -> list[dict[str, Any]]:
    validate_claim_catalog(catalog)
    return [
        {
            "source_id": claim["id"],
            "origin": claim["origin"],
            "paper_id": claim["paper_id"],
            "field_path": claim["field_path"],
            "text_hash": claim["source_text_hash"],
            "support_level": claim["support_level"],
            "evidence_status": claim["evidence_status"],
            "candidate_evidence_ids": claim["candidate_evidence_ids"],
        }
        for claim in catalog["claims"]
    ]


def _concept_claim_rank(claim: Mapping[str, Any]) -> tuple[int, str]:
    path = str(claim["field_path"])
    return (_CONCEPT_PATH_RANK.get(path, len(_CONCEPT_PATH_RANK)), path)


def _render_concept_capsule(
    catalog: Mapping[str, Any],
    *,
    max_characters: int,
) -> tuple[str, list[str], list[str], list[str]]:
    """Retain evolution-critical paths before other concept fields.

    Each block is whole and attributable. Text may be excerpted inside that
    block, while the claim keeps the hash of its complete extraction value.
    """
    validate_claim_catalog(catalog)
    claims = sorted(
        (
            claim for claim in catalog["claims"]
            if claim["root_field"] in CONCEPT_CLAIM_ROOTS
        ),
        key=_concept_claim_rank,
    )
    lines: list[str] = []
    included: list[str] = []
    omitted: list[str] = []
    roots: list[str] = []
    remaining = max_characters
    for claim in claims:
        header = (
            f"[{claim['id']}] field={claim['field_path']} "
            f"evidence={claim['evidence_status']}\n"
        )
        separator = 2 if lines else 0
        minimum = len(header) + 24 + separator
        if remaining < minimum:
            omitted.append(claim["id"])
            continue
        text_limit = min(320, remaining - len(header) - separator)
        text = claim["text"]
        if len(text) > text_limit:
            marker = " … [capsule truncated]"
            text = text[: max(1, text_limit - len(marker))].rstrip() + marker
        block = header + text
        lines.append(block)
        included.append(claim["id"])
        if claim["root_field"] not in roots:
            roots.append(claim["root_field"])
        remaining -= len(block) + separator
    return "\n\n".join(lines), included, omitted, roots


def _signature_identity(plan: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "signature_schema": "wiki-compile-input-v2",
        "compiler_version": SHADOW_COMPILER_VERSION,
        "kind": plan["kind"],
        "template_version": plan["template_version"],
        "budget_policy_version": BUDGET_POLICY_VERSION,
        "claim_contract_version": CLAIM_COMPILER_CONTRACT,
        "renderer_version": RENDERER_VERSION,
        "model": plan["model"],
        "locale": plan["locale"],
        "generation_config": plan["generation_config"],
        "system_prompt_hash": _sha256_text(plan["system_prompt"]),
        "user_prompt_hash": _sha256_text(plan["user_prompt"]),
        "bounded_payload_hash": _sha256_text(_stable_json(plan["bounded_payload"])),
        "coverage_hash": _sha256_text(_stable_json(plan["coverage"])),
        "sources_hash": _sha256_text(_stable_json(plan["sources"])),
        "source_identity": plan["source_identity"],
    }


def _finalize_plan(plan: dict[str, Any]) -> dict[str, Any]:
    identity = _signature_identity(plan)
    plan["signature_inputs"] = identity
    plan["source_signature_v2"] = _sha256_text(_stable_json(identity))
    return plan


def validate_compile_plan(plan: Mapping[str, Any]) -> None:
    if not isinstance(plan, Mapping) or plan.get("plan_schema_version") != 2:
        raise ShadowCompileError("unsupported compile plan")
    if plan.get("kind") not in {"paper", "concept"}:
        raise ShadowCompileError("unsupported plan kind")
    signature = plan.get("source_signature_v2")
    if not isinstance(signature, str) or not re.fullmatch(r"[0-9a-f]{64}", signature):
        raise ShadowCompileError("invalid compile plan signature")
    required = (
        "template_version",
        "model",
        "locale",
        "generation_config",
        "system_prompt",
        "user_prompt",
        "bounded_payload",
        "coverage",
        "sources",
        "source_identity",
    )
    if any(key not in plan for key in required):
        raise ShadowCompileError("compile plan is incomplete")
    sources = plan["sources"]
    if not isinstance(sources, list):
        raise ShadowCompileError("compile plan sources must be a list")
    source_ids: list[str] = []
    for source in sources:
        if not isinstance(source, Mapping):
            raise ShadowCompileError("compile plan source must be an object")
        source_id = source.get("source_id")
        if not isinstance(source_id, str) or not source_id:
            raise ShadowCompileError("compile plan source id is invalid")
        source_ids.append(source_id)
    if len(source_ids) != len(set(source_ids)):
        raise ShadowCompileError("compile plan source ids must be unique")
    expected_identity = _signature_identity(plan)
    if plan.get("signature_inputs") != expected_identity:
        raise ShadowCompileError("compile plan signature inputs were altered")
    if signature != _sha256_text(_stable_json(expected_identity)):
        raise ShadowCompileError("compile plan signature mismatch")


def build_paper_compile_plan(
    *,
    paper_id: str | int,
    title: str,
    extraction: Mapping[str, Any],
    notes: str,
    model: str,
    locale: str,
    source_file_hash: str,
    generation_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(title, str) or not title.strip():
        raise ShadowCompileError("title must be non-empty")
    if not isinstance(model, str) or not model.strip():
        raise ShadowCompileError("model must be non-empty")
    if not isinstance(locale, str) or not locale.strip():
        raise ShadowCompileError("locale must be non-empty")
    if not re.fullmatch(r"[0-9a-f]{64}", source_file_hash):
        raise ShadowCompileError("source_file_hash must be a SHA-256")
    catalog = build_claim_catalog(paper_id=paper_id, extraction=extraction)
    claim_text, included, omitted = render_claim_blocks(catalog)
    retained_note, note_report = _bounded_note(notes)
    sources = _claim_sources(catalog)
    if note_report["source_id"]:
        sources.append(note_report)
    note_label = (
        f"[{note_report['source_id']}]" if note_report["source_id"] else ""
    )
    bounded_payload = {
        "paper_id": str(paper_id),
        "title": title.strip(),
        "claims": catalog["claims"],
        "notes": retained_note,
    }
    user_prompt = (
        f"paper_id: {paper_id}\ntitle: {title.strip()}\n\n"
        f"STRUCTURED EXTRACTION CLAIMS\n{claim_text or '[none]'}\n\n"
        f"USER NOTE {note_label}\n"
        f"{retained_note or '[none]'}\n\n"
        "Return JSON: {paragraphs:[{section,text,source_claim_ids:[...]}]}."
    )
    return _finalize_plan({
        "plan_schema_version": 2,
        "kind": "paper",
        "entity_id": str(paper_id),
        "title": title.strip(),
        "template_version": PAPER_TEMPLATE_VERSION,
        "model": model.strip(),
        "locale": locale.strip(),
        "generation_config": _generation_config(generation_config),
        "system_prompt": PAPER_SYSTEM_PROMPT,
        "user_prompt": user_prompt,
        "bounded_payload": bounded_payload,
        "coverage": {
            "claims": catalog["retention"],
            "included_claim_ids": included,
            "omitted_claim_ids": omitted,
            "note": note_report,
        },
        "sources": sources,
        "claim_catalogs": [catalog],
        "source_identity": {
            "paper_id": str(paper_id),
            "source_file_hash": source_file_hash,
            "claim_catalog_signature": catalog["catalog_signature"],
            "notes_hash": note_report["text_hash"],
        },
    })


def build_concept_compile_plan(
    *,
    concept_id: str | int,
    title: str,
    description: str,
    source_papers: Sequence[Mapping[str, Any]],
    model: str,
    locale: str,
    generation_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(title, str) or not title.strip():
        raise ShadowCompileError("title must be non-empty")
    if not isinstance(description, str):
        raise ShadowCompileError("description must be a string")
    if isinstance(concept_id, bool) or not isinstance(concept_id, (str, int)):
        raise ShadowCompileError("concept_id must be a string or integer")
    if not str(concept_id).strip():
        raise ShadowCompileError("concept_id must be non-empty")
    if not isinstance(model, str) or not model.strip():
        raise ShadowCompileError("model must be non-empty")
    if not isinstance(locale, str) or not locale.strip():
        raise ShadowCompileError("locale must be non-empty")
    if not source_papers:
        raise ShadowCompileError("concept requires at least one source paper")
    paper_count = len(source_papers)
    quota = min(CONCEPT_MAX_PER_PAPER, CONCEPT_TOTAL_CHARACTERS // paper_count)
    catalogs: list[dict[str, Any]] = []
    capsules: list[dict[str, Any]] = []
    all_sources: list[dict[str, Any]] = []
    seen_papers: set[str] = set()
    for source in source_papers:
        if not isinstance(source, Mapping):
            raise ShadowCompileError("source papers must be objects")
        paper_id = str(source.get("paper_id") or "").strip()
        paper_title = str(source.get("title") or "").strip()
        extraction = source.get("extraction")
        if not paper_id or paper_id in seen_papers:
            raise ShadowCompileError("source paper ids must be unique and non-empty")
        if not paper_title or not isinstance(extraction, Mapping):
            raise ShadowCompileError("source paper title/extraction is invalid")
        seen_papers.add(paper_id)
        catalog = build_claim_catalog(paper_id=paper_id, extraction=extraction)
        rendered, included, omitted, included_roots = _render_concept_capsule(
            catalog, max_characters=quota
        )
        catalogs.append(catalog)
        included_set = set(included)
        all_sources.extend(
            source for source in _claim_sources(catalog)
            if source["source_id"] in included_set
        )
        capsules.append({
            "paper_id": paper_id,
            "title": paper_title,
            "quota_characters": quota,
            "claims_text": rendered,
            "included_claim_ids": included,
            "omitted_claim_ids": omitted,
            "included_roots": included_roots,
            "catalog_signature": catalog["catalog_signature"],
        })
    bounded_payload = {
        "concept_id": str(concept_id),
        "title": title.strip(),
        "description": description.strip()[:2_000],
        "source_capsules": capsules,
    }
    capsule_text = "\n\n".join(
        f"PAPER {capsule['paper_id']} — {capsule['title']}\n"
        f"{capsule['claims_text'] or '[no claims retained]'}\n"
        f"[omitted_claims={len(capsule['omitted_claim_ids'])}]"
        for capsule in capsules
    )
    user_prompt = (
        f"concept_id: {concept_id}\ntitle: {title.strip()}\n"
        f"description: {description.strip()[:2_000]}\n\n{capsule_text}\n\n"
        "Return JSON: {paragraphs:[{section,text,source_claim_ids:[...]}]}."
    )
    return _finalize_plan({
        "plan_schema_version": 2,
        "kind": "concept",
        "entity_id": str(concept_id),
        "title": title.strip(),
        "template_version": CONCEPT_TEMPLATE_VERSION,
        "model": model.strip(),
        "locale": locale.strip(),
        "generation_config": _generation_config(generation_config),
        "system_prompt": CONCEPT_SYSTEM_PROMPT,
        "user_prompt": user_prompt,
        "bounded_payload": bounded_payload,
        "coverage": {
            "source_paper_count": paper_count,
            "per_paper_quota": quota,
            "papers": [{
                "paper_id": item["paper_id"],
                "included_claim_count": len(item["included_claim_ids"]),
                "omitted_claim_count": len(item["omitted_claim_ids"]),
                "included_roots": item["included_roots"],
            } for item in capsules],
        },
        "sources": all_sources,
        "claim_catalogs": catalogs,
        "source_identity": {
            "concept_id": str(concept_id),
            "ordered_source_paper_ids": [item["paper_id"] for item in capsules],
            "capsule_hashes": [
                _sha256_text(_stable_json(item)) for item in capsules
            ],
        },
    })


def validate_synthesis(
    plan: Mapping[str, Any], synthesis: Mapping[str, Any]
) -> list[dict[str, Any]]:
    validate_compile_plan(plan)
    if not isinstance(synthesis, Mapping):
        raise ShadowCompileError("synthesis must be an object")
    paragraphs = synthesis.get("paragraphs")
    if not isinstance(paragraphs, list) or not paragraphs:
        raise ShadowCompileError("synthesis paragraphs must be non-empty")
    allowed = {
        source["source_id"] for source in plan.get("sources", [])
        if source.get("source_id")
    }
    validated: list[dict[str, Any]] = []
    for paragraph in paragraphs:
        if not isinstance(paragraph, Mapping):
            raise ShadowCompileError("each paragraph must be an object")
        section = paragraph.get("section")
        text = paragraph.get("text")
        source_ids = paragraph.get("source_claim_ids")
        if not isinstance(section, str) or not section.strip():
            raise ShadowCompileError("paragraph section must be non-empty")
        if not isinstance(text, str) or not text.strip():
            raise ShadowCompileError("paragraph text must be non-empty")
        if (
            not isinstance(source_ids, list)
            or not source_ids
            or any(not isinstance(item, str) for item in source_ids)
        ):
            raise ShadowCompileError("paragraph source_claim_ids must be non-empty")
        source_ids = list(dict.fromkeys(source_ids))
        unknown = set(source_ids) - allowed
        if unknown:
            raise ShadowCompileError("paragraph references an unknown source claim")
        validated.append({
            "section": section.strip(),
            "text": text.strip(),
            "source_claim_ids": source_ids,
        })
    return validated


def _render_markdown(plan: Mapping[str, Any], paragraphs: list[dict[str, Any]]) -> str:
    lines = [f"# {plan['title']}"]
    current_section = None
    for paragraph in paragraphs:
        if paragraph["section"] != current_section:
            current_section = paragraph["section"]
            lines.extend(("", f"## {current_section}"))
        sources = ",".join(paragraph["source_claim_ids"])
        lines.extend(("", paragraph["text"], f"<!-- source_claim_ids:{sources} -->"))
    return "\n".join(lines).rstrip() + "\n"


def _safe_shadow_root(output_root: Path, production_wiki_root: Path) -> Path:
    if not isinstance(output_root, Path) or not isinstance(production_wiki_root, Path):
        raise ShadowCompileError("output and production roots must be Paths")
    output = output_root.resolve()
    production = production_wiki_root.resolve()
    if (
        output == production
        or output.is_relative_to(production)
        or production.is_relative_to(output)
    ):
        raise ShadowCompileError("shadow output must not overlap production Wiki")
    return output


def _safe_shadow_target(root: Path, target: Path, production: Path) -> Path:
    try:
        relative = target.relative_to(root)
    except ValueError as error:
        raise ShadowCompileError("shadow target escapes output root") from error
    cursor = root
    for component in relative.parts[:-1]:
        cursor = cursor / component
        if cursor.is_symlink():
            raise ShadowCompileError("shadow target contains a symlink component")
    if target.is_symlink():
        raise ShadowCompileError("shadow target must not be a symlink")
    resolved = target.resolve()
    if not resolved.is_relative_to(root):
        raise ShadowCompileError("resolved shadow target escapes output root")
    if (
        resolved == production
        or resolved.is_relative_to(production)
        or production.is_relative_to(resolved)
    ):
        raise ShadowCompileError("shadow target overlaps production Wiki")
    return target


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def write_shadow_artifacts(
    *,
    plan: Mapping[str, Any],
    synthesis: Mapping[str, Any],
    output_root: Path,
    production_wiki_root: Path,
) -> dict[str, Path]:
    """Validate and write Markdown + lineage sidecar to an explicit shadow root."""
    validate_compile_plan(plan)
    root = _safe_shadow_root(output_root, production_wiki_root)
    paragraphs = validate_synthesis(plan, synthesis)
    kind = plan.get("kind")
    if kind not in {"paper", "concept"}:
        raise ShadowCompileError("unsupported plan kind")
    entity_id = str(plan.get("entity_id") or "")
    readable_id = re.sub(r"[^A-Za-z0-9._-]+", "-", entity_id).strip("-")[:60]
    if not readable_id:
        raise ShadowCompileError("invalid plan entity id")
    safe_id = f"{readable_id}-{_sha256_text(entity_id)[:12]}"
    markdown_path = root / f"{kind}s" / f"{safe_id}.md"
    sidecar_path = root / "claims" / f"{kind}-{safe_id}.claims.json"
    manifest_path = root / "runs" / f"{plan['source_signature_v2']}.json"
    production = production_wiki_root.resolve()
    for target in (markdown_path, sidecar_path, manifest_path):
        _safe_shadow_target(root, target, production)
    markdown = _render_markdown(plan, paragraphs)
    source_by_id = {
        source["source_id"]: source
        for source in plan["sources"]
        if source.get("source_id")
    }
    sidecar = {
        "schema_version": 1,
        "kind": kind,
        "entity_id": entity_id,
        "source_signature_v2": plan["source_signature_v2"],
        "support_level": "extraction_attribution",
        "evidence_status": "unavailable_or_candidate_only",
        "paragraphs": [{
            **paragraph,
            "sources": [source_by_id[source_id] for source_id in paragraph["source_claim_ids"]],
        } for paragraph in paragraphs],
    }
    rendered_sidecar = json.dumps(
        sidecar, ensure_ascii=False, sort_keys=True, indent=2
    ) + "\n"
    run_manifest = {
        "compiler_version": SHADOW_COMPILER_VERSION,
        "source_signature_v2": plan["source_signature_v2"],
        "kind": kind,
        "entity_id": entity_id,
        "coverage": plan["coverage"],
        "artifacts": {
            "markdown": str(markdown_path.relative_to(root)),
            "claims": str(sidecar_path.relative_to(root)),
            "markdown_sha256": _sha256_text(markdown),
            "claims_sha256": _sha256_text(rendered_sidecar),
        },
    }
    _atomic_write(markdown_path, markdown)
    _atomic_write(
        sidecar_path,
        rendered_sidecar,
    )
    _atomic_write(
        manifest_path,
        json.dumps(run_manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
    )
    return {
        "markdown": markdown_path,
        "claims": sidecar_path,
        "run_manifest": manifest_path,
    }
