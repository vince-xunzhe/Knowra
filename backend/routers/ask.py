"""HTTP surface for the wiki Ask agent + the auto-maintained index.

  POST /api/wiki/ask                — run the agent on a question
  POST /api/wiki/index/rebuild      — full LLM-driven index rebuild
  GET  /api/wiki/index              — return the raw index.md text
  GET  /api/wiki/index/status       — file metadata only (no read)
"""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from config import load_config
from database import get_db
from models import KnowledgeNode, Paper
from services import ask_agent, wiki_index
from services import wiki_search as wiki_search_service
from services.graph_service import (
    MANUAL_NODE_ORIGIN,
    PROMOTED_BY_USER,
    PROMOTION_PROMOTED,
    normalize_source_paper_ids,
)
from services.wiki_compiler import (
    WIKI_CONCEPTS_DIR,
    _concept_page_path,
    _now_iso,
    _render_frontmatter,
    reconcile_concept_pages_dir,
)

router = APIRouter(prefix="/api/wiki", tags=["wiki-ask"])


class AskRequest(BaseModel):
    question: str
    # Conversation context: [{role: 'user'|'assistant', content: str}, ...]
    # Stay 3.9-compatible by using typing-module forms instead of `|`.
    history: Optional[List[dict]] = None


class AskTraceStep(BaseModel):
    step: int
    tool: str
    args: dict
    result_summary: str
    duration_ms: int


class AskResponse(BaseModel):
    answer: str
    cited_files: List[str]
    trace: List[AskTraceStep]
    model: str
    duration_ms: int
    steps: int


@router.post("/ask", response_model=AskResponse)
def ask(body: AskRequest, db: Session = Depends(get_db)):
    question = (body.question or "").strip()
    if not question:
        raise HTTPException(status_code=400, detail="question 不能为空")
    cfg = load_config()
    api_key = cfg.get("openai_api_key") or ""
    model = cfg.get("wiki_compile_model") or "gpt-4o-mini"
    try:
        result = ask_agent.run_ask_agent(
            db,
            question=question,
            history=body.history,
            api_key=api_key,
            model=model,
        )
    except ask_agent.AskAgentUnavailable as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return AskResponse(
        answer=result.answer,
        cited_files=result.cited_files,
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
        duration_ms=result.duration_ms,
        steps=result.steps,
    )


@router.post("/index/rebuild")
def rebuild_index(db: Session = Depends(get_db)):
    cfg = load_config()
    api_key = cfg.get("openai_api_key") or ""
    if not api_key:
        raise HTTPException(status_code=400, detail="OpenAI API key 未配置")
    model = cfg.get("wiki_compile_model") or "gpt-4o-mini"
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
    source_paper_ids: List[int] = []
    tags: List[str] = []


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

    # Normalize tags + paper ids the same way manual_concepts does.
    tag_set: list[str] = []
    seen: set[str] = set()
    for raw in body.tags or []:
        t = (raw or "").strip()
        if t and t.lower() not in seen:
            tag_set.append(t)
            seen.add(t.lower())

    paper_ids = []
    if body.source_paper_ids:
        paper_ids = normalize_source_paper_ids(body.source_paper_ids)
        existing = {
            p.id for p in db.query(Paper).filter(Paper.id.in_(paper_ids)).all()
        }
        paper_ids = [pid for pid in paper_ids if pid in existing]

    node = KnowledgeNode(
        title=title,
        content=title,
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
        "synthesis_question": body.source_question or "",
        "tags": tag_set,
        "source_paper_ids": paper_ids,
        "compiled_at": _now_iso(),
    }
    page = _render_frontmatter(meta) + f"\n# {title}\n\n" + md_body + "\n"
    path.write_text(page, encoding="utf-8")

    # Refresh search index + reconcile so the new file is discoverable
    # immediately (Ask agent could find it on the next query).
    reconcile_concept_pages_dir(db, prune_orphans=True)
    try:
        wiki_search_service.rebuild_index()
    except Exception:
        pass

    return {
        "concept_id": node.id,
        "filename": path.name,
        "path": str(path),
    }
