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

from presentation_preferences import response_instructions

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Optional

from openai import OpenAI  # Back-compat target for existing tests/mocks.
from sqlalchemy.orm import Session

from config import load_config
from path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

from model_gateway import create_openai_client_for_model
from model_gateway import call_text_model, get_model_entry, get_provider_entry
from model_gateway import task_context, track_call
from services import graph_query_service, wiki_index, wiki_search
from services.ask_retrieval_budget import (
    MAX_GRAPH_CALLS,
    MAX_INDEX_CHARS,
    MAX_MODEL_CALLS,
    MAX_ORCHESTRATION_MS,
    MAX_READ_CALLS,
    MAX_READ_CHARS as MAX_RUN_READ_CHARS,
    MAX_SEARCH_CALLS,
    MAX_TOOL_CALLS,
    RetrievalBudget,
)
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
LOCAL_READ_LIMIT = 4
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
    "- find_nodes(query) / get_graph_node(node_id) — 定位图节点\n"
    "- get_neighbors(node_id) / find_shared_neighbors(node_ids) — 查询邻居和共享概念\n"
    "- shortest_path(source_id, target_id) — 查询可追溯的结构路径\n"
    "- find_papers_for_concept(concept_id) / find_concepts_for_paper(paper_id) — 论文与概念互查\n"
    "- explain_edge(edge_id) — 读取边的 provenance 与证据\n"
    "\n"
    "策略：\n"
    "1. 内容定义/结论问题使用 index、search_wiki、read_wiki；结构关系问题使用 graph 工具\n"
    "2. 综合问题先用 graph 工具缩小节点范围，再 read_wiki 获取内容证据\n"
    "3. 图工具结果只证明节点/边/路径存在，不替代论文内容证据；不要从图标题臆测结论\n"
    "4. explain_edge 的 provenance/evidence 是关系解释的唯一依据，不得编造边的成因\n"
    "5. 读 2-5 个最相关的 .md 文件并综合回答；结构陈述写出节点或边 ID\n"
    "6. 答案末尾分列 `### 结构路径` 与 `### Wiki 内容来源`；没有某类证据时明确写无\n"
    "7. index 和 search_wiki 只能用于定位，不能单独支持论文内容结论；内容结论必须来自成功的 read_wiki\n"
    "8. 只引用本轮工具实际成功读取或返回的来源，不得把未读取的文件或 [[paper:id]] 当成引用\n"
    f"9. 根据结果调整路径：零命中时换用另一个明确标题/实体查询；有命中后读取候选，证据不足才继续检索。"
    f"每轮最多 {MAX_SEARCH_CALLS} 次搜索、{MAX_READ_CALLS} 次读取、{MAX_MODEL_CALLS} 次模型调用，"
    f"总工具调用不超过 {MAX_TOOL_CALLS} 次、图查询不超过 {MAX_GRAPH_CALLS} 次，"
    f"read_wiki 正文不超过 {MAX_RUN_READ_CHARS} 字符、index 最多 {MAX_INDEX_CHARS} 字符，"
    f"编排墙钟预算 {MAX_ORCHESTRATION_MS // 1000} 秒；不要重复调用\n"
    "\n"
    "重要：基于材料综合，不要编造。如果库里材料不足，明说\"知识库里没有相关材料\"。\n"
    "输出 markdown，从 ## 二级标题开始，可用列表 / 引用块 / 公式块。不要 markdown 代码围栏包裹整个答案。"
)
LOCAL_ASK_SYSTEM_PROMPT = (
    "你是 Knowra 个人研究知识库的研究助手。系统已经把 index、搜索结果和若干 wiki 页面内容读给你了。"
    "请只基于这些给定材料综合回答，不要假装调用工具，不要编造库里没有的内容。"
    "index 和搜索摘要只用于定位，论文内容结论必须来自 `[read_wiki 材料]`。"
    "输出中文 markdown；引用来源将由系统根据实际读取记录统一附加，不要自行编造来源。"
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
            "description": "在 wiki/papers + wiki/concepts 里做 FTS5 全文搜索。返回实际查询模式、尝试记录和最多 12 个 hit。",
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

_GRAPH_TOOL_DEFS = [
    ("find_nodes", "按标题或别名查找策展图节点。", {"query": {"type": "string"}, "node_type": {"type": "string"}, "limit": {"type": "integer"}}, ["query"]),
    ("get_graph_node", "读取一个策展图节点。", {"node_id": {"type": "string"}}, ["node_id"]),
    ("get_neighbors", "读取节点的有界邻居。", {"node_id": {"type": "string"}, "relation_type": {"type": "string"}, "depth": {"type": "integer"}, "limit": {"type": "integer"}}, ["node_id"]),
    ("shortest_path", "查询两个节点间的最短结构路径。", {"source_id": {"type": "string"}, "target_id": {"type": "string"}, "max_depth": {"type": "integer"}}, ["source_id", "target_id"]),
    ("find_shared_neighbors", "查询两个或更多节点的共同邻居。", {"node_ids": {"type": "array", "items": {"type": "string"}}, "relation_type": {"type": "string"}, "limit": {"type": "integer"}}, ["node_ids"]),
    ("find_papers_for_concept", "查询与概念直接相连的论文。", {"concept_id": {"type": "string"}, "limit": {"type": "integer"}}, ["concept_id"]),
    ("find_concepts_for_paper", "查询论文直接关联的概念。", {"paper_id": {"type": "string"}, "limit": {"type": "integer"}}, ["paper_id"]),
    ("explain_edge", "读取边及其来源、置信度和证据。", {"edge_id": {"type": "string"}}, ["edge_id"]),
]

for _name, _description, _properties, _required in _GRAPH_TOOL_DEFS:
    TOOLS.append({
        "type": "function",
        "function": {
            "name": _name,
            "description": _description,
            "parameters": {"type": "object", "properties": _properties, "required": _required},
        },
    })


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
        "description": "在 wiki/papers + wiki/concepts 里做 FTS5 全文搜索。返回实际查询模式、尝试记录和最多 12 个 hit。",
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

for _name, _description, _properties, _required in _GRAPH_TOOL_DEFS:
    RESPONSES_TOOLS.append({
        "type": "function",
        "name": _name,
        "description": _description,
        "parameters": {"type": "object", "properties": _properties, "required": _required},
        "strict": False,
    })


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
    hit_node_ids: list[str] = field(default_factory=list)
    raw_result: str = field(default="", repr=False)


@dataclass
class AskResult:
    answer: str
    cited_files: list[str] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    trace: list[TraceStep] = field(default_factory=list)
    model: str = ""
    duration_ms: int = 0
    steps: int = 0


@dataclass(frozen=True)
class EvidenceAssessment:
    status: str
    required_content_sources: int
    content_refs: tuple[str, ...]
    graph_evidence_count: int
    search_entity_terms: int
    index_available: bool
    reason: str


# --- tool dispatchers ---------------------------------------------------


def _safe_read(path: Path, *, max_chars: int = READ_MAX_CHARS) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"[error: {exc}]"
    return _truncate_text(text, max_chars=max_chars, label="文件")


def _truncate_text(text: str, *, max_chars: int, label: str) -> str:
    max_chars = max(0, int(max_chars))
    if len(text) > max_chars:
        suffix = f"\n\n…（{label}已截断，原始长度 {len(text)} 字符）"
        if len(suffix) >= max_chars:
            return suffix[:max_chars]
        return text[: max_chars - len(suffix)] + suffix
    return text


def _tool_list_wiki_index() -> str:
    text = wiki_index.read_index()
    if text is None:
        return (
            "[index.md 不存在 — 请先调用 POST /api/wiki/index/rebuild 或在前端点重建索引]"
        )
    return _truncate_text(text, max_chars=MAX_INDEX_CHARS, label="索引")


def _tool_search_wiki(q: str) -> str:
    if not q or len(q.strip()) < 2:
        return "[搜索词太短，至少 2 字符]"
    search_result = wiki_search.search_with_fallback(q.strip(), limit=SEARCH_HIT_LIMIT)
    hits = search_result["hits"]
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
    payload = {
        "mode": search_result["mode"],
        "executed_queries": [
            attempt["query"]
            for attempt in (
                search_result["attempts"][1:]
                if len(search_result["attempts"]) > 1
                else search_result["attempts"]
            )
            if attempt.get("query")
        ],
        "query": search_result["query"],
        "terms": search_result["terms"],
        "attempts": search_result["attempts"],
        "hits": compact,
    }
    if not hits:
        payload["message"] = "没有匹配的 wiki 文件"
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _tool_read_wiki(
    filename: str,
    kind: str,
    *,
    max_chars: int = READ_MAX_CHARS,
) -> str:
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
    return _safe_read(path, max_chars=max_chars)


def _dispatch_tool(
    db: Session,
    name: str,
    args: dict[str, Any],
    *,
    read_max_chars: int = READ_MAX_CHARS,
) -> str:
    if name == "list_wiki_index":
        return _tool_list_wiki_index()
    if name == "search_wiki":
        return _tool_search_wiki(args.get("q", ""))
    if name == "read_wiki":
        return _tool_read_wiki(
            args.get("filename", ""),
            args.get("kind", ""),
            max_chars=read_max_chars,
        )
    graph_calls = {
        "find_nodes": lambda: graph_query_service.find_nodes(db, args.get("query", ""), args.get("node_type"), args.get("limit", 20)),
        "get_graph_node": lambda: graph_query_service.get_node(db, args.get("node_id", "")),
        "get_neighbors": lambda: graph_query_service.get_neighbors(db, args.get("node_id", ""), args.get("relation_type"), args.get("depth", 1), args.get("limit", 20)),
        "shortest_path": lambda: graph_query_service.shortest_path(db, args.get("source_id", ""), args.get("target_id", ""), args.get("max_depth", 4)),
        "find_shared_neighbors": lambda: graph_query_service.find_shared_neighbors(db, args.get("node_ids", []), args.get("relation_type"), args.get("limit", 20)),
        "find_papers_for_concept": lambda: graph_query_service.find_papers_for_concept(db, args.get("concept_id", ""), args.get("limit", 20)),
        "find_concepts_for_paper": lambda: graph_query_service.find_concepts_for_paper(db, args.get("paper_id", ""), args.get("limit", 20)),
        "explain_edge": lambda: graph_query_service.explain_edge(db, args.get("edge_id", "")),
    }
    if name in graph_calls:
        try:
            return json.dumps(graph_calls[name](), ensure_ascii=False, separators=(",", ":"))
        except (graph_query_service.GraphQueryError, TypeError, ValueError) as exc:
            return f"[graph query error: {exc}]"
    return f"[unknown tool: {name}]"


def _hit_node_ids(result: str) -> list[str]:
    try:
        payload = json.loads(result)
    except Exception:
        return []
    found: list[str] = []
    candidates = []
    if isinstance(payload, dict):
        candidates.extend(payload.get("nodes") or [])
        if isinstance(payload.get("node"), dict):
            candidates.append(payload["node"])
        for key in ("root", "source", "target"):
            if isinstance(payload.get(key), dict):
                candidates.append(payload[key])
    for node in candidates:
        node_id = str(node.get("id") or "") if isinstance(node, dict) else ""
        if node_id and node_id not in found:
            found.append(node_id)
    return found[: graph_query_service.MAX_RESULTS]


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
_INDEX_REF = "data/wiki/index.md"
_SOURCE_SECTION_RE = re.compile(
    r"(?ims)^\s*#{2,3}\s*(?:📚\s*)?"
    r"(?:引用来源|wiki\s*内容来源|sources?|references?)\s*$.*\Z"
)
_TOOL_FAILURE_PREFIXES = (
    "[duplicate tool call blocked",
    "[tool call limit reached",
    "[retrieval budget reached:",
    "[unknown tool:",
)
_READ_ERROR_PREFIXES = _TOOL_FAILURE_PREFIXES + (
    "[error:",
    "[file not found:",
    "[invalid filename:",
    "[unknown kind:",
)
_STRUCTURAL_EVIDENCE_TOOLS = {
    "get_neighbors",
    "shortest_path",
    "find_shared_neighbors",
    "find_papers_for_concept",
    "find_concepts_for_paper",
    "explain_edge",
}
_MULTI_SOURCE_MARKERS = (
    "比较", "对比", "异同", "跨论文", "多篇",
    "compare", "comparison", "versus", " vs ", "differences", "across papers",
)
_CATALOG_MARKERS = (
    "知识库里有什么", "知识库有什么", "知识库里有哪些", "知识库包含什么",
    "列出知识库", "查看知识库索引", "知识库索引", "知识库目录",
    "what is in the library", "what's in the library", "which papers are in the library",
    "which papers are in the knowledge base", "list the library", "library index", "knowledge base index",
    "library catalog", "knowledge base catalog",
)


def _extract_cited_files(answer: str, trace: list[TraceStep]) -> list[str]:
    """Return only Wiki files successfully read by a tool in this run.

    The answer text is intentionally ignored: a model-emitted filename or
    ``[[paper:id]]`` marker is not evidence that the source was opened.
    """
    del answer
    files: list[str] = []
    seen: set[str] = set()

    def _append(item: str) -> None:
        if not item or item in seen:
            return
        seen.add(item)
        files.append(item)

    for step in trace:
        if step.tool != "read_wiki" or not _successful_wiki_read(step.raw_result):
            continue
        kind = str(step.args.get("kind") or "").strip()
        kind = {"paper": "papers", "concept": "concepts"}.get(kind, kind)
        filename = str(step.args.get("filename") or "").strip()
        if kind not in {"papers", "concepts"} or not filename:
            continue
        if Path(filename).name != filename:
            continue
        _append(f"data/wiki/{kind}/{filename}")
    return files


def _successful_wiki_read(result: str) -> bool:
    text = (result or "").strip()
    return bool(text) and not text.casefold().startswith(_READ_ERROR_PREFIXES)


def _citation_from_ref(ref: str) -> Optional[dict[str, Any]]:
    if not ref:
        return None
    if ref == _INDEX_REF:
        return {
            "kind": "index",
            "paper_id": None,
            "path": ref,
            "filename": "index.md",
            "ref": ref,
        }
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


def _graph_citations(trace: list[TraceStep]) -> list[dict[str, Any]]:
    citations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for step in trace:
        if step.tool not in {name for name, *_ in _GRAPH_TOOL_DEFS}:
            continue
        for node_id in step.hit_node_ids:
            ref = f"graph-node:{node_id}"
            if ref not in seen:
                seen.add(ref)
                citations.append({"kind": "graph_node", "ref": ref, "path": None, "filename": None, "paper_id": None})
        try:
            payload = json.loads(step.raw_result or "{}")
        except Exception:
            payload = {}
        edges = payload.get("edges", []) if isinstance(payload, dict) else []
        if isinstance(payload, dict) and isinstance(payload.get("edge"), dict):
            edges = [*edges, payload["edge"]] if isinstance(edges, list) else [payload["edge"]]
        for edge in edges if isinstance(edges, list) else []:
            edge_id = str(edge.get("id") or "") if isinstance(edge, dict) else ""
            ref = f"graph-edge:{edge_id}"
            if edge_id and ref not in seen:
                seen.add(ref)
                citations.append({"kind": "graph_edge", "ref": ref, "path": None, "filename": None, "paper_id": None})
    return citations


def _question_needs_multiple_sources(question: str) -> bool:
    lower = f" {(question or '').casefold()} "
    return any(marker in lower for marker in _MULTI_SOURCE_MARKERS)


def _search_entity_term_count(trace: list[TraceStep]) -> int:
    count = 0
    for step in trace:
        if step.tool != "search_wiki":
            continue
        try:
            payload = json.loads(step.raw_result or "{}")
        except Exception:
            continue
        terms = payload.get("terms") if isinstance(payload, dict) else None
        if isinstance(terms, list):
            count = max(count, len({str(term).casefold() for term in terms if str(term).strip()}))
    return count


def _question_is_catalog_request(question: str) -> bool:
    lower = (question or "").casefold()
    return any(marker in lower for marker in _CATALOG_MARKERS)


def _successful_index_read(result: str) -> bool:
    text = (result or "").strip()
    return (
        bool(text)
        and not text.startswith("[index.md 不存在")
        and not text.casefold().startswith(_TOOL_FAILURE_PREFIXES)
    )


def _structural_evidence_count(trace: list[TraceStep]) -> int:
    count = 0
    for step in trace:
        if step.tool not in _STRUCTURAL_EVIDENCE_TOOLS:
            continue
        try:
            payload = json.loads(step.raw_result or "{}")
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if step.tool == "shortest_path":
            usable = payload.get("found") is True
        elif step.tool == "explain_edge":
            usable = isinstance(payload.get("edge"), dict)
        else:
            raw_count = payload.get("count")
            usable = isinstance(raw_count, int) and not isinstance(raw_count, bool) and raw_count > 0
        if usable:
            count += 1
    return count


def _assess_evidence(question: str, trace: list[TraceStep]) -> EvidenceAssessment:
    content_refs = tuple(_extract_cited_files("", trace))
    search_entity_terms = _search_entity_term_count(trace)
    required = 2 if _question_needs_multiple_sources(question) or search_entity_terms >= 2 else 1
    graph_count = _structural_evidence_count(trace)
    index_available = any(
        step.tool == "list_wiki_index" and _successful_index_read(step.raw_result)
        for step in trace
    )

    if len(content_refs) >= required:
        status = "supported" if len(content_refs) >= 2 else "limited"
        reason = (
            f"已成功读取 {len(content_refs)} 份 Wiki 内容材料，"
            f"达到本问题的 {required} 份来源门槛。"
        )
    elif _question_needs_graph(question) and graph_count:
        status = "structure_only"
        reason = (
            f"已取得 {graph_count} 项可验证图结构结果，但只成功读取 "
            f"{len(content_refs)} 份 Wiki 内容材料；只允许回答结构关系。"
        )
    elif index_available and _question_is_catalog_request(question):
        status = "catalog_only"
        reason = "已读取 Wiki 索引；只允许回答库内条目概览。"
    else:
        status = "insufficient"
        if content_refs:
            reason = (
                f"本问题需要至少 {required} 份 Wiki 内容材料，"
                f"当前只成功读取 {len(content_refs)} 份。"
            )
        else:
            reason = "没有成功读取到可支持内容结论的 Wiki 文件。"

    return EvidenceAssessment(
        status=status,
        required_content_sources=required,
        content_refs=content_refs,
        graph_evidence_count=graph_count,
        search_entity_terms=search_entity_terms,
        index_available=index_available,
        reason=reason,
    )


def _citations_for_assessment(
    assessment: EvidenceAssessment,
    trace: list[TraceStep],
) -> tuple[list[str], list[dict[str, Any]]]:
    if assessment.status == "insufficient":
        return [], []
    if assessment.status == "catalog_only":
        citation = _citation_from_ref(_INDEX_REF)
        return [_INDEX_REF], [citation] if citation is not None else []

    cited_files = list(assessment.content_refs)
    citations = _build_citations(cited_files)
    if assessment.status in {"supported", "limited", "structure_only"}:
        citations.extend(_graph_citations(trace))
    return cited_files, citations


def _finalize_answer(
    answer: str,
    assessment: EvidenceAssessment,
    citations: list[dict[str, Any]],
) -> str:
    if assessment.status == "insufficient":
        return (
            "## 证据不足\n\n"
            f"{assessment.reason}\n\n"
            "我不会根据搜索摘要、索引标题、图节点名称或模型自行生成的引用推断论文结论。\n\n"
            "## 📚 引用来源\n\n无"
        )

    body = _SOURCE_SECTION_RE.sub("", (answer or "").strip()).strip()
    allowed_paper_ids = {
        str(citation["paper_id"])
        for citation in citations
        if citation.get("paper_id") is not None
    }
    body = _PAPER_REF_RE.sub(
        lambda match: match.group(0) if match.group(1) in allowed_paper_ids else "",
        body,
    ).strip()

    boundaries = {
        "limited": (
            "> 证据边界：当前只成功读取 1 份 Wiki 内容材料；以下回答只代表该来源，"
            "不能视为完整的跨论文结论。"
        ),
        "structure_only": (
            "> 证据边界：以下只能回答工具返回的图结构关系；Wiki 内容证据未达到门槛，"
            "不能据此推断论文方法、结论或演进。"
        ),
        "catalog_only": (
            "> 证据边界：以下仅基于 Wiki 索引回答库内条目概览，"
            "索引标题不能支持论文内容结论。"
        ),
    }
    boundary = boundaries.get(assessment.status)
    if boundary:
        body = f"{body}\n\n{boundary}" if body else boundary

    refs: list[str] = []
    for citation in citations:
        ref = str(citation.get("ref") or "").strip()
        if ref and ref not in refs:
            refs.append(ref)
    source_lines = "\n".join(f"- `{ref}`" for ref in refs) or "无"
    return f"{body}\n\n## 📚 引用来源\n\n{source_lines}".strip()


def _finish_result(
    *,
    answer: str,
    question: str,
    trace: list[TraceStep],
    model: str,
    duration_ms: int,
    assessment: Optional[EvidenceAssessment] = None,
    budget: Optional[RetrievalBudget] = None,
) -> AskResult:
    assessment = assessment or _assess_evidence(question, trace)
    cited_files, citations = _citations_for_assessment(assessment, trace)
    if budget is not None:
        budget_payload = json.dumps(
            budget.snapshot(),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        trace.append(TraceStep(
            step=len(trace),
            tool="retrieval_budget",
            args={},
            result_summary=budget.summary_text(),
            duration_ms=0,
            raw_result=budget_payload,
        ))
    gate_payload = json.dumps(
        {
            "status": assessment.status,
            "required_content_sources": assessment.required_content_sources,
            "content_sources": len(assessment.content_refs),
            "graph_evidence": assessment.graph_evidence_count,
            "search_entity_terms": assessment.search_entity_terms,
            "index_available": assessment.index_available,
            "reason": assessment.reason,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    trace.append(_trace_step(
        len(trace),
        "evidence_gate",
        {"required_content_sources": assessment.required_content_sources},
        gate_payload,
        0,
    ))
    return AskResult(
        answer=_finalize_answer(answer, assessment, citations),
        cited_files=cited_files,
        citations=citations,
        trace=trace,
        model=model,
        duration_ms=duration_ms,
        steps=len(trace),
    )


def _trace_step(step: int, tool: str, args: dict[str, Any], result: str, duration_ms: int) -> TraceStep:
    return TraceStep(
        step=step,
        tool=tool,
        args=args,
        result_summary=_summarize_result(result),
        duration_ms=duration_ms,
        hit_node_ids=_hit_node_ids(result),
        raw_result=result,
    )


_TOOL_FINGERPRINT_FIELDS = {
    "list_wiki_index": (),
    "search_wiki": ("q",),
    "read_wiki": ("filename", "kind"),
    "find_nodes": ("query", "node_type", "limit"),
    "get_graph_node": ("node_id",),
    "get_neighbors": ("node_id", "relation_type", "depth", "limit"),
    "shortest_path": ("source_id", "target_id", "max_depth"),
    "find_shared_neighbors": ("node_ids", "relation_type", "limit"),
    "find_papers_for_concept": ("concept_id", "limit"),
    "find_concepts_for_paper": ("paper_id", "limit"),
    "explain_edge": ("edge_id",),
}
_INTEGER_TOOL_ARGS = {"limit", "depth", "max_depth"}
_TOOL_ARG_DEFAULTS = {
    "find_nodes": {"node_type": None, "limit": 20},
    "get_neighbors": {"relation_type": None, "depth": 1, "limit": 20},
    "shortest_path": {"max_depth": 4},
    "find_shared_neighbors": {"relation_type": None, "limit": 20},
    "find_papers_for_concept": {"limit": 20},
    "find_concepts_for_paper": {"limit": 20},
}


def _normalize_tool_arg(name: str, key: str, value: Any) -> Any:
    if key == "kind":
        normalized = str(value or "").strip().casefold()
        return {"paper": "papers", "concept": "concepts"}.get(normalized, normalized)
    if key == "node_ids" and isinstance(value, list):
        return sorted(str(item).strip() for item in value if str(item).strip())
    if key in _INTEGER_TOOL_ARGS:
        try:
            return int(value)
        except (TypeError, ValueError):
            return value
    if isinstance(value, str):
        normalized = " ".join(value.split())
        if key in {"q", "query", "node_type", "relation_type"}:
            return normalized.casefold()
        return normalized
    return value


def _tool_call_fingerprint(name: str, args: dict[str, Any]) -> str:
    fields = _TOOL_FINGERPRINT_FIELDS.get(name)
    if fields is None:
        normalized = args
    else:
        normalized = {
            key: _normalize_tool_arg(
                name,
                key,
                (
                    args[key]
                    if key in args
                    else _TOOL_ARG_DEFAULTS.get(name, {}).get(key)
                ),
            )
            for key in fields
        }
    return f"{name}:{json.dumps(normalized, ensure_ascii=False, sort_keys=True, default=str)}"


def _execute_tool_once(
    db: Session,
    name: str,
    args: dict[str, Any],
    seen_calls: set[str],
    budget: RetrievalBudget,
) -> tuple[str, int]:
    fingerprint = _tool_call_fingerprint(name, args)
    if fingerprint in seen_calls:
        budget.record_block("duplicate_tool_call")
        return "[duplicate tool call blocked; use the existing result]", 0
    is_graph = name in {tool_name for tool_name, *_ in _GRAPH_TOOL_DEFS}
    reason = budget.tool_block_reason(name, is_graph=is_graph)
    if reason:
        budget.record_block(reason)
        return f"[retrieval budget reached: {reason}]", 0
    seen_calls.add(fingerprint)
    started = perf_counter()
    read_limit = min(READ_MAX_CHARS, budget.remaining_read_chars())
    if name == "read_wiki":
        result = _dispatch_tool(db, name, args, read_max_chars=read_limit)
    else:
        result = _dispatch_tool(db, name, args)
    duration_ms = int((perf_counter() - started) * 1000)
    budget.record_tool_call(
        name,
        duration_ms,
        is_graph=is_graph,
        read_chars=(
            len(result)
            if name == "read_wiki" and _successful_wiki_read(result)
            else 0
        ),
        index_chars=(
            len(result)
            if name == "list_wiki_index" and _successful_index_read(result)
            else 0
        ),
    )
    return result, duration_ms


def _run_budgeted_model_call(
    budget: RetrievalBudget,
    call: Callable[[], Any],
) -> tuple[Any, Optional[str]]:
    reason = budget.model_block_reason()
    if reason:
        budget.record_block(reason)
        return None, reason
    started = perf_counter()
    try:
        return call(), None
    finally:
        budget.record_model_call(int((perf_counter() - started) * 1000))


def _budget_exhausted_answer(reason: str) -> str:
    return (
        "## 检索预算已用尽\n\n"
        f"已停止继续调用检索或模型（{reason}）。"
        "下面只保留本轮已经取得并通过来源门槛的材料边界。"
    )


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
    if isinstance(parsed, dict):
        parsed = parsed.get("hits")
    if not isinstance(parsed, list):
        return []
    hits: list[dict[str, Any]] = []
    for item in parsed:
        if isinstance(item, dict):
            hits.append(item)
    return hits


def _parse_search_terms(search_result: str) -> list[str]:
    try:
        parsed = json.loads(search_result)
    except Exception:
        return []
    terms = parsed.get("terms") if isinstance(parsed, dict) else None
    if not isinstance(terms, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for raw in terms:
        term = str(raw or "").strip()
        normalized = term.casefold()
        if len(term) < 2 or normalized in seen:
            continue
        seen.add(normalized)
        result.append(term)
    return result


def _balanced_search_hits(
    search_results: list[tuple[str, str]],
    *,
    limit: int = LOCAL_READ_LIMIT,
) -> list[dict[str, Any]]:
    """Round-robin primary/entity routes so one route cannot consume all reads."""
    routes = [(route, _parse_search_hits(result)) for route, result in search_results]
    selected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    max_hits = max((len(hits) for _route, hits in routes), default=0)
    for index in range(max_hits):
        for route, hits in routes:
            if index >= len(hits):
                continue
            hit = dict(hits[index])
            key = (
                str(hit.get("kind") or ""),
                str(hit.get("filename") or ""),
            )
            if not key[1] or key in seen:
                continue
            seen.add(key)
            hit["retrieval_route"] = route
            selected.append(hit)
            if len(selected) >= limit:
                return selected
    return selected


def _local_graph_context(
    db: Session,
    question: str,
    trace: list[TraceStep],
    seen_calls: set[str],
    budget: RetrievalBudget,
) -> str:
    """Deterministic graph pre-routing for providers without tool calling."""
    chunks: list[str] = []
    find_args = {"query": question, "limit": 8}
    raw, duration_ms = _execute_tool_once(
        db, "find_nodes", find_args, seen_calls, budget,
    )
    trace.append(_trace_step(
        len(trace), "find_nodes", find_args, raw, duration_ms,
    ))
    chunks.append(f"[find_nodes]\n{raw}")
    try:
        found = json.loads(raw)
    except Exception:
        found = {}
    if not isinstance(found, dict):
        found = {}
    nodes = found.get("nodes", [])
    paper_ids = [item["id"] for item in nodes if item.get("node_type") == "paper"]
    lower = question.casefold()
    structural = any(token in lower for token in ("shared", "common", "connect", "path", "related", "共享", "共同", "连接", "路径", "关系"))
    if structural and len(paper_ids) >= 2:
        args: dict[str, Any]
        if any(token in lower for token in ("shared", "common", "共享", "共同")):
            tool = "find_shared_neighbors"
            args = {"node_ids": paper_ids[:4]}
        else:
            tool = "shortest_path"
            args = {"source_id": paper_ids[0], "target_id": paper_ids[1], "max_depth": 4}
        result, duration_ms = _execute_tool_once(
            db, tool, args, seen_calls, budget,
        )
        trace.append(_trace_step(
            len(trace), tool, args, result, duration_ms,
        ))
        chunks.append(f"[{tool}]\n{result}")
    return "\n\n".join(chunks)


def _question_needs_graph(question: str) -> bool:
    lower = (question or "").casefold()
    return any(token in lower for token in (
        "shared", "common", "connect", "path", "related", "bridge", "neighbor",
        "共享", "共同", "连接", "路径", "关系", "关联", "桥接", "邻居",
    ))


def _run_local_retrieval_agent(
    db: Session,
    cfg: dict[str, Any],
    *,
    question: str,
    history: Optional[list[dict[str, str]]],
    model: str,
    reasoning_effort: Optional[str],
) -> AskResult:
    started = perf_counter()
    trace: list[TraceStep] = []
    budget = RetrievalBudget()
    seen_tool_calls: set[str] = set()

    graph_context = (
        _local_graph_context(
            db, question, trace, seen_tool_calls, budget,
        )
        if _question_needs_graph(question)
        else "[内容型问题：未调用图工具]"
    )

    index_text, duration_ms = _execute_tool_once(
        db, "list_wiki_index", {}, seen_tool_calls, budget,
    )
    trace.append(_trace_step(
        len(trace), "list_wiki_index", {}, index_text,
        duration_ms,
    ))

    primary_args = {"q": question, "route": "primary"}
    search_result, duration_ms = _execute_tool_once(
        db, "search_wiki", primary_args, seen_tool_calls, budget,
    )
    trace.append(_trace_step(
        len(trace), "search_wiki", primary_args, search_result, duration_ms,
    ))

    search_results: list[tuple[str, str]] = [("primary", search_result)]
    for term in _parse_search_terms(search_result):
        route_block = budget.tool_block_reason("search_wiki")
        if route_block:
            budget.record_block(route_block)
            break
        route = f"entity:{term}"
        args = {"q": term, "route": route}
        result, duration_ms = _execute_tool_once(
            db, "search_wiki", args, seen_tool_calls, budget,
        )
        trace.append(_trace_step(
            len(trace), "search_wiki", args, result, duration_ms,
        ))
        search_results.append((route, result))

    read_chunks: list[str] = []
    seen_files: set[tuple[str, str]] = set()
    for hit in _balanced_search_hits(search_results, limit=MAX_READ_CALLS):
        if len(read_chunks) >= LOCAL_READ_LIMIT:
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
        read_args = {
            "filename": filename,
            "kind": kind,
            "route": hit.get("retrieval_route") or "primary",
        }
        content, duration_ms = _execute_tool_once(
            db, "read_wiki", read_args, seen_tool_calls, budget,
        )
        trace.append(_trace_step(
            len(trace), "read_wiki", read_args, content, duration_ms,
        ))
        if _successful_wiki_read(content):
            read_chunks.append(f"[{kind}/{filename}]\n{content}")

    assessment = _assess_evidence(question, trace)
    if assessment.status == "insufficient":
        return _finish_result(
            answer="",
            question=question,
            trace=trace,
            model=model,
            duration_ms=int((perf_counter() - started) * 1000),
            assessment=assessment,
            budget=budget,
        )

    history_lines: list[str] = []
    for turn in history or []:
        role = str(turn.get("role") or "").strip()
        content = str(turn.get("content") or "").strip()
        if role in {"user", "assistant"} and content:
            speaker = "用户" if role == "user" else "助手"
            history_lines.append(f"{speaker}: {content}")

    user_prompt = response_instructions(
        "请基于下面提供的本地知识库材料回答问题。\n"
        "先综合 index、搜索结果和读到的 wiki 文件，再给出中文 markdown 答案。\n"
        "如果材料不足，请明确说明知识库里暂无足够材料，不要编造。\n"
        "不要自行生成引用列表；系统会根据本轮成功读取记录附加已核验来源。\n\n",
        cfg.get("prompt_locale", "zh"),
    ) + (
        f"[历史对话]\n{chr(10).join(history_lines) or '[无历史对话]'}\n\n"
        f"[当前问题]\n{question}\n\n"
        f"[图结构检索]\n{graph_context}\n\n"
        f"[index.md]\n{index_text}\n\n"
        "[search_wiki 多路结果]\n"
        + "\n\n".join(
            f"[{route}]\n{result}" for route, result in search_results
        )
        + "\n\n"
        + f"[read_wiki 材料]\n{chr(10).join(read_chunks) or '[没有读取到具体 wiki 文件]'}"
    )
    final_answer, budget_reason = _run_budgeted_model_call(
        budget,
        lambda: call_text_model(
            cfg,
            model_id=model,
            system=response_instructions(LOCAL_ASK_SYSTEM_PROMPT, cfg.get("prompt_locale", "zh")),
            user=user_prompt,
            reasoning_effort=reasoning_effort,
            max_tokens=2600,
            temperature=0.3,
            timeout_s=budget.remaining_timeout_s(),
        ),
    )
    if budget_reason:
        final_answer = _budget_exhausted_answer(budget_reason)
    else:
        final_answer = str(final_answer or "").strip()
    duration_ms = int((perf_counter() - started) * 1000)
    return _finish_result(
        answer=final_answer,
        question=question,
        trace=trace,
        model=model,
        duration_ms=duration_ms,
        assessment=assessment,
        budget=budget,
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
    # Tag every LLM call inside this function (and the codex_cli fallback)
    # with "ask_agent" so dashboard cost breakdowns attribute them
    # correctly. Telemetry is best-effort — never reraises.
    with task_context("ask_agent"):
        return _run_ask_agent_inner(
            db,
            question=question,
            history=history,
            api_key=api_key,
            model=model,
            reasoning_effort=reasoning_effort,
        )


def _run_ask_agent_inner(
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
            db,
            cfg,
            question=question,
            history=history,
            model=model,
            reasoning_effort=reasoning_effort,
        )
    try:
        client, model, provider_blob, _ = create_openai_client_for_model(
            cfg,
            model,
            api_key_override=api_key,
        )
    except Exception as exc:
        raise AskAgentUnavailable(str(exc))
    if isinstance(client, OpenAI):
        client = client.with_options(max_retries=0)
    provider_id = str(provider_blob.get("id") or "openai")
    trace: list[TraceStep] = []
    final_answer: Optional[str] = None
    seen_tool_calls: set[str] = set()
    budget = RetrievalBudget()

    if model_uses_responses_api(model):
        next_input: list[dict[str, Any]] = [
            *_history_messages(history),
            {"role": "user", "content": question},
        ]
        previous_response_id: Optional[str] = None

        for step in range(MAX_STEPS):
            response, budget_reason = _run_budgeted_model_call(
                budget,
                lambda: track_call(
                    lambda: client.responses.create(
                        model=model,
                        instructions=response_instructions(ASK_SYSTEM_PROMPT, cfg.get("prompt_locale", "zh")),
                        input=next_input,
                        tools=RESPONSES_TOOLS,
                        tool_choice="auto",
                        temperature=0.3,
                        timeout=budget.remaining_timeout_s(),
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
                    ),
                    provider=provider_id,
                    model=model,
                    surface="responses",
                ),
            )
            if budget_reason:
                final_answer = _budget_exhausted_answer(budget_reason)
                break
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
                    result, dt = _execute_tool_once(
                        db, fn_name, args, seen_tool_calls, budget,
                    )
                    trace.append(_trace_step(step, fn_name, args, result, dt))
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
                "instructions": response_instructions(ASK_SYSTEM_PROMPT, cfg.get("prompt_locale", "zh")),
                "input": [
                    {
                        "role": "user",
                        "content": "工具步数已达上限，请基于上面已经读到的内容直接给出 markdown 答案，不要再调用工具。",
                    }
                ],
                "temperature": 0.3,
                "timeout": budget.remaining_timeout_s(),
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
            wrap_up, budget_reason = _run_budgeted_model_call(
                budget,
                lambda: track_call(
                    lambda: client.responses.create(**wrap_kwargs),
                    provider=provider_id,
                    model=model,
                    surface="responses",
                ),
            )
            final_answer = (
                _budget_exhausted_answer(budget_reason)
                if budget_reason
                else _extract_responses_text(wrap_up)
            )
    else:
        messages: list[dict[str, Any]] = [{"role": "system", "content": response_instructions(ASK_SYSTEM_PROMPT, cfg.get("prompt_locale", "zh"))}]
        messages.extend(_history_messages(history))
        messages.append({"role": "user", "content": question})

        for step in range(MAX_STEPS):
            resp, budget_reason = _run_budgeted_model_call(
                budget,
                lambda: track_call(
                    lambda: client.chat.completions.create(
                        model=model,
                        messages=messages,
                        tools=TOOLS,
                        tool_choice="auto",
                        temperature=0.3,
                        timeout=budget.remaining_timeout_s(),
                    ),
                    provider=provider_id,
                    model=model,
                    surface="chat",
                ),
            )
            if budget_reason:
                final_answer = _budget_exhausted_answer(budget_reason)
                break
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
                    result, dt = _execute_tool_once(
                        db, fn_name, args, seen_tool_calls, budget,
                    )
                    trace.append(_trace_step(step, fn_name, args, result, dt))
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
            wrap_up, budget_reason = _run_budgeted_model_call(
                budget,
                lambda: track_call(
                    lambda: client.chat.completions.create(
                        model=model,
                        messages=messages
                        + [
                            {
                                "role": "user",
                                "content": "工具步数已达上限，请基于上面已经读到的内容直接给出 markdown 答案，不要再调用工具。",
                            }
                        ],
                        temperature=0.3,
                        timeout=budget.remaining_timeout_s(),
                    ),
                    provider=provider_id,
                    model=model,
                    surface="chat",
                ),
            )
            final_answer = (
                _budget_exhausted_answer(budget_reason)
                if budget_reason
                else (wrap_up.choices[0].message.content or "").strip()
            )

    duration_ms = int((perf_counter() - started) * 1000)
    return _finish_result(
        answer=final_answer,
        question=question,
        trace=trace,
        model=model,
        duration_ms=duration_ms,
        budget=budget,
    )
