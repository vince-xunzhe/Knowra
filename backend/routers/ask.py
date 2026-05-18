"""HTTP surface for the wiki Ask agent + the auto-maintained index.

  POST /api/wiki/ask                — run the agent on a question
  POST /api/wiki/index/rebuild      — full LLM-driven index rebuild
  GET  /api/wiki/index              — return the raw index.md text
  GET  /api/wiki/index/status       — file metadata only (no read)
"""
from __future__ import annotations

import logging
import re
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import load_config, task_model_id, task_model_name, task_reasoning_effort
from database import get_db
from models import KnowledgeEdge, KnowledgeNode, Paper
from model_gateway import call_text_model
from services import ask_agent, wiki_index
from services import wiki_search as wiki_search_service
from services.graph_service import (
    MANUAL_NODE_ORIGIN,
    PROMOTED_BY_USER,
    PROMOTION_PROMOTED,
    find_existing_concept_node,
    normalize_source_paper_ids,
)
from services.synthesis_concept_service import analyze_synthesis_concept
from services.wiki_compiler import (
    WIKI_CONCEPTS_DIR,
    _concept_page_path,
    _now_iso,
    _render_frontmatter,
    reconcile_concept_pages_dir,
)

router = APIRouter(prefix="/api/wiki", tags=["wiki-ask"])
log = logging.getLogger("wiki_ask")

ASK_TITLE_SYSTEM_PROMPT = (
    "你是知识库会话标题生成器。"
    "请根据用户问题和助手回答，提炼一个简短主题短句作为会话标题。"
    "要求：\n"
    "1. 只输出标题本身，不要解释、不要引号、不要编号。\n"
    "2. 优先使用名词短语，不要重复完整问句。\n"
    "3. 控制在 4 到 18 个汉字或等价长度。\n"
    "4. 如果是方法比较、数据集盘点、概念综述，也要压成一个主题短句。"
)
_ASK_TITLE_PREFIX_RE = re.compile(r"^(标题|会话标题|title)\s*[:：]\s*", re.IGNORECASE)
_ASK_TITLE_PUNCT_RE = re.compile(r"^[\"'“”‘’`]+|[\"'“”‘’`]+$")


class AskRequest(BaseModel):
    question: str
    # Conversation context: [{role: 'user'|'assistant', content: str}, ...]
    # Stay 3.9-compatible by using typing-module forms instead of `|`.
    history: Optional[List[dict]] = None
    session_id: Optional[str] = None


class AskTraceStep(BaseModel):
    step: int
    tool: str
    args: dict
    result_summary: str
    duration_ms: int


class AskCitation(BaseModel):
    kind: str
    ref: str
    path: Optional[str] = None
    filename: Optional[str] = None
    paper_id: Optional[int] = None


class AskResponse(BaseModel):
    answer: str
    cited_files: List[str]
    citations: List[AskCitation] = []
    trace: List[AskTraceStep]
    model: str
    session_title: Optional[str] = None
    session_id: Optional[str] = None
    duration_ms: int
    steps: int


