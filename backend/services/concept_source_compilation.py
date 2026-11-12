"""Bounded, source-covering concept Wiki compilation.

The legacy concept compiler kept only fixed prefixes of the concept summary,
each paper snippet, and each extraction field.  This module keeps every
selected source character, splits it into bounded prompts, and combines the
partial results through bounded reduction calls.

``text_coverage=complete`` means every selected input character was presented
to a compile call.  It deliberately does not claim that the model retained
every fact or understood figures, formula layout, or other visual material.
"""
from __future__ import annotations

import hashlib
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Callable, Iterable, Sequence


CONCEPT_SOURCE_POLICY = "bounded-concept-sources-v1"
DEFAULT_SOURCE_CHUNK_CHARACTERS = 2_000
DEFAULT_PROMPT_CHARACTER_LIMIT = 14_000
MAX_MODEL_OUTPUT_CHARACTERS = 12_000
REDUCE_ITEM_CHARACTERS = 3_000
MAX_REDUCE_LEVELS = 12


class ConceptSourceCompileError(RuntimeError):
    """Raised when bounded concept compilation cannot preserve its inputs."""


@dataclass(frozen=True)
class ConceptSourceDocument:
    source_key: str
    label: str
    text: str
    paper_id: str | None = None


@dataclass(frozen=True)
class ConceptSourceChunk:
    source_key: str
    label: str
    text: str
    start: int
    end: int
    part: int
    parts: int
    paper_id: str | None = None

    @property
    def chunk_id(self) -> str:
        payload = f"{self.source_key}\0{self.start}\0{self.end}\0{self.text}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ConceptSourceBatch:
    batch_id: str
    chunks: tuple[ConceptSourceChunk, ...]
    prompt: str


@dataclass(frozen=True)
class ConceptCompilation:
    body: str
    coverage: dict[str, object]


def _split_lossless(text: str, limit: int) -> list[tuple[int, int, str]]:
    if limit <= 0:
        raise ValueError("chunk character limit must be positive")
    return [
        (start, min(start + limit, len(text)), text[start : start + limit])
        for start in range(0, len(text), limit)
    ]


def build_source_chunks(
    documents: Sequence[ConceptSourceDocument],
    *,
    chunk_characters: int = DEFAULT_SOURCE_CHUNK_CHARACTERS,
) -> list[ConceptSourceChunk]:
    chunks: list[ConceptSourceChunk] = []
    seen: set[str] = set()
    for document in documents:
        if not document.source_key or document.source_key in seen:
            raise ConceptSourceCompileError("concept source keys must be unique and non-empty")
        seen.add(document.source_key)
        text = str(document.text or "")
        if not text:
            continue
        spans = _split_lossless(text, chunk_characters)
        for index, (start, end, value) in enumerate(spans, start=1):
            chunks.append(ConceptSourceChunk(
                source_key=document.source_key,
                label=document.label,
                paper_id=document.paper_id,
                text=value,
                start=start,
                end=end,
                part=index,
                parts=len(spans),
            ))
    return chunks


def _fair_chunk_order(chunks: Sequence[ConceptSourceChunk]) -> list[ConceptSourceChunk]:
    """Round-robin papers so one long paper cannot starve other evidence."""
    queues: OrderedDict[str, deque[ConceptSourceChunk]] = OrderedDict()
    for chunk in chunks:
        group_key = f"paper:{chunk.paper_id}" if chunk.paper_id is not None else chunk.source_key
        queues.setdefault(group_key, deque()).append(chunk)
    ordered: list[ConceptSourceChunk] = []
    while queues:
        exhausted: list[str] = []
        for source_key, queue in queues.items():
            ordered.append(queue.popleft())
            if not queue:
                exhausted.append(source_key)
        for source_key in exhausted:
            del queues[source_key]
    return ordered


def _render_chunk(chunk: ConceptSourceChunk) -> str:
    paper = f" paper:{chunk.paper_id}" if chunk.paper_id is not None else ""
    return (
        f"\n[SOURCE id={chunk.chunk_id}{paper} field={chunk.label!r} "
        f"chars={chunk.start}:{chunk.end} part={chunk.part}/{chunk.parts}]\n"
        f"{chunk.text}\n[/SOURCE]"
    )


def _map_prompt(
    *,
    title: str,
    node_type: str,
    chunks: Sequence[ConceptSourceChunk],
    index: int,
    total: int,
) -> str:
    output_target = "完整概念条目" if total == 1 else "可继续归并的概念小节"
    header = (
        f"# 概念名称: {title}\n"
        f"# 概念类型: {node_type}\n"
        f"# 输入批次: {index}/{total}\n\n"
        f"请综合当前批次中的材料，写成{output_target}。"
        "保留共识、差异、限制和关键尾部事实；引用论文事实时必须带对应的 "
        "[[paper:id]]。不要逐条照抄，也不要补写输入中没有的内容。"
    )
    return header + "".join(_render_chunk(chunk) for chunk in chunks)


