"""Wiki Q&A agent.

The user asks a free-form question; we hand the LLM a tiny tool kit
(list_wiki_index / search_wiki / read_wiki) and let it iterate until it
has enough context to write a markdown answer. Karpathy's blueprint
notes that at ~100 articles, this works well without RAG infrastructure
— the LLM reads index.md first, picks the right files, drills in.

Implementation choices:
  - chat.completions tool calling (broadly supported, simple JSON I/O).
    Responses-only models (gpt-5.x) currently aren't supported here;
    raise a clear error so the user can switch model.
  - Tool dispatch is plain Python. Each call re-fetches the file from
    disk so multi-step questions always see the freshest wiki state.
  - Bounded loop (MAX_STEPS) — no infinite tool spirals on bad queries.

Output shape mirrors `routers.ask.AskResponse` — answer markdown plus a
trace the UI can fold open for debugging.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, Optional

from openai import OpenAI
from sqlalchemy.orm import Session

from services import wiki_index, wiki_search
from services.wiki_compiler import (
    WIKI_CONCEPTS_DIR,
    WIKI_DIR,
    WIKI_PAPERS_DIR,
)
from services.vlm_service import model_uses_responses_api

log = logging.getLogger("ask_agent")

MAX_STEPS = 8
SEARCH_HIT_LIMIT = 12
READ_MAX_CHARS = 12000  # truncate huge .md files to keep context affordable
SUMMARY_MAX_CHARS = 240   # max chars stored in trace[].result_summary

ASK_SYSTEM_PROMPT = (
    "你是 Knowra 个人研究知识库的研究助手。用户问你一个问题，你需要在知识库里找到答案，"
    "综合多篇论文 / 概念页内容回答。\n"
    "\n"
    "知识库布局：\n"
    "- index.md — 顶层索引，先读这个找方向\n"
    "- wiki/papers/{id}-{slug}.md — 单篇论文摘要\n"
    "- wiki/concepts/{id}-{slug}.md — 跨论文概念综述\n"
    "\n"
    "可用工具（必须通过 tool call 调用，不要在文本里假装调用）：\n"
    "- list_wiki_index() — 返回 index.md 全文，先调它\n"
    "- search_wiki(q: string) — FTS5 全文搜索，返回前 N 个 hit 的标题 + snippet\n"
    "- read_wiki(filename: string) — 读单个 .md 完整内容\n"
    "\n"
    "策略：\n"
    "1. 先调 list_wiki_index 看清楚库里有什么\n"
    "2. 根据问题主题决定 search_wiki 或直接 read_wiki 拿全文\n"
    "3. 读 2-5 个最相关的 .md 文件\n"
    "4. 综合写答案。引用论文用 [[paper:{id}]]；引用概念用 [[concept-slug]] 或概念标题\n"
    "5. 答案末尾列 `## 📚 引用来源`，列出真正读过的 .md 文件名\n"
    "\n"
    "重要：基于材料综合，不要编造。如果库里材料不足，明说\"知识库里没有相关材料\"。\n"
    "输出 markdown，从 ## 二级标题开始，可用列表 / 引用块 / 公式块。不要 markdown 代码围栏包裹整个答案。"
)


# --- tool schema ---------------------------------------------------------

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "list_wiki_index",
            "description": "返回 wiki/index.md 全文。先调用此工具看清楚库里有哪些论文与概念。",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_wiki",
            "description": "在 wiki/papers + wiki/concepts 里做 FTS5 全文搜索。返回最多 12 个 hit。",
            "parameters": {
                "type": "object",
                "properties": {
                    "q": {"type": "string", "description": "搜索关键词，至少 2 个字符"},
                },
                "required": ["q"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_wiki",
            "description": "读取 wiki/{papers|concepts}/{filename}.md 的完整内容。filename 必须是从 search_wiki 或 list_wiki_index 看到过的文件名。",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "形如 0009-grounding-image-matching-in-3d-with-mast3r.md",
                    },
                    "kind": {
                        "type": "string",
                        "enum": ["papers", "concepts"],
                        "description": "文件所属目录；search_wiki 的 hit 里有 kind 字段可直接用",
                    },
                },
                "required": ["filename", "kind"],
            },
        },
    },
]


# --- error / response shapes --------------------------------------------


class AskAgentUnavailable(Exception):
    """Raised when the agent can't run (missing API key, unsupported model)."""


@dataclass
class TraceStep:
    step: int
    tool: str
    args: dict[str, Any]
    result_summary: str
    duration_ms: int


@dataclass
class AskResult:
    answer: str
    cited_files: list[str] = field(default_factory=list)
    trace: list[TraceStep] = field(default_factory=list)
    model: str = ""
    duration_ms: int = 0
    steps: int = 0


# --- tool dispatchers ---------------------------------------------------


def _safe_read(path: Path, *, max_chars: int = READ_MAX_CHARS) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"[error: {exc}]"
    if len(text) > max_chars:
        return text[:max_chars] + f"\n\n…（文件已截断，原始长度 {len(text)} 字符）"
    return text


def _tool_list_wiki_index() -> str:
    text = wiki_index.read_index()
    if text is None:
        return (
            "[index.md 不存在 — 请先调用 POST /api/wiki/index/rebuild 或在前端点重建索引]"
        )
    return text


