"""Auto-maintained top-level wiki index.

Karpathy's blueprint observes that at the ~100-article scale, an LLM agent
doesn't need fancy RAG — it just needs a well-maintained `index.md` to
orient itself, then it can decide what to read in detail. This module
produces and updates that file.

Layout written:

    data/wiki/index.md
    ---
    kind: index
    updated_at: ...
    total_papers: N
    total_concepts: M
    ---

    # Knowra 知识库索引
    ## 论文 · N
      - [[paper:1]] **Title** — one-sentence summary. 引用概念: [[A]] [[B]]
      ...
    ## 概念 · M
      ### 技术 (k)
        - [[concept:slug]] **Title** — gist. 引用 N 篇
      ### 数据集 (k)
        ...

The full version is rendered by an LLM in one shot (cheap once); future
incremental edits patch sections without re-running the LLM.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from models import KnowledgeNode, Paper
from services.wiki_compiler import (
    WIKI_DIR,
    _call_llm,
    _read_frontmatter_from_path,
    list_concept_pages,
    list_paper_pages,
    _render_frontmatter,
)

INDEX_PATH = WIKI_DIR / "index.md"

INDEX_SYSTEM_PROMPT = (
    "你是个人 LLM 知识库的索引编辑。给定一份所有论文页和概念页的简介列表，"
    "你的任务是输出一份顶层 markdown 索引文件 index.md 的正文（不含 frontmatter）。\n"
    "要求：\n"
    "1. 用中文写。整体结构清晰、扫读友好。\n"
    "2. 顶部一级标题 `# Knowra 知识库索引`。\n"
    "3. 二级标题至少包含 `## 论文`（按 paper_id 升序列出，每条一行）和 `## 概念`（按类目 sub-section）。\n"
    "4. 论文每条格式：`- [[paper:{id}]] **{title}** — 一句话定位（≤30 字）。`\n"
    "5. 概念按 node_type 分组，二级标题下用三级标题划分类目（如 ### 技术、### 数据集、### 手动概念）。\n"
    "6. 概念每条格式：`- [[{slug}]] **{title}** — 简介（≤25 字）· 引用 {N} 篇`。\n"
    "7. 末尾追加 `## 待办与连接候选` 一节，从输入材料里挑出明显的短桩 / 缺连接（不要编造）。\n"
    "8. 不要 Sources、不要免责说明、不要 markdown 代码围栏。"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _summarize_concept_for_prompt(meta: dict) -> dict:
    """Compact form suitable for stuffing into the index-build prompt."""
    return {
        "slug": meta.get("slug") or "",
        "title": meta.get("title") or "",
        "node_type": meta.get("node_type") or "concept",
        "concept_origin": meta.get("concept_origin") or "",
        "tags": (meta.get("tags") or [])[:5],
        "source_paper_ids": meta.get("source_paper_ids") or [],
        # Short body excerpt — gives the LLM context to write a one-liner.
        "excerpt": (meta.get("body") or "")[:600],
    }


def _summarize_paper_for_prompt(meta: dict) -> dict:
    return {
        "paper_id": meta.get("paper_id"),
        "title": meta.get("title") or "",
        "authors": (meta.get("authors") or [])[:3],
        "paper_category": meta.get("paper_category") or "",
        "excerpt": (meta.get("body") or "")[:600],
    }


def _read_full_md(path: str | Path) -> str:
    p = Path(path)
    try:
        return p.read_text(encoding="utf-8")
    except OSError:
        return ""


def _gather_inputs(db: Session) -> dict[str, Any]:
    """Collect summaries of every published paper page + concept page so
    the LLM has enough context to write the index in one shot."""
    paper_pages = list_paper_pages()
    concept_pages = list_concept_pages()

    # `body` isn't returned by the listing helpers (they only give meta);
    # re-read each .md to grab the first ~600 chars for the LLM. Cheap at
    # 100 files; if we ever scale to 10k we'd cache or skip.
    papers_payload = []
    for meta in sorted(paper_pages, key=lambda m: m.get("paper_id") or 10**9):
        body = _read_full_md(meta.get("disk_path") or "").split("---", 2)[-1].strip()
        meta_with_body = {**meta, "body": body[:1000]}
        papers_payload.append(_summarize_paper_for_prompt(meta_with_body))

    concepts_payload = []
    for meta in concept_pages:
        body = _read_full_md(meta.get("disk_path") or "").split("---", 2)[-1].strip()
        meta_with_body = {**meta, "body": body[:1000]}
        concepts_payload.append(_summarize_concept_for_prompt(meta_with_body))

    return {
        "papers": papers_payload,
        "concepts": concepts_payload,
    }


def _user_prompt(payload: dict[str, Any]) -> str:
    return (
        "以下是知识库当前的全部内容。请据此生成 index.md 正文（不含 frontmatter）：\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def rebuild_index(
    db: Session,
    api_key: str,
    model: str,
) -> Path:
    """Full rebuild via one LLM call. Run rarely (first setup, or when
    user explicitly asks). Incremental updates use `patch_*` helpers."""
    payload = _gather_inputs(db)

    if not payload["papers"] and not payload["concepts"]:
        # Nothing to index — write a stub so downstream code can still
        # rely on the file existing.
        body = "# Knowra 知识库索引\n\n_库里还没有论文或概念页，请先在「论文」页处理 PDF。_\n"
    else:
        body = _call_llm(
            None,
            model,
            INDEX_SYSTEM_PROMPT,
            _user_prompt(payload),
            max_tokens=4000,
            task_id="wiki_compile",
        )

    meta = {
        "kind": "index",
        "updated_at": _now_iso(),
        "compile_model": model,
        "total_papers": len(payload["papers"]),
        "total_concepts": len(payload["concepts"]),
    }
    full = _render_frontmatter(meta) + "\n" + body.strip() + "\n"
    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(full, encoding="utf-8")
    return INDEX_PATH


def read_index() -> Optional[str]:
    """Return the raw index.md text, or None if it hasn't been built."""
    if not INDEX_PATH.is_file():
        return None
    try:
        return INDEX_PATH.read_text(encoding="utf-8")
    except OSError:
        return None