def build_source_batches(
    *,
    title: str,
    node_type: str,
    chunks: Sequence[ConceptSourceChunk],
    prompt_character_limit: int = DEFAULT_PROMPT_CHARACTER_LIMIT,
) -> list[ConceptSourceBatch]:
    if not chunks:
        return []
    ordered = _fair_chunk_order(chunks)
    groups: list[list[ConceptSourceChunk]] = []
    current: list[ConceptSourceChunk] = []
    # The total count changes prompt width by only a few digits.  Plan with a
    # conservative placeholder, then render and verify the exact prompts.
    for chunk in ordered:
        candidate = [*current, chunk]
        probe = _map_prompt(
            title=title,
            node_type=node_type,
            chunks=candidate,
            index=999_999,
            total=999_999,
        )
        if len(probe) <= prompt_character_limit:
            current = candidate
            continue
        if not current:
            raise ConceptSourceCompileError(
                f"source chunk {chunk.chunk_id} cannot fit the prompt character limit"
            )
        groups.append(current)
        current = [chunk]
    if current:
        groups.append(current)

    batches: list[ConceptSourceBatch] = []
    for index, group in enumerate(groups, start=1):
        prompt = _map_prompt(
            title=title,
            node_type=node_type,
            chunks=group,
            index=index,
            total=len(groups),
        )
        if len(prompt) > prompt_character_limit:
            raise ConceptSourceCompileError("planned concept prompt exceeds its hard limit")
        descriptor = "\0".join(chunk.chunk_id for chunk in group)
        batch_id = hashlib.sha256(descriptor.encode("utf-8")).hexdigest()[:20]
        batches.append(ConceptSourceBatch(batch_id, tuple(group), prompt))
    return batches


def _summary_parts(summaries: Iterable[str]) -> list[str]:
    parts: list[str] = []
    for summary in summaries:
        value = str(summary or "").strip()
        if not value:
            raise ConceptSourceCompileError("concept compile returned an empty result")
        if len(value) > MAX_MODEL_OUTPUT_CHARACTERS:
            raise ConceptSourceCompileError("concept compile output exceeds its safety bound")
        parts.extend(piece for _, _, piece in _split_lossless(value, REDUCE_ITEM_CHARACTERS))
    return parts


def _reduce_prompt(*, title: str, items: Sequence[str], final: bool) -> str:
    mode = "最终完整条目" if final else "可继续归并的中间条目"
    header = (
        f"# 概念名称: {title}\n"
        f"# 输出目标: {mode}\n\n"
        "合并下列已经基于来源生成的材料。去重但不要丢掉分歧、限制、关键数值或"
        " [[paper:id]] 引用；不得补写材料中没有的事实。正文从 ## 二级标题开始。\n"
    )
    return header + "".join(
        f"\n[PART {index}]\n{item}\n[/PART]" for index, item in enumerate(items, start=1)
    )


def _reduce_groups(
    *,
    title: str,
    items: Sequence[str],
    prompt_character_limit: int,
) -> list[list[str]]:
    groups: list[list[str]] = []
    current: list[str] = []
    for item in items:
        candidate = [*current, item]
        if len(_reduce_prompt(title=title, items=candidate, final=False)) <= prompt_character_limit:
            current = candidate
            continue
        if not current:
            raise ConceptSourceCompileError("summary part cannot fit the prompt character limit")
        groups.append(current)
        current = [item]
    if current:
        groups.append(current)
    return groups


def compile_bounded_concept(
    *,
    title: str,
    node_type: str,
    documents: Sequence[ConceptSourceDocument],
    call_map: Callable[[ConceptSourceBatch, int, int], str],
    call_reduce: Callable[[str, int, int, bool], str],
    chunk_characters: int = DEFAULT_SOURCE_CHUNK_CHARACTERS,
    prompt_character_limit: int = DEFAULT_PROMPT_CHARACTER_LIMIT,
) -> ConceptCompilation:
    chunks = build_source_chunks(documents, chunk_characters=chunk_characters)
    batches = build_source_batches(
        title=title,
        node_type=node_type,
        chunks=chunks,
        prompt_character_limit=prompt_character_limit,
    )
    if not batches:
        raise ConceptSourceCompileError("concept has no source text to compile")

    partials = [
        str(call_map(batch, index, len(batches)) or "").strip()
        for index, batch in enumerate(batches, start=1)
    ]
    _summary_parts(partials)  # validate map outputs before any reduction
    reduce_calls = 0
    if len(partials) == 1:
        body = partials[0]
    else:
        level = 0
        while len(partials) > 1:
            level += 1
            if level > MAX_REDUCE_LEVELS:
                raise ConceptSourceCompileError("concept reduction did not converge")
            items = _summary_parts(partials)
            groups = _reduce_groups(
                title=title,
                items=items,
                prompt_character_limit=prompt_character_limit,
            )
            next_partials: list[str] = []
            for index, group in enumerate(groups, start=1):
                final = len(groups) == 1
                prompt = _reduce_prompt(title=title, items=group, final=final)
                if len(prompt) > prompt_character_limit:
                    raise ConceptSourceCompileError("concept reduce prompt exceeds its hard limit")
                result = str(call_reduce(prompt, index, len(groups), final) or "").strip()
                _summary_parts([result])
                next_partials.append(result)
                reduce_calls += 1
            partials = next_partials
        body = partials[0]

    paper_ids = sorted({document.paper_id for document in documents if document.paper_id is not None})
    return ConceptCompilation(
        body=body,
        coverage={
            "policy": CONCEPT_SOURCE_POLICY,
            "source_documents": len([document for document in documents if document.text]),
            "source_characters": sum(len(document.text or "") for document in documents),
            "source_chunks": len(chunks),
            "source_batches": len(batches),
            "source_papers": len(paper_ids),
            "source_input_scope": "concept_description_and_selected_extraction_fields",
            "map_calls": len(batches),
            "reduce_calls": reduce_calls,
            "text_coverage": "complete",
            "semantic_completeness": "not_guaranteed",
            "visual_coverage": "unsupported",
            "prompt_character_limit": prompt_character_limit,
        },
    )
