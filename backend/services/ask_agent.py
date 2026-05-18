"""Wiki Q&A agent.

The user asks a free-form question; we hand the LLM a tiny tool kit
(list_wiki_index / search_wiki / read_wiki) and let it iterate until it
has enough context to write a markdown answer. Karpathy's blueprint
notes that at ~100 articles, this works well without RAG infrastructure
— the LLM reads index.md first, picks the right files, drills in.

Implementation choices:
  - chat.completions and Responses API both use tool calling here.
    Older chat-capable models stay on chat.completions; Responses-only
    models (gpt-5.x in this app) run the same tool loop via responses.create.
  - Tool dispatch is plain Python. Each call re-fetches the file from
    disk so multi-step questions always see the freshest wiki state.
  - Bounded loop (MAX_STEPS) — no infinite tool spirals on bad queries.

Output shape mirrors `routers.ask.AskResponse` — answer markdown plus a
trace the UI can fold open for debugging.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, Optional

from openai import OpenAI  # Back-compat target for existing tests/mocks.
from sqlalchemy.orm import Session

from config import load_config
from path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

from model_gateway import create_openai_client_for_model
from model_gateway import call_text_model, get_model_entry, get_provider_entry
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
LOCAL_ASK_SYSTEM_PROMPT = (
    "你是 Knowra 个人研究知识库的研究助手。系统已经把 index、搜索结果和若干 wiki 页面内容读给你了。"
    "请只基于这些给定材料综合回答，不要假装调用工具，不要编造库里没有的内容。"
    "输出中文 markdown，并在末尾列出 `## 📚 引用来源`。"
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


RESPONSES_TOOLS = [
    {
        "type": "function",
        "name": "list_wiki_index",
        "description": "返回 wiki/index.md 全文。先调用此工具看清楚库里有哪些论文与概念。",
        "parameters": {"type": "object", "properties": {}},
        "strict": False,
    },
    {
        "type": "function",
        "name": "search_wiki",
        "description": "在 wiki/papers + wiki/concepts 里做 FTS5 全文搜索。返回最多 12 个 hit。",
        "parameters": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "搜索关键词，至少 2 个字符"},
            },
            "required": ["q"],
        },
        "strict": False,
    },
    {
        "type": "function",
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
        "strict": False,
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
    citations: list[dict[str, Any]] = field(default_factory=list)
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
    kind = {"paper": "papers", "concept": "concepts"}.get(kind, kind)
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


def _history_messages(history: Optional[list[dict[str, str]]]) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for turn in history or []:
        role = turn.get("role")
        content = turn.get("content")
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})
    return messages


def _extract_responses_text(response) -> str:
    text = (getattr(response, "output_text", "") or "").strip()
    if text:
        return text

    parts: list[str] = []
    for item in getattr(response, "output", []) or []:
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", []) or []:
            ctype = getattr(content, "type", None)
            if ctype == "output_text":
                value = getattr(content, "text", "")
                if value:
                    parts.append(value)
            elif ctype == "refusal":
                refusal = getattr(content, "refusal", "")
                if refusal:
                    parts.append(f"[REFUSAL] {refusal}")
    return "\n".join(parts).strip()


# --- citation extraction ------------------------------------------------


_PAPER_REF_RE = re.compile(r"\[\[paper:(\d+)\]\]")
_PAPER_FILE_RE = re.compile(r"^data/wiki/papers/(\d+)-")
_CONCEPT_FILE_RE = re.compile(r"^data/wiki/concepts/")


def _extract_cited_files(answer: str, trace: list[TraceStep]) -> list[str]:
    """Best-effort: collect every wiki file the agent actually read, plus
    any paper id mentioned in the final answer (mapped to that paper's
    `.md`). De-duped, preserving discovery order."""
    files: list[str] = []
    seen: set[str] = set()

    def _append(item: str) -> None:
        if not item or item in seen:
            return
        seen.add(item)
        files.append(item)

    for step in trace:
        if step.tool == "read_wiki":
            kind = step.args.get("kind")
            fn = step.args.get("filename")
            if kind and fn:
                _append(f"data/wiki/{kind}/{fn}")
    # Paper ids mentioned in the answer body — useful even if the agent
    # cited them without reading the full .md.
    for match in _PAPER_REF_RE.finditer(answer):
        _append(f"paper:{match.group(1)}")
    return files


def _citation_from_ref(ref: str) -> Optional[dict[str, Any]]:
    if not ref:
        return None
    if ref.startswith("paper:"):
        try:
            pid = int(ref.split(":", 1)[1])
        except (TypeError, ValueError):
            return None
        return {
            "kind": "paper",
            "paper_id": pid,
            "path": ref,
            "filename": None,
            "ref": ref,
        }
    match = _PAPER_FILE_RE.match(ref)
    if match:
        return {
            "kind": "paper",
            "paper_id": int(match.group(1)),
            "path": ref,
            "filename": Path(ref).name,
            "ref": ref,
        }
    if _CONCEPT_FILE_RE.match(ref):
        return {
            "kind": "concept",
            "paper_id": None,
            "path": ref,
            "filename": Path(ref).name,
            "ref": ref,
        }
    return {
        "kind": "unknown",
        "paper_id": None,
        "path": ref,
        "filename": Path(ref).name if "/" in ref else None,
        "ref": ref,
    }


def _build_citations(cited_files: list[str]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for ref in cited_files:
        if ref in seen:
            continue
        seen.add(ref)
        citation = _citation_from_ref(ref)
        if citation is not None:
            citations.append(citation)
    return citations


def _provider_type_for_model(cfg: dict[str, Any], model: str) -> str:
    model_entry = get_model_entry(cfg, model)
    if model_entry is None:
        return "openai"
    provider = get_provider_entry(cfg, model_entry.get("provider_id", ""))
    if provider is None:
        return "openai"
    return str(provider.get("provider_type") or "openai")


def _parse_search_hits(search_result: str) -> list[dict[str, Any]]:
    try:
        parsed = json.loads(search_result)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    hits: list[dict[str, Any]] = []
    for item in parsed:
        if isinstance(item, dict):
            hits.append(item)
    return hits


def _run_local_retrieval_agent(
    cfg: dict[str, Any],
    *,
    question: str,
    history: Optional[list[dict[str, str]]],
    model: str,
    reasoning_effort: Optional[str],
) -> AskResult:
    started = perf_counter()
    trace: list[TraceStep] = []

    t0 = perf_counter()
    index_text = _tool_list_wiki_index()
    trace.append(
        TraceStep(
            step=0,
            tool="list_wiki_index",
            args={},
            result_summary=_summarize_result(index_text),
            duration_ms=int((perf_counter() - t0) * 1000),
        )
    )

    t1 = perf_counter()
    search_result = _tool_search_wiki(question)
    trace.append(
        TraceStep(
            step=1,
            tool="search_wiki",
            args={"q": question},
            result_summary=_summarize_result(search_result),
            duration_ms=int((perf_counter() - t1) * 1000),
        )
    )

    read_chunks: list[str] = []
    seen_files: set[tuple[str, str]] = set()
    for index, hit in enumerate(_parse_search_hits(search_result)):
        if len(read_chunks) >= 4:
            break
        filename = str(hit.get("filename") or "").strip()
        kind = {"paper": "papers", "concept": "concepts"}.get(
            str(hit.get("kind") or "").strip(),
            str(hit.get("kind") or "").strip(),
        )
        if not filename or kind not in {"papers", "concepts"}:
            continue
        key = (kind, filename)
        if key in seen_files:
            continue
        seen_files.add(key)
        tr = perf_counter()
        content = _tool_read_wiki(filename, kind)
        trace.append(
            TraceStep(
                step=2 + index,
                tool="read_wiki",
                args={"filename": filename, "kind": kind},
                result_summary=_summarize_result(content),
                duration_ms=int((perf_counter() - tr) * 1000),
            )
        )
        read_chunks.append(f"[{kind}/{filename}]\n{content}")

    history_lines: list[str] = []
    for turn in history or []:
        role = str(turn.get("role") or "").strip()
        content = str(turn.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            speaker = "用户" if role == "user" else "助手"
            history_lines.append(f"{speaker}: {content}")

    user_prompt = (
        "请基于下面提供的本地知识库材料回答问题。\n"
        "先综合 index、搜索结果和读到的 wiki 文件，再给出中文 markdown 答案。\n"
        "如果材料不足，请明确说明知识库里暂无足够材料，不要编造。\n"
        "答案末尾必须包含 `## 📚 引用来源`，只列出你在材料区真正读到的文件名。\n\n"
        f"[历史对话]\n{chr(10).join(history_lines) or '[无历史对话]'}\n\n"
        f"[当前问题]\n{question}\n\n"
        f"[index.md]\n{index_text}\n\n"
        f"[search_wiki 结果]\n{search_result}\n\n"
        f"[read_wiki 材料]\n{chr(10).join(read_chunks) or '[没有读取到具体 wiki 文件]'}"
    )
    final_answer = call_text_model(
        cfg,
        model_id=model,
        system=LOCAL_ASK_SYSTEM_PROMPT,
        user=user_prompt,
        reasoning_effort=reasoning_effort,
        max_tokens=2600,
        temperature=0.3,
    ).strip()
    duration_ms = int((perf_counter() - started) * 1000)
    cited_files = _extract_cited_files(final_answer, trace)
    return AskResult(
        answer=final_answer,
        cited_files=cited_files,
        citations=_build_citations(cited_files),
        trace=trace,
        model=model,
        duration_ms=duration_ms,
        steps=len(trace),
    )


# --- main loop ----------------------------------------------------------


def run_ask_agent(
    db: Session,
    *,
    question: str,
    history: Optional[list[dict[str, str]]] = None,
    api_key: str,
    model: str,
    reasoning_effort: Optional[str] = None,
) -> AskResult:
    started = perf_counter()
    cfg = load_config()
    if _provider_type_for_model(cfg, model) == "codex_cli":
        return _run_local_retrieval_agent(
            cfg,
            question=question,
            history=history,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    try:
        client, model, _, _ = create_openai_client_for_model(
            cfg,
            model,
            api_key_override=api_key,
        )
    except Exception as exc:
        raise AskAgentUnavailable(str(exc))
    trace: list[TraceStep] = []
    final_answer: Optional[str] = None

    if model_uses_responses_api(model):
        next_input: list[dict[str, Any]] = [
            *_history_messages(history),
            {"role": "user", "content": question},
        ]
        previous_response_id: Optional[str] = None

        for step in range(MAX_STEPS):
            response = client.responses.create(
                model=model,
                instructions=ASK_SYSTEM_PROMPT,
                input=next_input,
                tools=RESPONSES_TOOLS,
                tool_choice="auto",
                temperature=0.3,
                **(
                    {"reasoning": {"effort": reasoning_effort}}
                    if reasoning_effort in {"low", "medium", "high"}
                    else {}
                ),
                **(
                    {"previous_response_id": previous_response_id}
                    if previous_response_id
                    else {}
                ),
            )
            tool_calls = [
                item
                for item in (getattr(response, "output", []) or [])
                if getattr(item, "type", None) == "function_call"
            ]
            if tool_calls:
                previous_response_id = response.id
                next_input = []
                for tool_call in tool_calls:
                    fn_name = tool_call.name
                    try:
                        args = json.loads(tool_call.arguments or "{}")
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
                    next_input.append(
                        {
                            "type": "function_call_output",
                            "call_id": tool_call.call_id,
                            "output": result,
                        }
                    )
                continue

            final_answer = _extract_responses_text(response)
            break

        if final_answer is None:
            log.warning("ask_agent reached MAX_STEPS=%s without answer", MAX_STEPS)
            wrap_kwargs = {
                "model": model,
                "instructions": ASK_SYSTEM_PROMPT,
                "input": [
                    {
                        "role": "user",
                        "content": "工具步数已达上限，请基于上面已经读到的内容直接给出 markdown 答案，不要再调用工具。",
                    }
                ],
                "temperature": 0.3,
            }
            if reasoning_effort in {"low", "medium", "high"}:
                wrap_kwargs["reasoning"] = {"effort": reasoning_effort}
            if previous_response_id:
                wrap_kwargs["previous_response_id"] = previous_response_id
            else:
                wrap_kwargs["input"] = [
                    *_history_messages(history),
                    {"role": "user", "content": question},
                    {
                        "role": "user",
                        "content": "工具步数已达上限，请基于上面已经读到的内容直接给出 markdown 答案，不要再调用工具。",
                    },
                ]
            wrap_up = client.responses.create(**wrap_kwargs)
            final_answer = _extract_responses_text(wrap_up)
    else:
        messages: list[dict[str, Any]] = [{"role": "system", "content": ASK_SYSTEM_PROMPT}]
        messages.extend(_history_messages(history))
        messages.append({"role": "user", "content": question})

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
    cited_files = _extract_cited_files(final_answer, trace)
    return AskResult(
        answer=final_answer,
        cited_files=cited_files,
        citations=_build_citations(cited_files),
        trace=trace,
        model=model,
        duration_ms=duration_ms,
        steps=len(trace),
    )
