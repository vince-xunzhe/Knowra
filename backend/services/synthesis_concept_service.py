from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from models import KnowledgeNode, Paper
from services.graph_service import (
    normalize_source_paper_ids,
    node_is_hidden,
    node_origin,
    promotion_status,
)
from services.wiki_compiler import _call_llm


ALLOWED_SYNTHESIS_RELATIONS = {"related", "builds_on", "belongs_to", "contrasts_with"}


@dataclass
class SynthesisRelatedLink:
    concept_id: int
    relation_type: str


@dataclass
class SynthesisConceptAnalysis:
    used_model: bool
    model: Optional[str]
    summary: str
    body_markdown: str
    tags: list[str]
    aliases: list[str]
    related_links: list[SynthesisRelatedLink]
    duplicate_concept_id: Optional[int]
    duplicate_reason: str


SYNTHESIS_CONCEPT_SYSTEM = (
    "你是个人知识图谱里的概念编辑器。给定一个来自 Ask 的候选概念，请完成两件事：\n"
    "1. 把它整理成更稳定的概念条目：提炼一句话摘要、补充结构化 markdown 正文、抽取少量标签。\n"
    "2. 判断它是否与已有概念完全同一件事。\n\n"
    "判重规则：\n"
    "- 只有在两个名称指向同一个概念时才判重复。\n"
    "- 相关、上下位、例子、实现、应用到某概念、经常一起出现，都不算重复。\n"
    "- 不要把已有节点的 tags 当成概念同一性的充分证据。\n"
    "- 不确定时，优先返回 create_new。\n\n"
    "正文要求：\n"
    "- 只基于输入材料，不要编造。\n"
    "- 输出 markdown，正文从 ## 二级标题开始，不要输出一级标题。\n"
    "- 如果引用输入里的论文来源，用 [[paper:id]] 标记；如果提到已有概念，用 [[概念名]] 标记。\n"
    "- 风格要像概念条目，不要像聊天回答。\n\n"
    "严格输出一个 JSON 对象，不要代码块，不要额外解释：\n"
    "{\n"
    '  "decision": "create_new" | "duplicate_existing",\n'
    '  "duplicate_concept_id": <int|null>,\n'
    '  "duplicate_reason": <string>,\n'
    '  "summary": <string>,\n'
    '  "tags": [<string>, ...],\n'
    '  "aliases": [<string>, ...],\n'
    '  "related_links": [{"concept_id": <int>, "relation_type": "related" | "builds_on" | "belongs_to" | "contrasts_with"}],\n'
    '  "body_markdown": <string>\n'
    "}"
)


_TOKEN_SPLIT_RE = re.compile(r"[^0-9A-Za-z_\u4e00-\u9fff]+")


def _normalize_text(text: str) -> str:
    return " ".join((text or "").strip().lower().split())


def _strip_markdown(text: str) -> str:
    value = (text or "").strip()
    value = re.sub(r"```.*?```", " ", value, flags=re.S)
    value = re.sub(r"`([^`]*)`", r"\1", value)
    value = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", value)
    value = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", value)
    value = re.sub(r"\[\[([^\]]+)\]\]", r"\1", value)
    value = re.sub(r"^\s{0,3}#{1,6}\s*", "", value, flags=re.M)
    value = re.sub(r"^\s{0,3}>\s?", "", value, flags=re.M)
    value = re.sub(r"[*_~]+", "", value)
    value = re.sub(r"\n{2,}", "\n", value)
    return value.strip()


def _fallback_summary(title: str, body_markdown: str) -> str:
    text = _strip_markdown(body_markdown)
    for block in re.split(r"\n\s*\n", text):
        line = block.strip()
        if line:
            return line[:240]
    return title[:240]


