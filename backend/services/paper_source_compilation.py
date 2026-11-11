"""Lightweight, source-complete paper Wiki compilation.

The historical Wiki compiler sent only the first 8,000 characters of the
structured extraction to one model call.  This module instead plans one
bounded compile call for every locally extracted source batch and concatenates
the page-ordered results.  It intentionally does not add the expensive claim
review pipeline: completeness here means that every extractable text chunk was
presented to a compile call, not that every semantic fact was retained.

The source manifest remains private local data.  Only callers that explicitly
choose a data path persist it, and the user-facing page endpoint exposes one
requested page at a time.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from services.paper_evidence_service import (
    load_evidence_manifest,
    validate_evidence_manifest,
    write_evidence_manifest,
)
from services.paper_extraction_hierarchy import (
    DEFAULT_MAP_INPUT_CHARACTERS,
    ExtractionCheckpoint,
    ExtractionCoverageError,
    SourceBatch,
    build_source_batches,
)


SOURCE_WIKI_POLICY = "full-source-wiki-v1"
MAX_SECTION_OUTPUT_CHARACTERS = 12_000


class SourceWikiCompileError(RuntimeError):
    """Raised when a full-source Wiki cannot be compiled without omission."""


@dataclass(frozen=True)
class SourceWikiCompilation:
    body: str
    coverage: dict[str, Any]


def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def evidence_manifest_path(
    root: Path,
    paper_id: str,
    *,
    library_file_hash: str | None = None,
) -> Path:
    """Return a traversal-safe private manifest path for an opaque paper id."""
    identity = f"{paper_id}\0{str(library_file_hash or 'unversioned').lower()}"
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return root / f"{digest}.json"


def save_current_evidence_manifest(
    manifest: dict[str, Any],
    *,
    root: Path,
    library_file_hash: str | None = None,
) -> Path:
    validate_evidence_manifest(manifest)
    return write_evidence_manifest(
        manifest,
        evidence_manifest_path(
            root,
            str(manifest["paper_id"]),
            library_file_hash=library_file_hash,
        ),
    )


def load_current_evidence_manifest(
    *,
    root: Path,
    paper_id: str,
    library_file_hash: str | None = None,
    source_sha256: str | None = None,
) -> dict[str, Any] | None:
    path = evidence_manifest_path(
        root,
        paper_id,
        library_file_hash=library_file_hash,
    )
    if not path.is_file():
        return None
    try:
        manifest = load_evidence_manifest(path)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None
    if str(manifest.get("paper_id")) != str(paper_id):
        return None
    recorded_hash = str((manifest.get("source") or {}).get("file_hash_sha256") or "")
    if source_sha256 and recorded_hash != str(source_sha256).lower():
        return None
    return manifest


def source_page_payload(manifest: Mapping[str, Any], page_number: int) -> dict[str, Any]:
    """Return exact located chunks for one page, without exposing the whole PDF."""
    value = dict(manifest)
    validate_evidence_manifest(value)
    if page_number < 1:
        raise ValueError("page_number must be positive")
    page = next(
        (item for item in value["pages"] if int(item["page_number"]) == page_number),
        None,
    )
    if page is None:
        raise ValueError("page is outside the source manifest")
    chunks_by_id = {item["id"]: item for item in value["chunks"]}
    chunks = []
    for chunk_id in page.get("chunk_ids") or []:
        chunk = chunks_by_id[chunk_id]
        chunks.append({
            "evidence_id": chunk["id"],
            "page_number": int(chunk["page_number"]),
            "page_char_start": int(chunk["page_char_start"]),
            "page_char_end": int(chunk["page_char_end"]),
            "section_kind": chunk.get("section_kind") or "unknown",
            "section_heading": chunk.get("section_heading"),
            "text": chunk["text"],
            "text_hash": chunk["text_hash"],
        })
    return {
        "manifest_id": value["manifest_id"],
        "paper_id": str(value["paper_id"]),
        "source_sha256": value["source"]["file_hash_sha256"],
        "source_pages": value["coverage"]["source_pages"],
        "page": {
            "page_number": page_number,
            "status": page["status"],
            "text_characters": int(page.get("text_characters") or 0),
            "parser_used": page.get("parser_used"),
            "error_type": page.get("error_type"),
            "chunks": chunks,
        },
        "limitations": [
            "text spans reflect local PDF extraction and may not preserve visual table layout",
            "figures, diagrams, and formulas without extractable text are not represented",
        ],
    }


def source_evidence_summary(manifest: Mapping[str, Any]) -> dict[str, Any]:
    value = dict(manifest)
    validate_evidence_manifest(value)
    coverage = value["coverage"]
    return {
        "manifest_id": value["manifest_id"],
        "source_pages": coverage["source_pages"],
        "attempted_pages": coverage["attempted_pages"],
        "ok_pages": coverage["ok_pages"],
        "empty_pages": coverage["empty_pages"],
        "failed_pages": coverage["failed_pages"],
        "source_characters": coverage["extracted_characters"],
        "source_chunks": coverage["chunk_count"],
        "page_iteration_complete": coverage["page_iteration_complete"],
        "visual_coverage": "unsupported",
    }


def source_batch_prompt(batch: SourceBatch, *, index: int, total: int) -> str:
    return (
        "请把下面这一段论文原文编译为可读的中文 Wiki 小节。\n"
        "要求：\n"
        "1. 只写输入能够支持的内容，不做跨批次猜测。\n"
        "2. 保留技术名词、实验设置、数值、限制，以及表格表头与数据行的对应关系。\n"
        "3. 每个事实段落末尾标注原文页码，例如 [p.10]；原文已有 evidence_id 时不要改写。\n"
        "4. 从 ### 三级标题开始，不写一级或二级标题。\n"
        "5. 这是文本编译；不得声称看懂了仅存在于图片、图表布局或公式排版中的信息。\n\n"
        f"[BATCH {index}/{total} id={batch.batch_id} pages={list(batch.pages)}]\n"
        f"{batch.text}\n"
        "[/BATCH]"
    )


def compile_source_wiki(
    manifest: Mapping[str, Any],
    *,
    model: str,
    reasoning_effort: str | None,
    prompt_version: str,
    call_batch: Callable[[SourceBatch, int, int], str],
    checkpoint_root: Path | None = None,
    input_character_budget: int = DEFAULT_MAP_INPUT_CHARACTERS,
) -> SourceWikiCompilation:
    value = dict(manifest)
    validate_evidence_manifest(value)
    try:
        batches = build_source_batches(
            value,
            input_character_budget=input_character_budget,
        )
    except ExtractionCoverageError as error:
        raise SourceWikiCompileError(str(error)) from error
    if not batches:
        raise SourceWikiCompileError("paper has no locally extractable source batches")

    identity = {
        "policy": SOURCE_WIKI_POLICY,
        "source_sha256": value["source"]["file_hash_sha256"],
        "manifest_id": value["manifest_id"],
        "model": model,
        "reasoning_effort": reasoning_effort,
        "prompt_sha256": _sha256(prompt_version),
        "input_character_budget": input_character_budget,
    }
    checkpoint = (
        ExtractionCheckpoint(
            checkpoint_root,
            identity=identity,
            batch_descriptors=[batch.descriptor() for batch in batches],
        )
        if checkpoint_root is not None
        else None
    )

    rendered: list[str] = []
    calls = 0
    resumed = 0
    for index, batch in enumerate(batches, start=1):
        section = checkpoint.map_note(batch.batch_id) if checkpoint else None
        if section is None:
            section = str(call_batch(batch, index, len(batches)) or "").strip()
            if not section:
                raise SourceWikiCompileError(
                    f"Wiki compile returned no content for pages {list(batch.pages)}"
                )
            if len(section) > MAX_SECTION_OUTPUT_CHARACTERS:
                raise SourceWikiCompileError("Wiki batch output exceeds its safety bound")
            calls += 1
            if checkpoint:
                checkpoint.save_map_note(batch.batch_id, section)
        else:
            resumed += 1
        rendered.append(section)

    original = value["coverage"]
    coverage = {
        "policy": SOURCE_WIKI_POLICY,
        "source_sha256": value["source"]["file_hash_sha256"],
        "source_manifest_id": value["manifest_id"],
        "source_pages": original["source_pages"],
        "attempted_pages": original["attempted_pages"],
        "ok_pages": original["ok_pages"],
        "empty_pages": original["empty_pages"],
        "failed_pages": original["failed_pages"],
        "source_characters": original["extracted_characters"],
        "source_chunks": original["chunk_count"],
        "compile_batches": len(batches),
        "model_calls_this_attempt": calls,
        "resumed_batches": resumed,
        "text_coverage": "all_locally_extracted_chunks_presented",
        "semantic_completeness": "not_claimed",
        "visual_coverage": "unsupported",
    }
    preamble = (
        "## 全文编译\n\n"
        "> 本页按页序分批编译全部可本地提取的论文文本，并保留页码。"
        "这证明文本输入覆盖，不证明模型保留了每个语义细节；图片、图表布局和公式视觉含义未验证。\n\n"
    )
    return SourceWikiCompilation(
        body=preamble + "\n\n".join(rendered).strip() + "\n",
        coverage=coverage,
    )
