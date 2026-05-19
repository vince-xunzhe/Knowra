"""P2 — render an Ask answer into a richer output format, filed back
into the wiki so it's viewable in Obsidian and re-queryable by the agent.

Karpathy's blueprint §4: "Instead of getting answers in text/terminal,
I like to have it render markdown files, or slide shows (Marp format)…
all of which I then view again in Obsidian. Often, I end up filing the
outputs back into the wiki to enhance it for further queries."

Two formats here:
  - marp   → `data/wiki/decks/{slug}.md`   (Marp-flavored slide deck)
  - report → `data/wiki/reports/{slug}.md` (clean sectioned report)

matplotlib / code-exec output is deliberately out of scope (needs a
sandbox); deferred in todo.md.

Every output carries Obsidian-resolvable `aliases` so it shows up in
the vault graph and the Ask agent can read it on a later query —
that's the "queries add up" feedback loop.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional

from config import load_config, task_model_id
from services.wiki_compiler import (
    WIKI_DIR,
    _call_llm,
    _dedup_aliases,
    _render_frontmatter,
    _slugify,
)
from services import wiki_search as wiki_search_service

OutputFormat = Literal["marp", "report"]

WIKI_DECKS_DIR = WIKI_DIR / "decks"
WIKI_REPORTS_DIR = WIKI_DIR / "reports"


MARP_SYSTEM = (
    "你是把研究问答整理成 Marp 幻灯片的助手。给定一段 markdown 答案，"
    "输出一份可被 Marp 渲染的 markdown 幻灯：\n"
    "1. 用 `---` 分隔每一页（首页是标题页）。\n"
    "2. 每页一个 `#` 或 `##` 标题 + 3-6 条要点，要点精炼，不要整段照抄。\n"
    "3. 保留原答案里的 [[wikilink]] 标记不动，方便 Obsidian 反链。\n"
    "4. 末尾单独一页 `## 📚 引用来源`，列出答案里出现过的 [[..]] 链接。\n"
    "5. 只输出幻灯正文，不要 frontmatter，不要 ```markdown 围栏。"
)

REPORT_SYSTEM = (
    "你是研究报告编辑。给定一段 markdown 问答答案，整理成一篇结构清晰的报告：\n"
    "1. 顶部一句话摘要（`> ` 引用块）。\n"
    "2. 用 `##` 分节，逻辑顺序：背景 / 关键发现 / 对比或分歧 / 结论。\n"
    "3. 保留 [[wikilink]] 标记不动。\n"
    "4. 末尾 `## 📚 引用来源` 保留所有 [[..]] 链接。\n"
    "5. 不要 frontmatter，不要 ```markdown 围栏，不要寒暄。"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def _extract_wikilinks(md: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for m in _WIKILINK_RE.finditer(md or ""):
        token = m.group(1).split("|", 1)[0].strip()
        if token and token not in seen:
            seen.add(token)
            out.append(token)
    return out


def _strip_code_fence(text: str) -> str:
    """LLMs sometimes wrap the whole thing in ```markdown … ``` despite
    being told not to. Peel one outer fence if present."""
    t = (text or "").strip()
    if t.startswith("```"):
        first_nl = t.find("\n")
        if first_nl != -1:
            t = t[first_nl + 1 :]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def render_output(
    *,
    answer_markdown: str,
    fmt: OutputFormat,
    title: str,
    source_question: Optional[str] = None,
) -> dict:
    answer = (answer_markdown or "").strip()
    if not answer:
        raise ValueError("answer_markdown 为空")
    title = (title or "").strip() or "未命名"

    cfg = load_config()
    model = task_model_id(cfg, "wiki_compile")

    if fmt == "marp":
        system, base_dir, kind = MARP_SYSTEM, WIKI_DECKS_DIR, "deck"
    elif fmt == "report":
        system, base_dir, kind = REPORT_SYSTEM, WIKI_REPORTS_DIR, "report"
    else:
        raise ValueError(f"unknown format: {fmt}")

    user = (
        f"# 标题\n{title}\n\n"
        + (f"# 原始问题\n{source_question}\n\n" if source_question else "")
        + f"# 待整理的答案\n{answer}"
    )
    body = _strip_code_fence(
        _call_llm(None, model, system, user, max_tokens=3000, task_id="wiki_compile")
    )

    base_dir.mkdir(parents=True, exist_ok=True)
    slug = _slugify(title, fallback=f"{kind}-output")
    # Timestamp suffix keeps repeated exports of similar questions from
    # clobbering each other.
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"{slug}-{stamp}.md"
    path = base_dir / filename

    aliases = _dedup_aliases([f"{kind}:{slug}", title])
    meta = {
        "kind": kind,
        "title": title,
        "aliases": aliases,
        "source_question": source_question or "",
        "source_links": _extract_wikilinks(answer),
        "compiled_at": _now_iso(),
        "compile_model": model,
    }

    if fmt == "marp":
        # Marp needs its own front-matter keys; we keep ours too — Marp
        # ignores unknown keys, Obsidian reads aliases, both happy.
        meta = {"marp": True, "theme": "default", "paginate": True, **meta}
        page = _render_frontmatter(meta) + "\n" + body.strip() + "\n"
    else:
        page = _render_frontmatter(meta) + f"\n# {title}\n\n" + body.strip() + "\n"

    path.write_text(page, encoding="utf-8")

    # Make it discoverable by the agent on the next query.
    try:
        wiki_search_service.rebuild_index()
    except Exception:
        pass

    return {
        "kind": kind,
        "filename": filename,
        "path": str(path),
        "rel_path": f"data/wiki/{base_dir.name}/{filename}",
    }
