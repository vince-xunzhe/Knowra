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
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from services.wiki_compiler import (
    WIKI_DIR,
    _call_llm,
    _hash_payload,
    _parse_frontmatter,
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


def _normalize_int_list(values: Any) -> list[int]:
    raw = values if isinstance(values, list) else [values]
    out: list[int] = []
    for item in raw:
        try:
            out.append(int(item))
        except (TypeError, ValueError):
            continue
    return out


def _truncate(text: str, max_len: int) -> str:
    compact = re.sub(r"\s+", " ", (text or "").strip())
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 1].rstrip() + "…"


def _clean_summary_line(line: str) -> str:
    text = (line or "").strip()
    if not text:
        return ""
    if text.startswith("#"):
        return ""
    text = re.sub(r"^\s*[-*+]\s+", "", text)
    text = re.sub(r"\[\[([^\]]+)\]\]", r"\1", text)
    text = re.sub(r"\[(.*?)\]\((.*?)\)", r"\1", text)
    text = text.strip(" \t-:：")
    return re.sub(r"\s+", " ", text).strip()


def _summary_from_markdown(path: str | Path, max_len: int) -> str:
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return ""
    _, body = _parse_frontmatter(text)
    in_code = False
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        cleaned = _clean_summary_line(line)
        if cleaned:
            return _truncate(cleaned, max_len=max_len)
    return ""


def _source_digest(paper_pages: list[dict], concept_pages: list[dict]) -> str:
    paper_rows = [
        {
            "paper_id": item.get("paper_id"),
            "filename": item.get("filename"),
            "title": item.get("title"),
            "compiled_at": item.get("compiled_at"),
            "source_signature": item.get("source_signature"),
        }
        for item in sorted(
            paper_pages,
            key=lambda x: (
                x.get("paper_id") if isinstance(x.get("paper_id"), int) else 10**9,
                str(x.get("filename") or ""),
            ),
        )
    ]
    concept_rows = [
        {
            "concept_id": item.get("concept_id"),
            "filename": item.get("filename"),
            "title": item.get("title"),
            "node_type": item.get("node_type"),
            "compiled_at": item.get("compiled_at"),
            "source_signature": item.get("source_signature"),
            "source_paper_ids": sorted(_normalize_int_list(item.get("source_paper_ids"))),
        }
        for item in sorted(
            concept_pages,
            key=lambda x: (
                x.get("concept_id") if isinstance(x.get("concept_id"), int) else 10**9,
                str(x.get("filename") or ""),
            ),
        )
    ]
    return _hash_payload({"papers": paper_rows, "concepts": concept_rows})


def _source_snapshot() -> dict[str, Any]:
    paper_pages = list_paper_pages()
    concept_pages = list_concept_pages()
    return {
        "papers": paper_pages,
        "concepts": concept_pages,
        "paper_count": len(paper_pages),
        "concept_count": len(concept_pages),
        "digest": _source_digest(paper_pages, concept_pages),
    }


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


_CONCEPT_GROUP_LABELS = {
    "concept": "技术概念",
    "method": "方法",
    "dataset": "数据集",
    "task": "任务",
    "metric": "指标",
    "model": "模型",
}


def _concept_group_label(meta: dict) -> str:
    if (meta.get("concept_origin") or "").strip().lower() in {"manual", "synthesis"}:
        return "手动概念"
    node_type = (meta.get("node_type") or "concept").strip().lower()
    return _CONCEPT_GROUP_LABELS.get(node_type, node_type or "概念")