def index_summary() -> dict:
    """Lightweight metadata for the API status endpoint — does not trigger
    a build. Includes a `stale` flag derived by comparing the page counts
    recorded in index.md frontmatter against the current wiki state, so
    the UI can prompt the user to rebuild after compiles add new pages."""
    if not INDEX_PATH.is_file():
        return {"exists": False, "path": str(INDEX_PATH), "size": 0, "stale": False}
    stat = INDEX_PATH.stat()
    indexed_papers: Optional[int] = None
    indexed_concepts: Optional[int] = None
    indexed_at: Optional[str] = None
    meta = _read_frontmatter_from_path(INDEX_PATH) or {}
    raw_p = meta.get("total_papers")
    raw_c = meta.get("total_concepts")
    if isinstance(raw_p, int):
        indexed_papers = raw_p
    if isinstance(raw_c, int):
        indexed_concepts = raw_c
    raw_updated = meta.get("updated_at")
    if isinstance(raw_updated, str):
        indexed_at = raw_updated

    current_papers = len(list_paper_pages())
    current_concepts = len(list_concept_pages())
    stale = (
        indexed_papers is not None
        and indexed_concepts is not None
        and (indexed_papers != current_papers or indexed_concepts != current_concepts)
    )
    return {
        "exists": True,
        "path": str(INDEX_PATH),
        "size": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "indexed_at": indexed_at,
        "indexed_papers": indexed_papers,
        "indexed_concepts": indexed_concepts,
        "current_papers": current_papers,
        "current_concepts": current_concepts,
        "stale": stale,
    }
