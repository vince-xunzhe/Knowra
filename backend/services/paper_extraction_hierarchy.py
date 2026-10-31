"""Bounded, resumable full-text preparation for local paper extraction.

The old local extraction path stopped after four source chunks.  This module
instead requires a validated page manifest, accounts for every extracted text
chunk, and reduces model notes hierarchically until the final prompt is
bounded.  Checkpoints contain derived notes only; source text remains in the
paper/evidence manifest and every cache identity is bound to the source,
prompt, model, reasoning setting, and chunk plan.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from services.paper_evidence_service import validate_evidence_manifest


HIERARCHY_POLICY_VERSION = "full-page-hierarchy-v1"
CHECKPOINT_SCHEMA_VERSION = 1
DEFAULT_MAP_INPUT_CHARACTERS = 12_000
DEFAULT_REDUCE_INPUT_CHARACTERS = 24_000
DEFAULT_FINAL_NOTES_CHARACTERS = 32_000
MAX_MAP_BATCHES = 96
MAX_REDUCE_LEVELS = 8
MAX_CHECKPOINT_NOTE_CHARACTERS = 12_000
MAX_CHECKPOINT_FINAL_CHARACTERS = 2_000_000


class ExtractionCoverageError(RuntimeError):
    """Raised instead of silently accepting incomplete source coverage."""


def _stable_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _page_ranges(pages: Sequence[int]) -> str:
    ordered = sorted(set(pages))
    if not ordered:
        return "none"
    ranges: list[str] = []
    start = previous = ordered[0]
    for page in ordered[1:]:
        if page == previous + 1:
            previous = page
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = page
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(ranges)


@dataclass(frozen=True)
class SourceBatch:
    batch_id: str
    text: str
    pages: tuple[int, ...]
    evidence_ids: tuple[str, ...]
    source_characters: int

    def descriptor(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "pages": list(self.pages),
            "evidence_ids": list(self.evidence_ids),
            "source_characters": self.source_characters,
            "text_hash": _sha256(self.text),
        }


@dataclass(frozen=True)
class NoteNode:
    node_id: str
    text: str
    pages: tuple[int, ...]
    source_batch_ids: tuple[str, ...]


@dataclass
class HierarchicalNotes:
    text: str
    coverage: dict[str, Any]
    checkpoint: "ExtractionCheckpoint | None"


def _source_block(chunk: Mapping[str, Any]) -> str:
    page = int(chunk["page_number"])
    start = int(chunk["page_char_start"])
    end = int(chunk["page_char_end"])
    section = str(chunk.get("section_kind") or "unknown")
    return (
        f"[SOURCE evidence_id={chunk['id']} page={page} chars={start}-{end} "
        f"section={section}]\n{chunk['text']}\n[/SOURCE]"
    )


def build_source_batches(
    manifest: Mapping[str, Any],
    *,
    input_character_budget: int = DEFAULT_MAP_INPUT_CHARACTERS,
    max_batches: int = MAX_MAP_BATCHES,
) -> list[SourceBatch]:
    """Pack every evidence chunk without dropping or prefix-truncating any."""
    validate_evidence_manifest(dict(manifest))
    coverage = manifest.get("coverage") or {}
    if not coverage.get("page_iteration_complete"):
        failed = [
            int(page["page_number"])
            for page in manifest.get("pages", [])
            if page.get("status") == "error"
        ]
        detail = _page_ranges(failed)
        raise ExtractionCoverageError(
            "PDF page coverage is incomplete; failed pages=" + detail
        )
    if not coverage.get("has_usable_text"):
        raise ExtractionCoverageError(
            "PDF has no locally extractable text; complete local extraction cannot be claimed"
        )
    if input_character_budget < 1_000:
        raise ValueError("map input budget must be at least 1000 characters")
    if max_batches < 1:
        raise ValueError("max_batches must be positive")

    batches: list[SourceBatch] = []
    blocks: list[str] = []
    pages: list[int] = []
    evidence_ids: list[str] = []
    source_characters = 0
    rendered_characters = 0

    def flush() -> None:
        nonlocal blocks, pages, evidence_ids, source_characters, rendered_characters
        if not blocks:
            return
        text = "\n\n".join(blocks)
        identity = _stable_json({
            "policy": HIERARCHY_POLICY_VERSION,
            "manifest_id": manifest["manifest_id"],
            "evidence_ids": evidence_ids,
            "text_hash": _sha256(text),
        })
        batches.append(SourceBatch(
            batch_id=f"map:{_sha256(identity)}",
            text=text,
            pages=tuple(sorted(set(pages))),
            evidence_ids=tuple(evidence_ids),
            source_characters=source_characters,
        ))
        blocks = []
        pages = []
        evidence_ids = []
        source_characters = 0
        rendered_characters = 0

    for chunk in manifest.get("chunks", []):
        block = _source_block(chunk)
        if len(block) > input_character_budget:
            raise ExtractionCoverageError(
                f"evidence chunk exceeds map input budget: {chunk['id']}"
            )
        if blocks and rendered_characters + 2 + len(block) > input_character_budget:
            flush()
        blocks.append(block)
        pages.append(int(chunk["page_number"]))
        evidence_ids.append(str(chunk["id"]))
        source_characters += len(str(chunk["text"]))
        rendered_characters += len(block) + (2 if rendered_characters else 0)
    flush()

    if len(batches) > max_batches:
        raise ExtractionCoverageError(
            "paper requires "
            f"{len(batches)} map calls, exceeding the explicit safety limit {max_batches}; "
            "no source chunk was sent or silently omitted"
        )
    planned = [item for batch in batches for item in batch.evidence_ids]
    expected = [str(chunk["id"]) for chunk in manifest.get("chunks", [])]
    if planned != expected:
        raise ExtractionCoverageError("source plan does not account for every evidence chunk")
    return batches


class ExtractionCheckpoint:
    """Atomic local checkpoint for map/reduce notes and a validated final result."""

    def __init__(
        self,
        root: Path,
        *,
        identity: Mapping[str, Any],
        batch_descriptors: Sequence[Mapping[str, Any]],
    ) -> None:
        self.identity = json.loads(_stable_json(identity))
        self.run_id = _sha256(_stable_json(self.identity))
        self.directory = root / self.run_id
        self.path = self.directory / "checkpoint.json"
        self.batch_descriptors = json.loads(_stable_json(list(batch_descriptors)))
        self.state = self._load_or_create()

    def _fresh(self) -> dict[str, Any]:
        return {
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "identity": self.identity,
            "batch_descriptors": self.batch_descriptors,
            "map_notes": {},
            "reduce_notes": {},
            "final": None,
        }

    def _load_or_create(self) -> dict[str, Any]:
        if not self.path.exists():
            return self._fresh()
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as error:
            raise ExtractionCoverageError("extraction checkpoint is unreadable") from error
        if (
            not isinstance(value, dict)
            or value.get("schema_version") != CHECKPOINT_SCHEMA_VERSION
            or value.get("identity") != self.identity
            or value.get("batch_descriptors") != self.batch_descriptors
            or not isinstance(value.get("map_notes"), dict)
            or not isinstance(value.get("reduce_notes"), dict)
        ):
            raise ExtractionCoverageError("extraction checkpoint identity is invalid")
        return value

    @staticmethod
    def _valid_text(value: Any, *, maximum: int) -> str | None:
        if not isinstance(value, dict):
            return None
        text = value.get("text")
        if (
            not isinstance(text, str)
            or not text.strip()
            or len(text) > maximum
            or value.get("text_hash") != _sha256(text)
        ):
            return None
        return text

    def map_note(self, batch_id: str) -> str | None:
        return self._valid_text(
            self.state["map_notes"].get(batch_id),
            maximum=MAX_CHECKPOINT_NOTE_CHARACTERS,
        )

    def reduce_note(self, group_id: str) -> str | None:
        return self._valid_text(
            self.state["reduce_notes"].get(group_id),
            maximum=MAX_CHECKPOINT_NOTE_CHARACTERS,
        )

    def final_response(self) -> str | None:
        return self._valid_text(
            self.state.get("final"), maximum=MAX_CHECKPOINT_FINAL_CHARACTERS
        )

    def save_map_note(self, batch_id: str, text: str) -> None:
        self.state["map_notes"][batch_id] = {"text": text, "text_hash": _sha256(text)}
        self._save()

    def save_reduce_note(self, group_id: str, text: str) -> None:
        self.state["reduce_notes"][group_id] = {"text": text, "text_hash": _sha256(text)}
        self._save()

    def save_final_response(self, text: str) -> None:
        if not text.strip() or len(text) > MAX_CHECKPOINT_FINAL_CHARACTERS:
            raise ExtractionCoverageError("final extraction response cannot be checkpointed")
        self.state["final"] = {"text": text, "text_hash": _sha256(text)}
        self._save()

    def _save(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.directory, 0o700)
        except OSError:
            pass
        fd, temporary = tempfile.mkstemp(
            prefix=".checkpoint-", suffix=".json", dir=self.directory
        )
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self.state, handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass


def _pack_note_groups(
    nodes: Sequence[NoteNode], *, input_character_budget: int
) -> list[list[NoteNode]]:
    groups: list[list[NoteNode]] = []
    current: list[NoteNode] = []
    current_characters = 0
    for node in nodes:
        rendered = _render_node(node)
        if len(rendered) > input_character_budget:
            raise ExtractionCoverageError(
                f"hierarchical note exceeds reduce input budget: {node.node_id}"
            )
        if current and current_characters + 2 + len(rendered) > input_character_budget:
            groups.append(current)
            current = []
            current_characters = 0
        current.append(node)
        current_characters += len(rendered) + (2 if current_characters else 0)
    if current:
        groups.append(current)
    return groups


def _render_node(node: NoteNode) -> str:
    return (
        f"[NOTE id={node.node_id} pages={_page_ranges(node.pages)}]\n"
        f"{node.text}\n[/NOTE]"
    )


def _group_identity(level: int, nodes: Sequence[NoteNode]) -> str:
    value = {
        "policy": HIERARCHY_POLICY_VERSION,
        "level": level,
        "nodes": [
            {
                "id": node.node_id,
                "text_hash": _sha256(node.text),
                "sources": list(node.source_batch_ids),
            }
            for node in nodes
        ],
    }
    return f"reduce:{level}:{_sha256(_stable_json(value))}"


def prepare_hierarchical_notes(
    manifest: Mapping[str, Any],
    *,
    prompt: str,
    model: str,
    reasoning_effort: str | None,
    call_map: Callable[[SourceBatch], str],
    call_reduce: Callable[[int, str, str, tuple[int, ...]], str],
    checkpoint_root: Path | None = None,
    map_input_characters: int = DEFAULT_MAP_INPUT_CHARACTERS,
    reduce_input_characters: int = DEFAULT_REDUCE_INPUT_CHARACTERS,
    final_notes_characters: int = DEFAULT_FINAL_NOTES_CHARACTERS,
    max_batches: int = MAX_MAP_BATCHES,
) -> HierarchicalNotes:
    batches = build_source_batches(
        manifest,
        input_character_budget=map_input_characters,
        max_batches=max_batches,
    )
    identity = {
        "policy": HIERARCHY_POLICY_VERSION,
        "source_sha256": manifest["source"]["file_hash_sha256"],
        "manifest_id": manifest["manifest_id"],
        "prompt_sha256": _sha256(prompt),
        "model": model,
        "reasoning_effort": reasoning_effort,
        "map_input_characters": map_input_characters,
        "reduce_input_characters": reduce_input_characters,
        "final_notes_characters": final_notes_characters,
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

    map_calls = 0
    resumed_map_batches = 0
    nodes: list[NoteNode] = []
    for batch in batches:
        note = checkpoint.map_note(batch.batch_id) if checkpoint else None
        if note is None:
            note = str(call_map(batch) or "").strip()
            if not note:
                raise ExtractionCoverageError(
                    f"map extraction returned no note for pages {_page_ranges(batch.pages)}"
                )
            if len(note) > MAX_CHECKPOINT_NOTE_CHARACTERS:
                raise ExtractionCoverageError("map extraction note exceeds its safety bound")
            map_calls += 1
            if checkpoint:
                checkpoint.save_map_note(batch.batch_id, note)
        else:
            resumed_map_batches += 1
        nodes.append(NoteNode(
            node_id=batch.batch_id,
            text=note,
            pages=batch.pages,
            source_batch_ids=(batch.batch_id,),
        ))

    reduce_calls = 0
    resumed_reduce_groups = 0
    level = 0
    while len("\n\n".join(_render_node(node) for node in nodes)) > final_notes_characters:
        level += 1
        if level > MAX_REDUCE_LEVELS:
            raise ExtractionCoverageError(
                "hierarchical notes did not converge within the explicit reduction limit"
            )
        groups = _pack_note_groups(nodes, input_character_budget=reduce_input_characters)
        reduced: list[NoteNode] = []
        for group in groups:
            group_id = _group_identity(level, group)
            pages = tuple(sorted({page for node in group for page in node.pages}))
            sources = tuple(
                dict.fromkeys(
                    source for node in group for source in node.source_batch_ids
                )
            )
            note = checkpoint.reduce_note(group_id) if checkpoint else None
            if note is None:
                rendered = "\n\n".join(_render_node(node) for node in group)
                note = str(call_reduce(level, group_id, rendered, pages) or "").strip()
                if not note:
                    raise ExtractionCoverageError(
                        f"hierarchical reduction returned no note for pages {_page_ranges(pages)}"
                    )
                if len(note) > MAX_CHECKPOINT_NOTE_CHARACTERS:
                    raise ExtractionCoverageError("reduction note exceeds its safety bound")
                reduce_calls += 1
                if checkpoint:
                    checkpoint.save_reduce_note(group_id, note)
            else:
                resumed_reduce_groups += 1
            reduced.append(NoteNode(
                node_id=group_id,
                text=note,
                pages=pages,
                source_batch_ids=sources,
            ))
        nodes = reduced

    final_text = "\n\n".join(_render_node(node) for node in nodes)
    page_records = [
        {
            "page_number": int(page["page_number"]),
            "status": page["status"],
            "text_characters": int(page.get("text_characters") or 0),
            "chunk_count": len(page.get("chunk_ids") or []),
            "parser_used": page.get("parser_used"),
            "error_type": page.get("error_type"),
        }
        for page in manifest.get("pages", [])
    ]
    original = manifest.get("coverage") or {}
    coverage = {
        "strategy": HIERARCHY_POLICY_VERSION,
        "coverage_scope": "locally_extractable_pdf_text",
        "source_sha256": manifest["source"]["file_hash_sha256"],
        "manifest_id": manifest["manifest_id"],
        "source_pages": original.get("source_pages"),
        "attempted_pages": original.get("attempted_pages"),
        "ok_pages": original.get("ok_pages"),
        "empty_pages": original.get("empty_pages"),
        "failed_pages": original.get("failed_pages"),
        "page_iteration_complete": original.get("page_iteration_complete"),
        "source_characters": original.get("extracted_characters"),
        "source_chunks": original.get("chunk_count"),
        "map_batches": len(batches),
        "map_batches_completed": len(batches),
        "resumed_map_batches": resumed_map_batches,
        "reduction_levels": level,
        "resumed_reduce_groups": resumed_reduce_groups,
        "model_calls_this_attempt": map_calls + reduce_calls,
        "final_notes_characters": len(final_text),
        "model_input_coverage": "all_extracted_text_chunks",
        "page_sources": page_records,
        "limitations": [
            "empty pages have no locally extractable text and may contain unobserved images",
            "hierarchical model notes can omit semantics even when structural source coverage is complete",
        ],
    }
    return HierarchicalNotes(final_text, coverage, checkpoint)


def provider_file_search_coverage(
    manifest: Mapping[str, Any] | None,
    *,
    audit_error_type: str | None = None,
) -> dict[str, Any]:
    """Disclose what local auditing can and cannot prove for provider retrieval."""
    value: dict[str, Any] = {
        "strategy": "provider-file-search-v1",
        "coverage_scope": "provider_managed_pdf_retrieval",
        "model_input_coverage": "provider_retrieval_unverified",
        "page_iteration_complete": None,
        "limitations": [
            "the provider indexes the uploaded PDF but does not report which pages were considered",
            "local page audit does not prove that file_search supplied every page to the model",
        ],
    }
    if manifest is not None:
        original = manifest.get("coverage") or {}
        value.update({
            "source_sha256": manifest["source"]["file_hash_sha256"],
            "manifest_id": manifest["manifest_id"],
            "source_pages": original.get("source_pages"),
            "attempted_pages": original.get("attempted_pages"),
            "ok_pages": original.get("ok_pages"),
            "empty_pages": original.get("empty_pages"),
            "failed_pages": original.get("failed_pages"),
            "local_page_iteration_complete": original.get("page_iteration_complete"),
        })
    if audit_error_type:
        value["local_audit_error_type"] = audit_error_type
    return value
