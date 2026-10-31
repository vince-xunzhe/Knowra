"""Deterministic page evidence for papers, independent of Wiki compilation.

This module is deliberately not wired into application startup or the existing
paper pipeline. Callers must explicitly provide an output path if they choose
to persist a manifest. That keeps existing libraries migration-free while the
evidence contract is validated on synthetic data.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import io
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Iterable

import pypdf
import pypdfium2


EVIDENCE_SCHEMA_VERSION = 1
PARSER_NAME = "pypdf_with_pdfium_page_fallback"
DEFAULT_CHUNK_CHARS = 2400
DEFAULT_CHUNK_OVERLAP = 240
TEXT_NORMALIZATION = "surrogate_replacement_and_outer_strip_v1"

_HEADING_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("abstract", re.compile(r"^(?:abstract|摘要)$", re.IGNORECASE)),
    ("introduction", re.compile(r"^(?:\d+(?:\.\d+)*[.\s]*)?(?:introduction|引言|介绍)$", re.IGNORECASE)),
    ("background", re.compile(r"^(?:\d+(?:\.\d+)*[.\s]*)?(?:background|preliminaries|背景|预备知识)$", re.IGNORECASE)),
    ("related_work", re.compile(r"^(?:\d+(?:\.\d+)*[.\s]*)?(?:related work|相关工作)$", re.IGNORECASE)),
    ("method", re.compile(r"^(?:\d+(?:\.\d+)*[.\s]*)?(?:method|methods|methodology|approach|model|方法|模型)$", re.IGNORECASE)),
    ("experiments", re.compile(r"^(?:\d+(?:\.\d+)*[.\s]*)?(?:experiment|experiments|experimental setup|实验|实验设置)$", re.IGNORECASE)),
    ("results", re.compile(r"^(?:\d+(?:\.\d+)*[.\s]*)?(?:result|results|evaluation|结果|评估)$", re.IGNORECASE)),
    ("discussion", re.compile(r"^(?:\d+(?:\.\d+)*[.\s]*)?(?:discussion|讨论)$", re.IGNORECASE)),
    ("limitations", re.compile(r"^(?:\d+(?:\.\d+)*[.\s]*)?(?:limitation|limitations|局限|局限性)$", re.IGNORECASE)),
    ("conclusion", re.compile(r"^(?:\d+(?:\.\d+)*[.\s]*)?(?:conclusion|conclusions|总结|结论)$", re.IGNORECASE)),
    ("references", re.compile(r"^(?:references|bibliography|参考文献)$", re.IGNORECASE)),
    ("appendix", re.compile(r"^(?:appendix(?:\s+[A-Z])?|附录)$", re.IGNORECASE)),
)


class EvidenceValidationError(ValueError):
    """Raised when a manifest cannot be trusted as traceable evidence."""


def _sanitize_text(text: str) -> str:
    if not text:
        return ""
    return "".join(
        "\ufffd" if 0xD800 <= ord(character) <= 0xDFFF else character
        for character in text
    )


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parser_version() -> str:
    return (
        f"pypdf-{getattr(pypdf, '__version__', 'unknown')}+"
        f"pypdfium2-{importlib.metadata.version('pypdfium2')}"
    )


def _parser_label(name: str, version: str) -> str:
    return f"{name}-{version}"


def _primary_parser_label() -> str:
    return _parser_label("pypdf", getattr(pypdf, "__version__", "unknown"))


def _fallback_parser_label() -> str:
    return _parser_label(
        "pypdfium2", importlib.metadata.version("pypdfium2")
    )


def _open_pdfium_document(source_bytes: bytes):
    return pypdfium2.PdfDocument(source_bytes)


def _extract_pdfium_page(document: Any, index: int) -> str:
    page = document[index]
    try:
        text_page = page.get_textpage()
        try:
            return text_page.get_text_bounded()
        finally:
            text_page.close()
    finally:
        page.close()


def _error_type(error: Exception) -> str:
    return type(error).__name__


def _page_provenance(item: dict[str, Any]) -> dict[str, Any]:
    fields = ("parser_used", "primary_error_type", "fallback_error_type")
    if not any(field in item for field in fields):
        return {}
    return {field: item.get(field) for field in fields}


def _heading_kind(line: str) -> str | None:
    candidate = re.sub(r"\s+", " ", line.strip())
    if not candidate or len(candidate) > 120:
        return None
    for kind, pattern in _HEADING_PATTERNS:
        if pattern.fullmatch(candidate):
            return kind
    return None


def _section_ranges(
    text: str,
    inherited_kind: str | None,
    inherited_heading: str | None,
) -> tuple[list[dict[str, Any]], str | None, str | None]:
    headings: list[tuple[int, str, str]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        heading = line.strip()
        kind = _heading_kind(heading)
        if kind:
            headings.append((offset, kind, heading))
        offset += len(line)

    if not headings:
        return ([{
            "start": 0,
            "end": len(text),
            "section_kind": inherited_kind or "unknown",
            "section_heading": inherited_heading,
            "section_source": "inherited" if inherited_kind else "unknown",
        }], inherited_kind, inherited_heading)

    ranges: list[dict[str, Any]] = []
    first_start = headings[0][0]
    if first_start > 0:
        ranges.append({
            "start": 0,
            "end": first_start,
            "section_kind": inherited_kind or "unknown",
            "section_heading": inherited_heading,
            "section_source": "inherited" if inherited_kind else "unknown",
        })
    for index, (start, kind, heading) in enumerate(headings):
        end = headings[index + 1][0] if index + 1 < len(headings) else len(text)
        ranges.append({
            "start": start,
            "end": end,
            "section_kind": kind,
            "section_heading": heading,
            "section_source": "heading_heuristic",
        })
    final_kind = headings[-1][1]
    final_heading = headings[-1][2]
    return ranges, final_kind, final_heading


def _bounded_end(text: str, start: int, hard_end: int, maximum: int) -> int:
    proposed = min(hard_end, start + maximum)
    if proposed >= hard_end:
        return hard_end
    soft_start = start + int(maximum * 0.7)
    for separator in ("\n\n", "\n", ". ", "。"):
        split = text.rfind(separator, soft_start, proposed)
        if split >= soft_start:
            return split + len(separator)
    return proposed


def _chunk_section(
    *,
    paper_id: str,
    source_file_hash: str,
    page_number: int,
    page_text: str,
    section: dict[str, Any],
    chunk_chars: int,
    overlap_chars: int,
) -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = []
    cursor = section["start"]
    section_end = section["end"]
    while cursor < section_end:
        end = _bounded_end(page_text, cursor, section_end, chunk_chars)
        raw = page_text[cursor:end]
        leading = len(raw) - len(raw.lstrip())
        trailing = len(raw) - len(raw.rstrip())
        content_start = cursor + leading
        content_end = end - trailing
        content = page_text[content_start:content_end]
        if content:
            text_hash = _sha256_text(content)
            identity = "|".join((
                str(EVIDENCE_SCHEMA_VERSION),
                source_file_hash,
                str(paper_id),
                str(page_number),
                str(content_start),
                str(content_end),
                section["section_kind"],
                text_hash,
            ))
            chunks.append({
                "id": f"evidence:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}",
                "paper_id": str(paper_id),
                "page_number": page_number,
                "page_char_start": content_start,
                "page_char_end": content_end,
                "section_kind": section["section_kind"],
                "section_heading": section["section_heading"],
                "section_source": section["section_source"],
                "text": content,
                "text_hash": text_hash,
            })
        if end >= section_end:
            break
        next_cursor = max(cursor + 1, end - overlap_chars)
        cursor = next_cursor
    return chunks


def _manifest_identifier(manifest_without_id: dict[str, Any]) -> str:
    serialized = json.dumps(
        manifest_without_id,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"manifest:{_sha256_text(serialized)}"


def build_evidence_manifest(
    *,
    paper_id: str | int,
    source_file_hash: str,
    source_size_bytes: int,
    total_pages: int | None,
    pages: Iterable[dict[str, Any]],
    parser_name: str = PARSER_NAME,
    parser_version: str | None = None,
    document_error_type: str | None = None,
    document_primary_error_type: str | None = None,
    document_fallback_error_type: str | None = None,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_CHUNK_OVERLAP,
) -> dict[str, Any]:
    """Build a deterministic manifest from ordered page extraction results.

    Each page item must contain ``page_number`` and ``status``. ``ok`` pages
    additionally contain complete untruncated page ``text``. ``empty`` pages
    intentionally have no text. ``error`` pages include only ``error_type``.
    """
    if not str(paper_id).strip() or str(paper_id) != str(paper_id).strip():
        raise EvidenceValidationError("paper_id must be non-empty")
    if not re.fullmatch(r"[0-9a-f]{64}", source_file_hash):
        raise EvidenceValidationError("source_file_hash must be a lowercase SHA-256")
    if (
        isinstance(source_size_bytes, bool)
        or not isinstance(source_size_bytes, int)
        or source_size_bytes < 0
    ):
        raise EvidenceValidationError("source_size_bytes must be non-negative")
    if total_pages is not None and (
        isinstance(total_pages, bool)
        or not isinstance(total_pages, int)
        or total_pages < 0
    ):
        raise EvidenceValidationError("total_pages must be non-negative or null")
    if isinstance(chunk_chars, bool) or not isinstance(chunk_chars, int) or chunk_chars < 200:
        raise EvidenceValidationError("chunk_chars must be at least 200")
    if (
        isinstance(overlap_chars, bool)
        or not isinstance(overlap_chars, int)
        or overlap_chars < 0
        or overlap_chars >= chunk_chars
    ):
        raise EvidenceValidationError("overlap_chars must be within chunk bounds")

    page_items = list(pages)
    for item in page_items:
        if not isinstance(item, dict):
            raise EvidenceValidationError("each page must be an object")
        number = item.get("page_number")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise EvidenceValidationError("page_number must be a positive integer")
    ordered_pages = sorted(page_items, key=lambda item: item["page_number"])
    page_records: list[dict[str, Any]] = []
    chunks: list[dict[str, Any]] = []
    inherited_kind: str | None = None
    inherited_heading: str | None = None
    seen_pages: set[int] = set()
    for item in ordered_pages:
        number = item.get("page_number")
        status = item.get("status")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise EvidenceValidationError("page_number must be a positive integer")
        if number in seen_pages:
            raise EvidenceValidationError(f"duplicate page_number: {number}")
        seen_pages.add(number)
        if status not in {"ok", "empty", "error"}:
            raise EvidenceValidationError(f"invalid page status for page {number}")
        if total_pages is not None and number > total_pages:
            raise EvidenceValidationError(f"page {number} exceeds total_pages")

        if status == "ok":
            raw_text = item.get("text")
            if not isinstance(raw_text, str):
                raise EvidenceValidationError(f"ok page {number} text must be a string")
            text = _sanitize_text(raw_text).strip()
            if not text:
                raise EvidenceValidationError(f"ok page {number} has no text")
            ranges, inherited_kind, inherited_heading = _section_ranges(
                text, inherited_kind, inherited_heading
            )
            page_chunks: list[dict[str, Any]] = []
            for section in ranges:
                page_chunks.extend(_chunk_section(
                    paper_id=str(paper_id),
                    source_file_hash=source_file_hash,
                    page_number=number,
                    page_text=text,
                    section=section,
                    chunk_chars=chunk_chars,
                    overlap_chars=overlap_chars,
                ))
            chunks.extend(page_chunks)
            page_record = {
                "page_number": number,
                "status": "ok",
                "text_characters": len(text),
                "text_hash": _sha256_text(text),
                "chunk_ids": [chunk["id"] for chunk in page_chunks],
                "section_kinds": list(dict.fromkeys(
                    chunk["section_kind"] for chunk in page_chunks
                )),
                "error_type": None,
            }
            page_record.update(_page_provenance(item))
            page_records.append(page_record)
        elif status == "empty":
            page_record = {
                "page_number": number,
                "status": "empty",
                "text_characters": 0,
                "text_hash": _sha256_text(""),
                "chunk_ids": [],
                "section_kinds": [],
                "error_type": None,
            }
            page_record.update(_page_provenance(item))
            page_records.append(page_record)
        else:
            error_type = str(item.get("error_type") or "UnknownError").strip()
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,127}", error_type):
                raise EvidenceValidationError(f"invalid error_type for page {number}")
            page_record = {
                "page_number": number,
                "status": "error",
                "text_characters": 0,
                "text_hash": None,
                "chunk_ids": [],
                "section_kinds": [],
                "error_type": error_type,
            }
            page_record.update(_page_provenance(item))
            page_records.append(page_record)

    attempted = len(page_records)
    if total_pages is not None and seen_pages != set(range(1, total_pages + 1)):
        raise EvidenceValidationError("every source page must have a page record")
    ok_pages = sum(page["status"] == "ok" for page in page_records)
    empty_pages = sum(page["status"] == "empty" for page in page_records)
    failed_pages = sum(page["status"] == "error" for page in page_records)
    denominator = total_pages or 0
    coverage = {
        "source_pages": total_pages,
        "attempted_pages": attempted,
        "ok_pages": ok_pages,
        "empty_pages": empty_pages,
        "failed_pages": failed_pages,
        "pages_with_chunks": sum(bool(page["chunk_ids"]) for page in page_records),
        "extracted_characters": sum(page["text_characters"] for page in page_records),
        "chunk_count": len(chunks),
        "attempted_page_ratio": round(attempted / denominator, 6) if denominator else None,
        "text_page_ratio": round(ok_pages / denominator, 6) if denominator else None,
        "failure_page_ratio": round(failed_pages / denominator, 6) if denominator else None,
        "page_iteration_complete": (
            document_error_type is None
            and total_pages is not None
            and attempted == total_pages
            and failed_pages == 0
        ),
        "has_usable_text": ok_pages > 0,
    }
    manifest: dict[str, Any] = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "paper_id": str(paper_id),
        "source": {
            "file_hash_sha256": source_file_hash,
            "size_bytes": source_size_bytes,
            "total_pages": total_pages,
        },
        "parser": {
            "name": parser_name,
            "version": parser_version or _parser_version(),
            "text_normalization": TEXT_NORMALIZATION,
            "document_error_type": document_error_type,
            "document_primary_error_type": document_primary_error_type,
            "document_fallback_error_type": document_fallback_error_type,
        },
        "chunking": {
            "max_characters": chunk_chars,
            "overlap_characters": overlap_chars,
            "section_detection": "bounded_heading_heuristic_v1",
        },
        "coverage": coverage,
        "pages": page_records,
        "chunks": chunks,
    }
    manifest["manifest_id"] = _manifest_identifier(manifest)
    validate_evidence_manifest(manifest)
    return manifest


def build_pdf_evidence(
    filepath: str | Path,
    *,
    paper_id: str | int,
    source_file_hash: str | None = None,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_chars: int = DEFAULT_CHUNK_OVERLAP,
) -> dict[str, Any]:
    """Read every PDF page and return evidence without writing any artifact."""
    path = Path(filepath)
    source_bytes = path.read_bytes()
    actual_file_hash = hashlib.sha256(source_bytes).hexdigest()
    if source_file_hash is not None and source_file_hash != actual_file_hash:
        raise EvidenceValidationError("provided source hash does not match the PDF")
    file_hash = actual_file_hash
    size = len(source_bytes)
    reader = None
    fallback_document = None
    document_primary_error_type = None
    pages: list[dict[str, Any]] = []
    try:
        try:
            reader = pypdf.PdfReader(io.BytesIO(source_bytes))
            total_pages = len(reader.pages)
        except Exception as error:
            document_primary_error_type = _error_type(error)
            try:
                fallback_document = _open_pdfium_document(source_bytes)
                total_pages = len(fallback_document)
            except Exception as fallback_error:
                return build_evidence_manifest(
                    paper_id=paper_id,
                    source_file_hash=file_hash,
                    source_size_bytes=size,
                    total_pages=None,
                    pages=[],
                    document_error_type=document_primary_error_type,
                    document_primary_error_type=document_primary_error_type,
                    document_fallback_error_type=_error_type(fallback_error),
                    chunk_chars=chunk_chars,
                    overlap_chars=overlap_chars,
                )

        for number in range(1, total_pages + 1):
            primary_error_type = document_primary_error_type
            if reader is not None:
                try:
                    page = reader.pages[number - 1]
                    text = _sanitize_text(page.extract_text() or "").strip()
                    pages.append({
                        "page_number": number,
                        "status": "ok" if text else "empty",
                        "text": text,
                        "parser_used": _primary_parser_label(),
                        "primary_error_type": None,
                        "fallback_error_type": None,
                    })
                    continue
                except Exception as error:
                    primary_error_type = _error_type(error)

            try:
                if fallback_document is None:
                    fallback_document = _open_pdfium_document(source_bytes)
                text = _sanitize_text(
                    _extract_pdfium_page(fallback_document, number - 1) or ""
                ).strip()
                pages.append({
                    "page_number": number,
                    "status": "ok" if text else "empty",
                    "text": text,
                    "parser_used": _fallback_parser_label(),
                    "primary_error_type": primary_error_type,
                    "fallback_error_type": None,
                })
            except Exception as fallback_error:
                pages.append({
                    "page_number": number,
                    "status": "error",
                    "error_type": primary_error_type or _error_type(fallback_error),
                    "parser_used": None,
                    "primary_error_type": primary_error_type,
                    "fallback_error_type": _error_type(fallback_error),
                })
    finally:
        if fallback_document is not None:
            fallback_document.close()
    return build_evidence_manifest(
        paper_id=paper_id,
        source_file_hash=file_hash,
        source_size_bytes=size,
        total_pages=total_pages,
        pages=pages,
        document_primary_error_type=document_primary_error_type,
        chunk_chars=chunk_chars,
        overlap_chars=overlap_chars,
    )


def _expected_chunk_id(chunk: dict[str, Any], source_hash: str) -> str:
    identity = "|".join((
        str(EVIDENCE_SCHEMA_VERSION),
        source_hash,
        str(chunk["paper_id"]),
        str(chunk["page_number"]),
        str(chunk["page_char_start"]),
        str(chunk["page_char_end"]),
        str(chunk["section_kind"]),
        str(chunk["text_hash"]),
    ))
    return f"evidence:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"


def validate_evidence_manifest(manifest: dict[str, Any]) -> None:
    if manifest.get("schema_version") != EVIDENCE_SCHEMA_VERSION:
        raise EvidenceValidationError("unsupported evidence schema")
    source = manifest.get("source") or {}
    source_hash = source.get("file_hash_sha256")
    if not isinstance(source_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", source_hash):
        raise EvidenceValidationError("invalid source hash")
    paper_id = manifest.get("paper_id")
    if (
        not isinstance(paper_id, str)
        or not paper_id.strip()
        or paper_id != paper_id.strip()
    ):
        raise EvidenceValidationError("invalid paper_id")
    size = source.get("size_bytes")
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise EvidenceValidationError("invalid source size")
    total_pages = source.get("total_pages")
    if total_pages is not None and (
        isinstance(total_pages, bool)
        or not isinstance(total_pages, int)
        or total_pages < 0
    ):
        raise EvidenceValidationError("invalid source page count")
    parser = manifest.get("parser") or {}
    parser_name = parser.get("name")
    parser_version = parser.get("version")
    if (
        not isinstance(parser_name, str)
        or not parser_name.strip()
        or parser_name != parser_name.strip()
        or not isinstance(parser_version, str)
        or not parser_version.strip()
        or parser_version != parser_version.strip()
        or parser.get("text_normalization") != TEXT_NORMALIZATION
    ):
        raise EvidenceValidationError("invalid parser identity")
    document_error_type = parser.get("document_error_type")
    if document_error_type is not None and (
        not isinstance(document_error_type, str)
        or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_.]{0,127}", document_error_type
        )
    ):
        raise EvidenceValidationError("invalid document error type")
    document_primary_error_type = parser.get("document_primary_error_type")
    document_fallback_error_type = parser.get("document_fallback_error_type")
    for error_type in (document_primary_error_type, document_fallback_error_type):
        if error_type is not None and (
            not isinstance(error_type, str)
            or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]{0,127}", error_type)
        ):
            raise EvidenceValidationError("invalid document fallback metadata")
    if document_fallback_error_type is not None and document_error_type is None:
        raise EvidenceValidationError("orphaned document fallback error")
    if document_error_type is not None and document_primary_error_type != document_error_type:
        raise EvidenceValidationError("document primary error mismatch")
    pages = manifest.get("pages")
    chunks = manifest.get("chunks")
    if not isinstance(pages, list) or not isinstance(chunks, list):
        raise EvidenceValidationError("pages and chunks must be lists")
    chunking = manifest.get("chunking") or {}
    chunk_characters = chunking.get("max_characters")
    overlap_characters = chunking.get("overlap_characters")
    if (
        isinstance(chunk_characters, bool)
        or not isinstance(chunk_characters, int)
        or chunk_characters < 200
        or isinstance(overlap_characters, bool)
        or not isinstance(overlap_characters, int)
        or overlap_characters < 0
        or overlap_characters >= chunk_characters
        or chunking.get("section_detection") != "bounded_heading_heuristic_v1"
    ):
        raise EvidenceValidationError("invalid chunking configuration")
    if document_error_type is not None and (total_pages is not None or pages or chunks):
        raise EvidenceValidationError("document error manifest must not claim pages")

    page_by_number: dict[int, dict[str, Any]] = {}
    chunk_by_id: dict[str, dict[str, Any]] = {}
    for page in pages:
        if not isinstance(page, dict):
            raise EvidenceValidationError("each page record must be an object")
        number = page.get("page_number")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            raise EvidenceValidationError("invalid page number")
        if number in page_by_number:
            raise EvidenceValidationError("duplicate page record")
        if total_pages is not None and number > total_pages:
            raise EvidenceValidationError("page exceeds source page count")
        status = page.get("status")
        if status not in {"ok", "empty", "error"}:
            raise EvidenceValidationError("invalid page status")
        provenance_present = any(
            field in page
            for field in ("parser_used", "primary_error_type", "fallback_error_type")
        )
        parser_used = page.get("parser_used")
        primary_error_type = page.get("primary_error_type")
        fallback_error_type = page.get("fallback_error_type")
        if provenance_present:
            if status in {"ok", "empty"}:
                if (
                    not isinstance(parser_used, str)
                    or not parser_used.strip()
                    or len(parser_used) > 160
                    or fallback_error_type is not None
                    or (primary_error_type is None) != parser_used.startswith("pypdf-")
                    or (primary_error_type is not None) != parser_used.startswith(
                        "pypdfium2-"
                    )
                ):
                    raise EvidenceValidationError("invalid successful-page provenance")
            elif parser_used is not None:
                raise EvidenceValidationError("failed page cannot name a parser result")
            for error_type in (primary_error_type, fallback_error_type):
                if error_type is not None and (
                    not isinstance(error_type, str)
                    or not re.fullmatch(
                        r"[A-Za-z_][A-Za-z0-9_.]{0,127}", error_type
                    )
                ):
                    raise EvidenceValidationError("invalid page parser error metadata")
            if status == "error" and (
                primary_error_type is None or fallback_error_type is None
            ):
                raise EvidenceValidationError("incomplete failed-page provenance")
        characters = page.get("text_characters")
        if isinstance(characters, bool) or not isinstance(characters, int) or characters < 0:
            raise EvidenceValidationError("invalid page character count")
        if status == "ok":
            if characters < 1 or not re.fullmatch(
                r"[0-9a-f]{64}", str(page.get("text_hash") or "")
            ):
                raise EvidenceValidationError("invalid text-page metadata")
            if page.get("error_type") is not None:
                raise EvidenceValidationError("text page cannot contain an error")
        elif status == "empty":
            if characters != 0 or page.get("text_hash") != _sha256_text(""):
                raise EvidenceValidationError("invalid empty-page metadata")
            if page.get("error_type") is not None:
                raise EvidenceValidationError("empty page cannot contain an error")
        else:
            error_type = page.get("error_type")
            if characters != 0 or page.get("text_hash") is not None:
                raise EvidenceValidationError("invalid failed-page metadata")
            if not isinstance(error_type, str) or not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_.]{0,127}", error_type
            ):
                raise EvidenceValidationError("invalid failed-page error type")
            if provenance_present and error_type != primary_error_type:
                raise EvidenceValidationError("failed-page primary error mismatch")
        page_by_number[number] = page
    if total_pages is not None and set(page_by_number) != set(range(1, total_pages + 1)):
        raise EvidenceValidationError("source page records are incomplete")
    for chunk in chunks:
        if not isinstance(chunk, dict):
            raise EvidenceValidationError("each chunk must be an object")
        chunk_id = chunk.get("id")
        if not isinstance(chunk_id, str) or chunk_id in chunk_by_id:
            raise EvidenceValidationError("invalid or duplicate chunk id")
        if chunk.get("paper_id") != paper_id:
            raise EvidenceValidationError("chunk paper_id mismatch")
        page = page_by_number.get(chunk.get("page_number"))
        if page is None or page.get("status") != "ok":
            raise EvidenceValidationError("chunk points to a non-text page")
        text = chunk.get("text")
        if not isinstance(text, str) or not text:
            raise EvidenceValidationError("chunk text must be non-empty")
        if _sha256_text(text) != chunk.get("text_hash"):
            raise EvidenceValidationError("chunk text hash mismatch")
        if _expected_chunk_id(chunk, source_hash) != chunk_id:
            raise EvidenceValidationError("chunk identity mismatch")
        start = chunk.get("page_char_start")
        end = chunk.get("page_char_end")
        if (
            isinstance(start, bool)
            or not isinstance(start, int)
            or isinstance(end, bool)
            or not isinstance(end, int)
            or start < 0
            or end <= start
        ):
            raise EvidenceValidationError("invalid chunk page offsets")
        if len(text) != end - start or len(text) > chunk_characters:
            raise EvidenceValidationError("chunk text does not match its page span")
        if end > page.get("text_characters", 0):
            raise EvidenceValidationError("chunk exceeds page text bounds")
        section_kind = chunk.get("section_kind")
        section_heading = chunk.get("section_heading")
        if (
            not isinstance(section_kind, str)
            or not section_kind
            or len(section_kind) > 64
            or (
                section_heading is not None
                and (
                    not isinstance(section_heading, str)
                    or not section_heading
                    or len(section_heading) > 120
                )
            )
        ):
            raise EvidenceValidationError("invalid chunk section metadata")
        if chunk.get("section_source") not in {
            "heading_heuristic",
            "inherited",
            "unknown",
        }:
            raise EvidenceValidationError("invalid section provenance")
        chunk_by_id[chunk_id] = chunk

    for page in pages:
        chunk_ids = page.get("chunk_ids")
        if not isinstance(chunk_ids, list) or any(
            chunk_id not in chunk_by_id for chunk_id in chunk_ids
        ):
            raise EvidenceValidationError("page contains an invalid chunk reference")
        if len(chunk_ids) != len(set(chunk_ids)) or any(
            chunk_by_id[chunk_id].get("page_number") != page.get("page_number")
            for chunk_id in chunk_ids
        ):
            raise EvidenceValidationError("page chunk references are inconsistent")
        if page.get("status") != "ok" and chunk_ids:
            raise EvidenceValidationError("non-text page cannot contain chunks")
        expected_sections = list(dict.fromkeys(
            chunk_by_id[chunk_id]["section_kind"] for chunk_id in chunk_ids
        ))
        if page.get("section_kinds") != expected_sections:
            raise EvidenceValidationError("page section summary mismatch")

    referenced_chunk_ids = [
        chunk_id for page in pages for chunk_id in page.get("chunk_ids", [])
    ]
    if set(referenced_chunk_ids) != set(chunk_by_id):
        raise EvidenceValidationError("manifest contains unreferenced chunks")

    coverage = manifest.get("coverage") or {}
    expected = {
        "source_pages": total_pages,
        "attempted_pages": len(pages),
        "ok_pages": sum(page.get("status") == "ok" for page in pages),
        "empty_pages": sum(page.get("status") == "empty" for page in pages),
        "failed_pages": sum(page.get("status") == "error" for page in pages),
        "pages_with_chunks": sum(bool(page.get("chunk_ids")) for page in pages),
        "extracted_characters": sum(page.get("text_characters", 0) for page in pages),
        "chunk_count": len(chunks),
    }
    for key, value in expected.items():
        if coverage.get(key) != value:
            raise EvidenceValidationError(f"coverage mismatch: {key}")
    denominator = total_pages or 0
    expected_ratios = {
        "attempted_page_ratio": (
            round(len(pages) / denominator, 6) if denominator else None
        ),
        "text_page_ratio": (
            round(expected["ok_pages"] / denominator, 6) if denominator else None
        ),
        "failure_page_ratio": (
            round(expected["failed_pages"] / denominator, 6) if denominator else None
        ),
        "page_iteration_complete": (
            (manifest.get("parser") or {}).get("document_error_type") is None
            and total_pages is not None
            and len(pages) == total_pages
            and expected["failed_pages"] == 0
        ),
        "has_usable_text": expected["ok_pages"] > 0,
    }
    for key, value in expected_ratios.items():
        if coverage.get(key) != value:
            raise EvidenceValidationError(f"coverage mismatch: {key}")

    claimed_manifest_id = manifest.get("manifest_id")
    content = dict(manifest)
    content.pop("manifest_id", None)
    if claimed_manifest_id != _manifest_identifier(content):
        raise EvidenceValidationError("manifest identity mismatch")


def write_evidence_manifest(manifest: dict[str, Any], output_path: str | Path) -> Path:
    """Atomically write private evidence to an explicit, ignored local path.

    A manifest contains source-paper text. Callers must keep it out of Git and
    public artifacts; this function never chooses a repository or data path.
    """
    validate_evidence_manifest(manifest)
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(
        dir=target.parent,
        prefix=f".{target.name}.",
        suffix=".tmp",
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(manifest, stream, ensure_ascii=False, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        return target
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def load_evidence_manifest(path: str | Path) -> dict[str, Any]:
    manifest = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise EvidenceValidationError("manifest root must be an object")
    validate_evidence_manifest(manifest)
    return manifest