def _clean_session_title(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    text = text.splitlines()[0].strip()
    text = re.sub(r"^#+\s*", "", text)
    text = _ASK_TITLE_PREFIX_RE.sub("", text)
    text = _ASK_TITLE_PUNCT_RE.sub("", text).strip()
    text = re.sub(r"\s+", " ", text)
    text = text.rstrip("。！？!?：:;；，,、")
    return text[:24].strip()


def _suggest_ask_session_title(
    cfg: dict,
    *,
    question: str,
    answer: str,
    model: str,
    reasoning_effort: Optional[str],
) -> Optional[str]:
    try:
        raw = call_text_model(
            cfg,
            model_id=model,
            system=ASK_TITLE_SYSTEM_PROMPT,
            user=(
                f"[用户问题]\n{question.strip()}\n\n"
                f"[助手回答]\n{answer.strip()[:2800]}"
            ),
            max_tokens=48,
            temperature=0.2,
            reasoning_effort="low" if reasoning_effort in {"low", "medium", "high"} else None,
            timeout_s=90,
        )
    except Exception as exc:
        log.warning("ask session title generation failed: %s", exc)
        return None
    cleaned = _clean_session_title(raw)
    return cleaned or None


@router.post("/ask", response_model=AskResponse)
def ask(body: AskRequest, db: Session = Depends(get_db)):
    question = (body.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question 不能为空")
    cfg = load_config()
    api_key = cfg.get("openai_api_key") or ""
    model = task_model_name(cfg, "ask_agent")
    try:
        result = ask_agent.run_ask_agent(
            db,
            question=question,
            history=body.history,
            api_key=api_key,
            model=model,
            reasoning_effort=task_reasoning_effort(cfg, "ask_agent"),
        )
    except ask_agent.AskAgentUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    session_title = _suggest_ask_session_title(
        cfg,
        question=question,
        answer=result.answer,
        model=model,
        reasoning_effort=task_reasoning_effort(cfg, "ask_agent"),
    )
    session_id = (body.session_id or "").strip() or None
    return AskResponse(
        answer=result.answer,
        cited_files=result.cited_files,
        citations=[
            AskCitation(
                kind=str(item.get("kind") or "unknown"),
                ref=str(item.get("ref") or ""),
                path=item.get("path"),
                filename=item.get("filename"),
                paper_id=item.get("paper_id"),
            )
            for item in result.citations
            if isinstance(item, dict)
        ],
        trace=[
            AskTraceStep(
                step=t.step,
                tool=t.tool,
                args=t.args,
                result_summary=t.result_summary,
                duration_ms=t.duration_ms,
            )
            for t in result.trace
        ],
        model=result.model,
        session_title=session_title,
        session_id=session_id,
        duration_ms=result.duration_ms,
        steps=result.steps,
    )


@router.post("/index/rebuild")
def rebuild_index(db: Session = Depends(get_db)):
    cfg = load_config()
    api_key = cfg.get("openai_api_key") or ""
    model = task_model_id(cfg, "wiki_compile")
    try:
        path = wiki_index.rebuild_index(db, api_key=api_key, model=model)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return {
        "path": str(path),
        "size": path.stat().st_size,
    }


@router.get("/index")
def get_index():
    text = wiki_index.read_index()
    if text is None:
        raise HTTPException(status_code=404, detail="index.md 还没生成")
    return {"text": text, "summary": wiki_index.index_summary()}


@router.get("/index/status")
def index_status():
    return wiki_index.index_summary()


# --- A3: file an Ask answer back as a synthesis concept page ----------


class SynthesisConceptInput(BaseModel):
    title: str
    body: str
    source_question: Optional[str] = None
    source_questions: List[str] = []
    synthesis_scope: Optional[str] = "turn"
    source_session_id: Optional[str] = None
    source_session_title: Optional[str] = None
    source_turn_indexes: List[int] = []
    source_cited_files: List[str] = []
    force_create: bool = False
    source_paper_ids: List[int] = []
    tags: List[str] = []


def _sync_synthesis_relation_edges(
    db: Session,
    *,
    source_node_id: int,
    related_links: list,
) -> list[dict]:
    if not related_links:
        return []
    node_map = {
        node.id: node
        for node in db.query(KnowledgeNode).all()
        if getattr(node, "id", None) != source_node_id and (node.node_type or "") != "paper"
    }
    symmetric_relations = {"related", "contrasts_with", "similar"}
    existing_edges = {
        (edge.source_id, edge.target_id, edge.relation_type)
        for edge in db.query(KnowledgeEdge).all()
    }
    resolved: list[dict] = []
    created = False
    for link in related_links:
        target = node_map.get(getattr(link, "concept_id", None))
        relation = (getattr(link, "relation_type", "") or "").strip().lower()
        if target is None or not relation:
            continue
        resolved.append({
            "concept_id": target.id,
            "title": target.title,
            "relation_type": relation,
        })
        key = (source_node_id, target.id, relation)
        reverse_key = (target.id, source_node_id, relation)
        if key in existing_edges or (relation in symmetric_relations and reverse_key in existing_edges):
            continue
        db.add(KnowledgeEdge(
            source_id=source_node_id,
            target_id=target.id,
            relation_type=relation,
            weight=1.0,
        ))
        existing_edges.add(key)
        created = True
    if created:
        db.commit()
    return resolved


def _filename_to_paper_id(filename: str) -> Optional[int]:
    """Pull the leading 0042 → 42 from a wiki .md filename. Tolerant of
    junk input — returns None if not parseable."""
    import re

    m = re.match(r"(\d+)-", filename)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


@router.post("/concepts/from_synthesis")
def create_concept_from_synthesis(
    body: SynthesisConceptInput,
    db: Session = Depends(get_db),
):
    """Persist an Ask agent answer as a new synthesis concept page.

    Creates:
      - A manual `KnowledgeNode` (origin=manual, promoted, by=user) so
        the new concept shows up in the graph alongside other manual
        concepts.
      - A `data/wiki/concepts/{id}-{slug}.md` file directly (skipping
        LLM compile — we already have the body).

    The frontmatter records `concept_origin: synthesis` and the original
    question so future agent runs can recognize the file's provenance.
    """
    title = (body.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="标题不能为空")
    md_body = (body.body or "").strip()
    if not md_body:
        raise HTTPException(status_code=400, detail="内容不能为空")
    synthesis_scope = (body.synthesis_scope or "turn").strip().lower()
    if synthesis_scope not in {"turn", "session"}:
        synthesis_scope = "turn"
    source_session_id = (body.source_session_id or "").strip()[:120] or None
    source_session_title = (body.source_session_title or "").strip()[:120] or None

    source_questions: list[str] = []
    seen_questions: set[str] = set()
    for raw in body.source_questions or []:
        question = (raw or "").strip()
        if question and question not in seen_questions:
            source_questions.append(question)
            seen_questions.add(question)
    source_question = (body.source_question or "").strip()
    if source_question and source_question not in seen_questions:
        source_questions.insert(0, source_question)
        seen_questions.add(source_question)
    source_turn_indexes = sorted({
        int(raw)
        for raw in (body.source_turn_indexes or [])
        if isinstance(raw, int) and raw > 0
    })
    source_cited_files: list[str] = []
    seen_cited: set[str] = set()
    for raw in body.source_cited_files or []:
        cited = (raw or "").strip()
        if not cited or cited in seen_cited:
            continue
        seen_cited.add(cited)
        source_cited_files.append(cited)
        if len(source_cited_files) >= 64:
            break

    # Normalize tags + paper ids the same way manual_concepts does.
    tag_set: list[str] = []
    seen_tags: set[str] = set()
    for raw in body.tags or []:
        t = (raw or "").strip()
        if t and t.lower() not in seen_tags:
            tag_set.append(t)
            seen_tags.add(t.lower())

    paper_ids = []
    if body.source_paper_ids:
        paper_ids = normalize_source_paper_ids(body.source_paper_ids)
        existing = {
            p.id for p in db.query(Paper).filter(Paper.id.in_(paper_ids)).all()
        }
        paper_ids = [pid for pid in paper_ids if pid in existing]

    cfg = load_config()
    analysis = analyze_synthesis_concept(
        db,
        title=title,
        body_markdown=md_body,
        source_questions=source_questions,
        synthesis_scope=synthesis_scope,
        source_paper_ids=paper_ids,
        user_tags=tag_set,
        api_key=cfg.get("openai_api_key") or "",
        model=task_model_id(cfg, "ask_synthesis"),
    )
    summary = (analysis.summary or "").strip() or title
    body_markdown = (analysis.body_markdown or "").strip() or md_body
    for tag in analysis.tags:
        key = tag.lower()
        if key in seen_tags:
            continue
        tag_set.append(tag)
        seen_tags.add(key)

    # Title duplicates stay deterministic: exact visible title match still
    # blocks creation even if the model judged "create_new".
    dedupe_aliases = list(analysis.aliases or [])
    duplicate = find_existing_concept_node(
        db,
        title,
        aliases=dedupe_aliases,
    )
    if duplicate is None and dedupe_aliases:
        # Secondary pass: allow existing tag hits only when we already have
        # explicit alias candidates from analysis, to reduce false positives.
        duplicate = find_existing_concept_node(
            db,
            title,
            aliases=dedupe_aliases,
            include_tags=True,
        )
    if duplicate is None and analysis.duplicate_concept_id is not None:
        duplicate = next(
            (
                node
                for node in db.query(KnowledgeNode).all()
                if getattr(node, "id", None) == analysis.duplicate_concept_id
                and (node.node_type or "") != "paper"
            ),
            None,
        )
    if duplicate is not None and not body.force_create:
        duplicate_path = _concept_page_path(duplicate)
        duplicate_reason = (analysis.duplicate_reason or "").strip()
        message = (
            f"模型判断它与现有概念「{duplicate.title}」是同一个概念：{duplicate_reason}"
            if duplicate_reason and getattr(duplicate, "id", None) == analysis.duplicate_concept_id
            else f"已存在同名概念「{duplicate.title}」，请先确认是否重复。"
        )
        raise HTTPException(
            status_code=409,
            detail={
                "message": message,
                "duplicate_reason": duplicate_reason or None,
                "duplicate_concept": {
                    "concept_id": duplicate.id,
                    "title": duplicate.title,
                    "filename": duplicate_path.name,
                    "path": str(duplicate_path),
                },
                "duplicate_strategy": (
                    "model_duplicate_id"
                    if getattr(duplicate, "id", None) == analysis.duplicate_concept_id
                    else "title_or_alias_match"
                ),
                "can_force_create": True,
            },
        )

    node = KnowledgeNode(
        title=title,
        content=summary,
        node_type="concept",
        node_origin=MANUAL_NODE_ORIGIN,
        hidden=False,
        tags=tag_set,
        source_paper_ids=paper_ids,
        embedding=None,
        promotion_status=PROMOTION_PROMOTED,
        promoted_by=PROMOTED_BY_USER,
    )
    db.add(node)
    db.commit()
    db.refresh(node)
    resolved_related_concepts = _sync_synthesis_relation_edges(
        db,
        source_node_id=node.id,
        related_links=list(analysis.related_links or []),
    )

    # Write the .md file directly. We bypass `compile_concept_page` so we
    # don't burn another LLM call — the user already vetted this body in
    # the Ask drawer.
    WIKI_CONCEPTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _concept_page_path(node)
    meta = {
        "kind": "concept",
        "title": title,
        "concept_id": node.id,
        "slug": path.stem.split("-", 1)[-1],
        "node_type": node.node_type,
        "concept_origin": "synthesis",
        "synthesis_scope": synthesis_scope,
        "synthesis_question": source_question,
        "synthesis_questions": source_questions,
        "source_session_id": source_session_id,
        "source_session_title": source_session_title,
        "source_turn_indexes": source_turn_indexes,
        "source_turn_count": len(source_turn_indexes),
        "source_cited_files": source_cited_files,
        "aliases": list(analysis.aliases or []),
        "tags": tag_set,
        "source_paper_ids": paper_ids,
        "related_concept_ids": [item["concept_id"] for item in resolved_related_concepts],
        "related_concepts": [
            f'{item["relation_type"]}: {item["title"]} (#{item["concept_id"]})'
            for item in resolved_related_concepts
        ],
        "compiled_at": _now_iso(),
        "summary": summary,
        "analysis_model": analysis.model,
    }
    page = _render_frontmatter(meta) + f"\n# {title}\n\n" + body_markdown + "\n"
    path.write_text(page, encoding="utf-8")

    # Refresh search index + reconcile so the new file is discoverable
    # immediately (Ask agent could find it on the next query).
    reconcile_concept_pages_dir(db, prune_orphans=True)
    try:
        wiki_index.refresh_index()
    except Exception:
        pass
    try:
        wiki_search_service.rebuild_index()
    except Exception:
        pass

    return {
        "concept_id": node.id,
        "filename": path.name,
        "path": str(path),
        "created": True,
        "reused_existing": False,
        "forced_create": bool(duplicate is not None and body.force_create),
        "concept_title": title,
        "analysis_used": analysis.used_model,
        "analysis_model": analysis.model,
        "related_concepts_added": len(resolved_related_concepts),
    }