def _dedupe_preserve_order(values: list[str], *, limit: int = 8) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values:
        text = (raw or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _trim_top_heading(markdown: str) -> str:
    return re.sub(r"^\s*#\s+[^\n]+\n+", "", (markdown or "").strip(), count=1).strip()


def _source_paper_context(db: Session, paper_ids: list[int]) -> list[dict]:
    if not paper_ids:
        return []
    papers = db.query(Paper).filter(Paper.id.in_(paper_ids)).all()
    paper_by_id = {paper.id: paper for paper in papers}
    out: list[dict] = []
    for pid in paper_ids:
        paper = paper_by_id.get(pid)
        if paper is None:
            continue
        title = getattr(paper, "title", None) or getattr(paper, "filename", None) or f"paper:{pid}"
        out.append({
            "id": pid,
            "title": title,
        })
    return out


def _candidate_rank(node: KnowledgeNode) -> tuple[int, int, int, int]:
    return (
        0 if node_origin(node) == "manual" else 1,
        0 if promotion_status(node) == "promoted" else 1,
        0 if (node.node_type or "") == "concept" else 1,
        int(getattr(node, "id", 10**9) or 10**9),
    )


def _candidate_score(
    node: KnowledgeNode,
    *,
    title: str,
    body_markdown: str,
    source_questions: list[str],
    source_paper_ids: list[int],
) -> int:
    title_norm = _normalize_text(title)
    title_chunks = [chunk for chunk in _TOKEN_SPLIT_RE.split(title_norm) if len(chunk) >= 2]
    body_norm = _normalize_text(_strip_markdown(body_markdown)[:1800])
    questions_norm = _normalize_text(" ".join(source_questions))
    node_title_norm = _normalize_text(node.title or "")
    score = 0
    if title_norm and title_norm == node_title_norm:
        score += 120
    if title_norm and node_title_norm and (title_norm in node_title_norm or node_title_norm in title_norm):
        score += 50
    if node_title_norm and node_title_norm in body_norm:
        score += 25
    if node_title_norm and node_title_norm in questions_norm:
        score += 18
    for chunk in title_chunks:
        if chunk in node_title_norm:
            score += 10
    for tag in list(node.tags or [])[:8]:
        tag_norm = _normalize_text(tag)
        if not tag_norm:
            continue
        if tag_norm == title_norm:
            score += 12
        elif tag_norm and tag_norm in title_norm:
            score += 8
    node_papers = set(normalize_source_paper_ids(node.source_paper_ids))
    if node_papers and source_paper_ids:
        overlap = len(node_papers & set(source_paper_ids))
        score += overlap * 7
    return score


def _candidate_nodes_for_model(
    db: Session,
    *,
    title: str,
    body_markdown: str,
    source_questions: list[str],
    source_paper_ids: list[int],
) -> list[dict]:
    nodes = [
        node for node in db.query(KnowledgeNode).all()
        if (node.node_type or "") != "paper" and not node_is_hidden(node)
    ]
    nodes.sort(key=_candidate_rank)
    if len(nodes) > 120:
        ranked = sorted(
            nodes,
            key=lambda node: (
                -_candidate_score(
                    node,
                    title=title,
                    body_markdown=body_markdown,
                    source_questions=source_questions,
                    source_paper_ids=source_paper_ids,
                ),
                *_candidate_rank(node),
            ),
        )
        nodes = ranked[:40]
    payload: list[dict] = []
    for node in nodes:
        payload.append({
            "id": int(getattr(node, "id", 0) or 0),
            "title": node.title or "",
            "node_type": node.node_type or "",
            "origin": node_origin(node),
            "promotion_status": promotion_status(node),
            "tags": _dedupe_preserve_order(list(node.tags or []), limit=6),
            "paper_count": len(normalize_source_paper_ids(node.source_paper_ids)),
            "summary": _strip_markdown(node.content or "")[:120],
        })
    return payload


def _analysis_prompt(
    *,
    title: str,
    body_markdown: str,
    source_questions: list[str],
    synthesis_scope: str,
    source_papers: list[dict],
    user_tags: list[str],
    candidate_nodes: list[dict],
) -> str:
    payload = {
        "candidate_concept": {
            "title": title,
            "synthesis_scope": synthesis_scope,
            "source_questions": source_questions,
            "user_tags": user_tags,
            "source_papers": source_papers,
            "body_markdown": body_markdown[:9000],
        },
        "existing_nodes": candidate_nodes,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _parse_related_links(raw: object, *, allowed_ids: set[int]) -> list[SynthesisRelatedLink]:
    if not isinstance(raw, list):
        return []
    out: list[SynthesisRelatedLink] = []
    seen: set[tuple[int, str]] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        try:
            concept_id = int(item.get("concept_id"))
        except (TypeError, ValueError):
            continue
        relation_type = (item.get("relation_type") or "").strip().lower()
        if concept_id not in allowed_ids or relation_type not in ALLOWED_SYNTHESIS_RELATIONS:
            continue
        key = (concept_id, relation_type)
        if key in seen:
            continue
        seen.add(key)
        out.append(SynthesisRelatedLink(concept_id=concept_id, relation_type=relation_type))
        if len(out) >= 6:
            break
    return out


def _parse_analysis(raw: str) -> Optional[dict]:
    text = (raw or "").strip()
    for fence in ("```json", "```"):
        if text.startswith(fence):
            text = text[len(fence):].strip()
        if text.endswith("```"):
            text = text[:-3].strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _fallback_body(
    *,
    raw_body: str,
    summary: str,
    source_questions: list[str],
    source_papers: list[dict],
    synthesis_scope: str,
) -> str:
    body = _trim_top_heading(raw_body)
    sections: list[str] = []
    if summary:
        sections.append("## 一句话定义\n\n" + summary.strip())
    plain_body = _strip_markdown(body)
    plain_summary = _strip_markdown(summary)
    if plain_body and plain_body != plain_summary:
        heading = "## 最终综合结论" if synthesis_scope == "session" else "## 展开说明"
        sections.append(f"{heading}\n\n{body.strip()}")
    if source_questions:
        lines = [f"{idx + 1}. {question}" for idx, question in enumerate(source_questions) if question.strip()]
        if lines:
            heading = "## 问题演进" if synthesis_scope == "session" else "## 来源问题"
            sections.append(heading + "\n\n" + "\n".join(lines))
    if source_papers:
        lines = [f'- [[paper:{item["id"]}]] {item["title"]}' for item in source_papers]
        sections.append("## 来源论文\n\n" + "\n".join(lines))
    return "\n\n".join(section.strip() for section in sections if section.strip()).strip()


def _append_context_sections(
    body_markdown: str,
    *,
    source_questions: list[str],
    source_papers: list[dict],
    synthesis_scope: str,
) -> str:
    text = _trim_top_heading(body_markdown)
    sections: list[str] = [text] if text else []
    lowered = text.lower()
    if source_questions:
        heading = "## 问题演进" if synthesis_scope == "session" else "## 来源问题"
        if heading.lower() not in lowered:
            lines = [f"{idx + 1}. {question}" for idx, question in enumerate(source_questions) if question.strip()]
            if lines:
                sections.append(heading + "\n\n" + "\n".join(lines))
    if source_papers and "## 来源论文".lower() not in lowered:
        lines = [f'- [[paper:{item["id"]}]] {item["title"]}' for item in source_papers]
        sections.append("## 来源论文\n\n" + "\n".join(lines))
    return "\n\n".join(section.strip() for section in sections if section.strip()).strip()


def analyze_synthesis_concept(
    db: Session,
    *,
    title: str,
    body_markdown: str,
    source_questions: list[str],
    synthesis_scope: str,
    source_paper_ids: list[int],
    user_tags: list[str],
    api_key: str,
    model: str,
) -> SynthesisConceptAnalysis:
    source_papers = _source_paper_context(db, source_paper_ids)
    candidate_nodes = _candidate_nodes_for_model(
        db,
        title=title,
        body_markdown=body_markdown,
        source_questions=source_questions,
        source_paper_ids=source_paper_ids,
    )
    fallback_summary = _fallback_summary(title, body_markdown)
    fallback_body = _fallback_body(
        raw_body=body_markdown,
        summary=fallback_summary,
        source_questions=source_questions,
        source_papers=source_papers,
        synthesis_scope=synthesis_scope,
    )
    if not model:
        return SynthesisConceptAnalysis(
            used_model=False,
            model=None,
            summary=fallback_summary,
            body_markdown=fallback_body,
            tags=[],
            aliases=[],
            related_links=[],
            duplicate_concept_id=None,
            duplicate_reason="",
        )

    try:
        raw = _call_llm(
            None,
            model,
            SYNTHESIS_CONCEPT_SYSTEM,
            _analysis_prompt(
                title=title,
                body_markdown=body_markdown,
                source_questions=source_questions,
                synthesis_scope=synthesis_scope,
                source_papers=source_papers,
                user_tags=user_tags,
                candidate_nodes=candidate_nodes,
            ),
            max_tokens=2200,
            task_id="ask_synthesis",
        )
        parsed = _parse_analysis(raw)
    except Exception:
        parsed = None

    if not parsed:
        return SynthesisConceptAnalysis(
            used_model=False,
            model=None,
            summary=fallback_summary,
            body_markdown=fallback_body,
            tags=[],
            aliases=[],
            related_links=[],
            duplicate_concept_id=None,
            duplicate_reason="",
        )

    decision = (parsed.get("decision") or "").strip().lower()
    duplicate_concept_id = None
    if decision == "duplicate_existing":
        try:
            duplicate_concept_id = int(parsed.get("duplicate_concept_id"))
        except (TypeError, ValueError):
            duplicate_concept_id = None
    duplicate_reason = (parsed.get("duplicate_reason") or "").strip()[:500]
    summary = (parsed.get("summary") or "").strip()[:400] or fallback_summary
    body = _append_context_sections(
        (parsed.get("body_markdown") or "").strip() or fallback_body,
        source_questions=source_questions,
        source_papers=source_papers,
        synthesis_scope=synthesis_scope,
    )
    allowed_ids = {
        int(item.get("id"))
        for item in candidate_nodes
        if isinstance(item, dict) and str(item.get("id") or "").isdigit()
    }
    return SynthesisConceptAnalysis(
        used_model=True,
        model=model,
        summary=summary,
        body_markdown=body,
        tags=_dedupe_preserve_order(list(parsed.get("tags") or []), limit=8),
        aliases=_dedupe_preserve_order(list(parsed.get("aliases") or []), limit=6),
        related_links=_parse_related_links(parsed.get("related_links"), allowed_ids=allowed_ids),
        duplicate_concept_id=duplicate_concept_id,
        duplicate_reason=duplicate_reason,
    )