def _tool_search_wiki(q: str) -> str:
    if not q or len(q.strip()) < 2:
        return "[搜索词太短，至少 2 字符]"
    hits = wiki_search.search(q.strip(), limit=SEARCH_HIT_LIMIT)
    if not hits:
        return f"[没有匹配的 wiki 文件，关键词：{q!r}]"
    # Compact JSON so the LLM can both eyeball and machine-parse it.
    compact = [
        {
            "kind": h.get("kind"),
            "filename": h.get("filename"),
            "title": h.get("title"),
            "snippet": (h.get("snippet") or "").replace("\n", " ")[:240],
        }
        for h in hits
    ]
    return json.dumps(compact, ensure_ascii=False, indent=2)


def _tool_read_wiki(filename: str, kind: str) -> str:
    if kind not in {"papers", "concepts"}:
        return f"[unknown kind: {kind}; expected 'papers' or 'concepts']"
    base = WIKI_PAPERS_DIR if kind == "papers" else WIKI_CONCEPTS_DIR
    # Strip any leading slashes / parent traversals — only allow flat names.
    safe = Path(filename).name
    if safe != filename:
        return f"[invalid filename: {filename}; nested paths not allowed]"
    path = base / safe
    if not path.is_file():
        return f"[file not found: {kind}/{safe}]"
    return _safe_read(path)


def _dispatch_tool(name: str, args: dict[str, Any]) -> str:
    if name == "list_wiki_index":
        return _tool_list_wiki_index()
    if name == "search_wiki":
        return _tool_search_wiki(args.get("q", ""))
    if name == "read_wiki":
        return _tool_read_wiki(args.get("filename", ""), args.get("kind", ""))
    return f"[unknown tool: {name}]"


def _summarize_result(text: str) -> str:
    one_line = text.replace("\n", " ").strip()
    if len(one_line) <= SUMMARY_MAX_CHARS:
        return one_line
    return one_line[: SUMMARY_MAX_CHARS - 1] + "…"


# --- citation extraction ------------------------------------------------


_PAPER_REF_RE = __import__("re").compile(r"\[\[paper:(\d+)\]\]")


def _extract_cited_files(answer: str, trace: list[TraceStep]) -> list[str]:
    """Best-effort: collect every wiki file the agent actually read, plus
    any paper id mentioned in the final answer (mapped to that paper's
    `.md`). De-duped, sorted."""
    files: set[str] = set()
    for step in trace:
        if step.tool == "read_wiki":
            kind = step.args.get("kind")
            fn = step.args.get("filename")
            if kind and fn:
                files.add(f"data/wiki/{kind}/{fn}")
    # Paper ids mentioned in the answer body — useful even if the agent
    # cited them without reading the full .md.
    for match in _PAPER_REF_RE.finditer(answer):
        files.add(f"paper:{match.group(1)}")
    return sorted(files)


# --- main loop ----------------------------------------------------------


def run_ask_agent(
    db: Session,
    *,
    question: str,
    history: Optional[list[dict[str, str]]] = None,
    api_key: str,
    model: str,
) -> AskResult:
    if not api_key:
        raise AskAgentUnavailable("OpenAI API key not configured")
    if model_uses_responses_api(model):
        raise AskAgentUnavailable(
            f"Model {model!r} 走 Responses API，本 agent 暂未支持。"
            "请到 设置 把 wiki_compile_model 改成 chat.completions 兼容的模型（如 gpt-4o-mini）。"
        )

    started = perf_counter()
    client = OpenAI(api_key=api_key)

    messages: list[dict[str, Any]] = [{"role": "system", "content": ASK_SYSTEM_PROMPT}]
    for turn in history or []:
        role = turn.get("role")
        content = turn.get("content")
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": question})

    trace: list[TraceStep] = []
    final_answer: Optional[str] = None

    for step in range(MAX_STEPS):
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            temperature=0.3,
        )
        msg = resp.choices[0].message

        if msg.tool_calls:
            # Append assistant-with-tool-calls turn so the protocol is
            # well-formed; OpenAI requires this before the matching tool
            # results.
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            })
            for tc in msg.tool_calls:
                fn_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                t0 = perf_counter()
                result = _dispatch_tool(fn_name, args)
                dt = int((perf_counter() - t0) * 1000)
                trace.append(
                    TraceStep(
                        step=step,
                        tool=fn_name,
                        args=args,
                        result_summary=_summarize_result(result),
                        duration_ms=dt,
                    )
                )
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })
            continue

        # Terminal: model returned plain content, no tool calls.
        final_answer = (msg.content or "").strip()
        break

    if final_answer is None:
        # Hit MAX_STEPS without terminating. Ask the model to wrap up
        # with what it has — a softer failure than silently truncating.
        log.warning("ask_agent reached MAX_STEPS=%s without answer", MAX_STEPS)
        wrap_up = client.chat.completions.create(
            model=model,
            messages=messages
            + [
                {
                    "role": "user",
                    "content": "工具步数已达上限，请基于上面已经读到的内容直接给出 markdown 答案，不要再调用工具。",
                }
            ],
            temperature=0.3,
        )
        final_answer = (wrap_up.choices[0].message.content or "").strip()

    duration_ms = int((perf_counter() - started) * 1000)
    return AskResult(
        answer=final_answer,
        cited_files=_extract_cited_files(final_answer, trace),
        trace=trace,
        model=model,
        duration_ms=duration_ms,
        steps=len(trace),
    )