def _render_incremental_body(paper_pages: list[dict], concept_pages: list[dict]) -> str:
    lines = ["# Knowra 知识库索引", ""]

    papers_sorted = sorted(
        paper_pages,
        key=lambda m: (
            m.get("paper_id") if isinstance(m.get("paper_id"), int) else 10**9,
            str(m.get("title") or m.get("filename") or ""),
        ),
    )
    lines.append(f"## 论文 · {len(papers_sorted)}")
    if not papers_sorted:
        lines.append("- （暂无论文页）")
    else:
        for meta in papers_sorted:
            paper_id = meta.get("paper_id")
            if not isinstance(paper_id, int):
                continue
            title = (meta.get("title") or meta.get("filename") or f"paper-{paper_id}").strip()
            summary = _summary_from_markdown(meta.get("disk_path") or "", max_len=42) or "待补充摘要"
            lines.append(f"- [[paper:{paper_id}]] **{title}** — {summary}")

    lines.append("")
    lines.append(f"## 概念 · {len(concept_pages)}")
    if not concept_pages:
        lines.append("- （暂无概念页）")
    else:
        buckets: dict[str, list[dict]] = defaultdict(list)
        for meta in concept_pages:
            buckets[_concept_group_label(meta)].append(meta)
        for label in sorted(buckets.keys()):
            items = sorted(
                buckets[label],
                key=lambda m: (
                    str(m.get("title") or m.get("filename") or ""),
                    m.get("concept_id") if isinstance(m.get("concept_id"), int) else 10**9,
                ),
            )
            lines.append(f"### {label} ({len(items)})")
            for meta in items:
                title = (meta.get("title") or meta.get("filename") or "untitled").strip()
                slug = (meta.get("slug") or Path(meta.get("filename") or "").stem or "concept").strip()
                source_count = len(_normalize_int_list(meta.get("source_paper_ids")))
                summary = _summary_from_markdown(meta.get("disk_path") or "", max_len=36) or "待补充简介"
                lines.append(f"- [[{slug}]] **{title}** — {summary} · 引用 {source_count} 篇")

    lines.extend(["", "## 待办与连接候选", "- 增量编译后若出现 `freshness.stale` 项，优先对该页执行单项重编译。"])
    return "\n".join(lines).strip() + "\n"


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
    source = _source_snapshot()

    meta = {
        "kind": "index",
        "updated_at": _now_iso(),
        "compile_model": model,
        "build_mode": "llm_full",
        "total_papers": len(payload["papers"]),
        "total_concepts": len(payload["concepts"]),
        "source_digest": source["digest"],
    }
    full = _render_frontmatter(meta) + "\n" + body.strip() + "\n"
    WIKI_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(full, encoding="utf-8")
    return INDEX_PATH


def refresh_index() -> Path:
    """Fast deterministic refresh from current wiki page metadata.

    This avoids an extra LLM call on incremental compiles while keeping Ask's
    index.md aligned with the latest page titles/summaries.
    """
    source = _source_snapshot()
    body = _render_incremental_body(source["papers"], source["concepts"])
    meta = {
        "kind": "index",
        "updated_at": _now_iso(),
        "build_mode": "incremental",
        "compile_model": "local/incremental-index",
        "total_papers": source["paper_count"],
        "total_concepts": source["concept_count"],
        "source_digest": source["digest"],
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
    indexed_digest: Optional[str] = None
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
    raw_digest = meta.get("source_digest")
    if isinstance(raw_digest, str) and raw_digest:
        indexed_digest = raw_digest

    source = _source_snapshot()
    current_papers = source["paper_count"]
    current_concepts = source["concept_count"]
    current_digest = source["digest"]
    stale = (
        (
            indexed_papers is not None
            and indexed_concepts is not None
            and (indexed_papers != current_papers or indexed_concepts != current_concepts)
        )
        or (indexed_digest is not None and indexed_digest != current_digest)
        or (indexed_digest is None and (current_papers > 0 or current_concepts > 0))
    )
    return {
        "exists": True,
        "path": str(INDEX_PATH),
        "size": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
        "indexed_at": indexed_at,
        "indexed_papers": indexed_papers,
        "indexed_concepts": indexed_concepts,
        "indexed_digest": indexed_digest,
        "current_papers": current_papers,
        "current_concepts": current_concepts,
        "current_digest": current_digest,
        "stale": stale,
    }
