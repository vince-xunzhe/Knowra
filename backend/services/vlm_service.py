"""Paper extraction via OpenAI file_search.

Compatibility strategy:
- Older supported models keep using Assistants API + file_search.
- Newer GPT-5.4/5.5 models use Responses API + file_search because they are
  not available in the Assistants API.
"""
import json
import math
import re
import sys
import time
from pathlib import Path
from typing import Optional

from openai import OpenAI
from openai import NotFoundError, APIStatusError


class PaperExtractionError(RuntimeError):
    """Raised when extraction fails. Carries the raw response so the caller
    can persist it for post-mortem inspection via the Review page."""

    def __init__(
        self,
        message: str,
        raw: str = "",
        file_id: str = "",
        assistant_id: str = "",
    ):
        super().__init__(message)
        self.raw = raw
        self.file_id = file_id
        self.assistant_id = assistant_id


def _log(msg: str) -> None:
    print(f"[vlm] {msg}", file=sys.stderr, flush=True)


# Minimal, stable instructions for the assistant. The real extraction prompt
# travels in each user message, so editing it does NOT require rebuilding the
# assistant.
ASSISTANT_INSTRUCTIONS = (
    "你是一位学术论文分析助手。每次对话里用户会附加一篇论文的 PDF。"
    "请使用 file_search 工具阅读 PDF 全文，严格按用户消息中的要求返回 JSON。"
    "不要输出 JSON 以外的文字，不要 markdown 代码块围栏，不要 file_search 引用标记。"
)

ASSISTANT_NAME = "knowledge-tree-paper-extractor"
RESPONSES_ONLY_MODELS = {"gpt-5.5", "gpt-5.4", "gpt-5.4-mini"}


def model_uses_responses_api(model: str) -> bool:
    return model in RESPONSES_ONLY_MODELS


# --- assistant lifecycle -----------------------------------------------------

def _ensure_assistant(
    client: OpenAI,
    cached_assistant_id: Optional[str],
    model: str,
) -> str:
    """Return a usable assistant id. Reuses cached one if model matches;
    otherwise creates a fresh assistant."""
    if cached_assistant_id:
        try:
            existing = client.beta.assistants.retrieve(cached_assistant_id)
            if existing.model == model:
                return existing.id
            # Model changed — delete old and recreate so cache stays clean.
            try:
                client.beta.assistants.delete(cached_assistant_id)
            except Exception:
                pass
        except NotFoundError:
            pass
        except APIStatusError:
            pass

    created = client.beta.assistants.create(
        name=ASSISTANT_NAME,
        instructions=ASSISTANT_INSTRUCTIONS,
        model=model,
        tools=[{"type": "file_search"}],
    )
    return created.id


# --- file upload / reuse -----------------------------------------------------

def _ensure_file(
    client: OpenAI,
    cached_file_id: Optional[str],
    pdf_filepath: str,
) -> str:
    """Return a usable OpenAI file id for this PDF. Uploads if missing or the
    cached id has been deleted on the server."""
    if cached_file_id:
        try:
            client.files.retrieve(cached_file_id)
            return cached_file_id
        except NotFoundError:
            pass
        except APIStatusError:
            pass

    if not Path(pdf_filepath).exists():
        raise FileNotFoundError(f"PDF not found: {pdf_filepath}")

    with open(pdf_filepath, "rb") as f:
        uploaded = client.files.create(file=f, purpose="assistants")
    return uploaded.id


def _ensure_vector_store(
    client: OpenAI,
    cached_vector_store_id: Optional[str],
    file_id: str,
    timeout_s: int = 300,
) -> str:
    if cached_vector_store_id:
        try:
            existing = client.vector_stores.retrieve(cached_vector_store_id)
            file_counts = getattr(existing, "file_counts", None)
            in_progress = getattr(file_counts, "in_progress", 0) if file_counts else 0
            if in_progress == 0:
                return existing.id
        except Exception:
            pass

    store = client.vector_stores.create(file_ids=[file_id])
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        current = client.vector_stores.retrieve(store.id)
        file_counts = getattr(current, "file_counts", None)
        in_progress = getattr(file_counts, "in_progress", 0) if file_counts else 0
        failed = getattr(file_counts, "failed", 0) if file_counts else 0
        completed = getattr(file_counts, "completed", 0) if file_counts else 0
        if in_progress == 0:
            if failed and completed == 0:
                raise RuntimeError("Vector store file processing failed")
            return current.id
        time.sleep(1.5)
    raise RuntimeError("Timed out waiting for vector store readiness")


# --- run & collect -----------------------------------------------------------

def _extract_assistant_text(messages) -> str:
    """Pull the latest assistant message's text out of a thread list.

    Handles `text` and `refusal` content block variants. Returns "" if the
    assistant produced no textual output (rare but possible when the model
    bails to only tool calls).
    """
    for msg in messages.data:
        if msg.role != "assistant":
            continue
        parts: list[str] = []
        for block in msg.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                value = getattr(block.text, "value", "") if hasattr(block, "text") else ""
                if value:
                    parts.append(value)
            elif btype == "refusal":
                refusal = getattr(block, "refusal", "")
                if refusal:
                    parts.append(f"[REFUSAL] {refusal}")
        if parts:
            return "\n".join(parts)
    return ""


def _run_and_collect(
    client: OpenAI,
    assistant_id: str,
    file_id: str,
    prompt: str,
    timeout_s: int = 300,
) -> tuple[str, str]:
    """Create a thread, attach the PDF, run the assistant, and return
    `(raw_response_text, thread_id)`. The thread is kept alive so the
    caller can reuse it for follow-up chat turns; it will expire on
    OpenAI's side after ~60 days."""
    thread = client.beta.threads.create()
    _log(f"thread={thread.id} assistant={assistant_id} file={file_id} — running")
    client.beta.threads.messages.create(
        thread_id=thread.id,
        role="user",
        content=prompt,
        attachments=[
            {"file_id": file_id, "tools": [{"type": "file_search"}]},
        ],
    )
    run = client.beta.threads.runs.create_and_poll(
        thread_id=thread.id,
        assistant_id=assistant_id,
        poll_interval_ms=1500,
        response_format={"type": "json_object"},
    )
    # Edge case: create_and_poll returns when terminal, but guard anyway.
    deadline = time.time() + timeout_s
    while run.status in ("queued", "in_progress") and time.time() < deadline:
        time.sleep(2)
        run = client.beta.threads.runs.retrieve(
            thread_id=thread.id, run_id=run.id
        )

    _log(f"thread={thread.id} run={run.id} status={run.status}")
    if run.status != "completed":
        last_err = getattr(run, "last_error", None)
        detail = f" ({last_err.message})" if last_err else ""
        raise RuntimeError(f"Assistant run did not complete: {run.status}{detail}")

    messages = client.beta.threads.messages.list(
        thread_id=thread.id, order="desc", limit=10
    )
    raw = _extract_assistant_text(messages)
    preview = (raw[:160] + "…") if len(raw) > 160 else raw or "<empty>"
    _log(f"thread={thread.id} raw_len={len(raw)} preview={preview!r}")
    return raw, thread.id


def _run_with_responses_file_search(
    client: OpenAI,
    model: str,
    vector_store_id: str,
    prompt: str,
) -> tuple[str, str]:
    response = client.responses.create(
        model=model,
        instructions=ASSISTANT_INSTRUCTIONS,
        input=prompt,
        tools=[{"type": "file_search", "vector_store_ids": [vector_store_id]}],
        text={"format": {"type": "json_object"}},
    )
    raw = getattr(response, "output_text", "") or ""
    preview = (raw[:160] + "…") if len(raw) > 160 else raw or "<empty>"
    _log(f"responses id={response.id} raw_len={len(raw)} preview={preview!r}")
    return raw, response.id


# --- response cleaning -------------------------------------------------------

# file_search citation markers: 【4:0†source】, 【12:3†paper.pdf】, etc.
_CITATION_RE = re.compile(r"【[^】]*?†[^】]*?】")


def _strip_citations(s: str) -> str:
    return _CITATION_RE.sub("", s)


def _parse_json_lenient(s: str) -> dict:
    s = _strip_citations(s).strip()
    # Strip markdown code fence if present
    if s.startswith("```"):
        lines = s.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines)
    try:
        return json.loads(s)
    except Exception:
        i, j = s.find("{"), s.rfind("}")
        if i != -1 and j != -1 and j > i:
            return json.loads(s[i : j + 1])
        raise


# --- extraction schema normalization ---------------------------------------
#
# Real-world observation: even with an explicit English-key schema, the model
# sometimes returns Chinese keys ("核心贡献"), half-translated variants
# ("principle_explanation", "background_position", "pytorch_code"), nests the
# whole thing under a "报告" wrapper, or returns code as a string array.
# The normalizer below canonicalizes whatever the model produced into the
# shape the downstream graph + UI expect. Prompt + normalizer are defense in
# depth — either alone would still leave paper reviews looking empty.

_TOP_KEY_MAP = {
    # identity
    "标题": "title", "title": "title",
    "作者": "authors", "authors": "authors",
    "会议": "venue", "发表": "venue", "venue": "venue",
    "年份": "year", "year": "year",
    # classification
    "研究领域": "problem_area", "problem_area": "problem_area",
    "大模型技术栈位置": "tech_stack_position",
    "技术栈位置": "tech_stack_position",
    "tech_stack_position": "tech_stack_position",
    "关键词": "keywords", "keywords": "keywords",
    # narrative
    "核心贡献": "core_contribution", "core_contribution": "core_contribution",
    "摘要": "abstract_summary", "abstract_summary": "abstract_summary", "abstract": "abstract_summary",
    "研究问题": "problem", "problem": "problem",
    "研究动机": "motivation", "motivation": "motivation",
    "原理解析": "principle", "核心原理": "principle",
    "principle": "principle", "principle_explanation": "principle",
    "关键创新点": "innovations", "创新点": "innovations",
    "innovations": "innovations", "key_innovations": "innovations",
    "关键结论": "experimental_gains", "实验效果": "experimental_gains",
    "experimental_gains": "experimental_gains", "key_results": "experimental_gains",
    "key_conclusions": "experimental_gains",
    "背景地位": "historical_position", "historical_position": "historical_position",
    "background_position": "historical_position", "background_status": "historical_position",
    "background": "historical_position",
    "局限性": "limitations", "这里的坑": "limitations", "limitations": "limitations",
    "pitfalls": "limitations", "drawbacks": "limitations",
    "pytorch实现": "pytorch_snippet", "pytorch代码": "pytorch_snippet",
    "代码": "pytorch_snippet", "pytorch_snippet": "pytorch_snippet",
    "pytorch_code": "pytorch_snippet", "torch_code": "pytorch_snippet",
    "code_snippet": "pytorch_snippet", "implementation": "pytorch_snippet",
    # graph
    "技术方法": "techniques", "技术": "techniques", "techniques": "techniques",
    "数据集": "datasets", "datasets": "datasets",
    "对比基线": "baselines", "基线": "baselines", "baselines": "baselines",
    "贡献": "contributions", "contributions": "contributions",
    "关键发现": "key_findings", "key_findings": "key_findings", "findings": "key_findings",
}

_FLATTEN_WRAPPERS = {
    "报告", "report", "论文身份卡", "身份卡",
    "identity_card", "paper_identity_card", "paper_identity", "identity",
}

_PRINCIPLE_SUB = {
    "比喻": "analogy", "通俗比喻": "analogy", "analogy": "analogy",
    "数据流": "architecture_flow", "数据流动": "architecture_flow",
    "架构流程": "architecture_flow", "架构流动": "architecture_flow",
    "architecture_flow": "architecture_flow",
    "关键公式": "key_formulas", "key_formulas": "key_formulas",
}

_FORMULA_SUB = {
    "名称": "name", "名字": "name", "title": "name", "name": "name",
    "公式": "formula", "公式内容": "formula", "latex": "formula",
    "equation": "formula", "expression": "formula", "formula": "formula",
    "解释": "plain", "白话": "plain", "说明": "plain",
    "plain": "plain", "meaning": "plain",
}

_INNOVATIONS_SUB = {
    "以前": "previous_work", "之前": "previous_work",
    "以前是怎么做的": "previous_work",
    "以前的做法": "previous_work", "previous": "previous_work",
    "previous_work": "previous_work", "prior_work": "previous_work",
    "previous_method": "previous_work", "previous_approach": "previous_work",
    "before": "previous_work",
    "这篇论文": "this_work", "这篇论文怎么做": "this_work",
    "这篇论文是怎么做的": "this_work", "本文": "this_work", "本文做法": "this_work",
    "this_work": "this_work", "this": "this_work", "current": "this_work",
    "current_method": "this_work", "current_approach": "this_work",
    "new_method": "this_work", "proposed_method": "this_work",
    "为什么更好": "why_better", "为什么现在的更好": "why_better",
    "改进": "why_better", "优势": "why_better",
    "why_better": "why_better", "why": "why_better", "improvement": "why_better",
    "why_is_now_better": "why_better", "why_is_better": "why_better",
    "advantage": "why_better", "advantages": "why_better",
}

_POSITION_SUB = {
    "站在谁的肩上": "builds_on", "基于": "builds_on", "继承": "builds_on",
    "builds_on": "builds_on",
    "启发了谁": "inspired", "启发": "inspired", "inspired": "inspired",
    "综合评价": "overall", "总体评价": "overall", "总评": "overall",
    "overall": "overall",
}

_PYTORCH_SUB = {
    "模块名": "module_name", "module_name": "module_name",
    "代码": "code", "code": "code",
    "注释": "notes", "笔记": "notes", "说明": "notes", "notes": "notes",
}


def _strip_code_fence(s: str) -> str:
    if not isinstance(s, str):
        return s
    s = s.strip()
    if s.startswith("```"):
        lines = s.split("\n")
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines)
    return s


def _coerce_code(v):
    """pytorch code may arrive as str, list[str], or dict. Return a string."""
    if isinstance(v, list):
        return _strip_code_fence("\n".join(str(x) for x in v))
    if isinstance(v, str):
        return _strip_code_fence(v)
    return ""


def _remap_keys(d, mapping):
    if not isinstance(d, dict):
        return d
    return {mapping.get(str(k).strip().lower(), k): v for k, v in d.items()}


def _normalize_extraction(raw) -> dict:
    """Canonicalize the model's JSON into the English-keyed schema."""
    if not isinstance(raw, dict):
        return {}

    # Step 1: flatten wrappers like "报告" / "论文身份卡" into the parent dict
    # before remapping top-level keys.
    flattened: dict = {}
    for k, v in raw.items():
        key_l = str(k).strip().lower()
        if key_l in _FLATTEN_WRAPPERS and isinstance(v, dict):
            for sk, sv in v.items():
                flattened.setdefault(sk, sv)
            continue
        flattened[k] = v

    # Step 2: remap top-level keys to the canonical English schema.
    out = {_TOP_KEY_MAP.get(str(k).strip().lower(), k): v for k, v in flattened.items()}

    # Step 3: coerce nested blocks that the model sometimes returns as plain
    # strings, and remap their sub-keys.
    if "principle" in out:
        p = out["principle"]
        if isinstance(p, str):
            out["principle"] = {"analogy": p}
        elif isinstance(p, dict):
            p = _remap_keys(p, _PRINCIPLE_SUB)
            formulas = p.get("key_formulas")
            if isinstance(formulas, list):
                normalized_formulas = []
                for item in formulas:
                    if isinstance(item, str):
                        normalized_formulas.append({"name": item, "formula": "", "plain": ""})
                    elif isinstance(item, dict):
                        normalized_formulas.append(_remap_keys(item, _FORMULA_SUB))
                p["key_formulas"] = normalized_formulas
            out["principle"] = p

    if "innovations" in out and isinstance(out["innovations"], dict):
        out["innovations"] = _remap_keys(out["innovations"], _INNOVATIONS_SUB)

    if "historical_position" in out:
        hp = out["historical_position"]
        if isinstance(hp, str):
            out["historical_position"] = {"overall": hp}
        elif isinstance(hp, dict):
            out["historical_position"] = _remap_keys(hp, _POSITION_SUB)

    # pytorch_snippet: accept string, list[str], or dict (possibly with sub-keys
    # in Chinese). Always end up with {module_name?, code, notes?}.
    if "pytorch_snippet" in out:
        ps = out["pytorch_snippet"]
        if isinstance(ps, str) or isinstance(ps, list):
            out["pytorch_snippet"] = {"code": _coerce_code(ps)}
        elif isinstance(ps, dict):
            ps = _remap_keys(ps, _PYTORCH_SUB)
            if "code" in ps:
                ps["code"] = _coerce_code(ps["code"])
            out["pytorch_snippet"] = ps

    return out


def parse_extraction_response(s: str) -> dict:
    """Parse + normalize a model response.

    Runs the lenient JSON parser, then canonicalizes keys so downstream code
    (graph builder + frontend renderer) can rely on the English schema
    regardless of how the model spelled its keys this time.
    """
    return _normalize_extraction(_parse_json_lenient(s))


# --- public entry point ------------------------------------------------------

def extract_knowledge_from_paper(
    pdf_filepath: str,
    prompt: str,
    api_key: str,
    model: str,
    cached_file_id: Optional[str] = None,
    cached_assistant_id: Optional[str] = None,
    cached_vector_store_id: Optional[str] = None,
) -> tuple[dict, str, str, str, str, str]:
    """Run structured extraction on a single PDF via Assistants + file_search.

    Returns (parsed_json, raw_response, file_id, assistant_id, thread_or_response_id, vector_store_id).
    Callers should persist file_id on the Paper row, assistant_id in config,
    and thread_id on the Paper row so follow-up chat can reuse the thread.
    """
    client = OpenAI(api_key=api_key)
    file_id = _ensure_file(client, cached_file_id, pdf_filepath)
    assistant_id = ""
    vector_store_id = cached_vector_store_id or ""

    raw = ""
    thread_id = ""
    try:
        if model_uses_responses_api(model):
            vector_store_id = _ensure_vector_store(
                client, cached_vector_store_id, file_id
            )
            raw, thread_id = _run_with_responses_file_search(
                client, model, vector_store_id, prompt
            )
        else:
            assistant_id = _ensure_assistant(client, cached_assistant_id, model)
            raw, thread_id = _run_and_collect(client, assistant_id, file_id, prompt)
        parsed = _normalize_extraction(_parse_json_lenient(raw))
    except PaperExtractionError:
        raise
    except Exception as e:
        preview = raw if raw else "<empty response>"
        if len(preview) > 200:
            preview = preview[:200] + "…"
        raise PaperExtractionError(
            f"解析失败: {e} | raw={preview!r}",
            raw=raw,
            file_id=file_id,
            assistant_id=assistant_id,
        ) from e

    return parsed, raw, file_id, assistant_id, thread_id, vector_store_id


# --- follow-up chat ----------------------------------------------------------

CHAT_INSTRUCTIONS = (
    "你是一位学术论文分析助手。当前对话线程里用户已附加了一篇论文的 PDF。"
    "请使用 file_search 工具阅读 PDF 全文，用中文自然地回答用户的追问。"
    "可以使用 Markdown 排版，但不要输出 file_search 引用标记（如【n:m†source】）。"
    "不要用 JSON 格式回答，正常讲人话即可。"
)


def _ensure_chat_thread(
    client: OpenAI,
    cached_thread_id: Optional[str],
    file_id: str,
) -> tuple[str, bool]:
    """Return (thread_id, is_new). If the cached thread is missing (expired
    or never existed), create a fresh one. Callers attach the PDF to the
    first user message only when is_new is True."""
    if cached_thread_id:
        try:
            client.beta.threads.retrieve(cached_thread_id)
            return cached_thread_id, False
        except NotFoundError:
            pass
        except APIStatusError:
            pass

    thread = client.beta.threads.create()
    return thread.id, True


def run_chat_turn(
    api_key: str,
    model: str,
    assistant_id: str,
    file_id: str,
    user_message: str,
    cached_vector_store_id: Optional[str] = None,
    cached_thread_id: Optional[str] = None,
    chat_history: Optional[list] = None,
    timeout_s: int = 300,
) -> tuple[str, str, bool, str]:
    """Send a user message on the paper's thread and return
    `(assistant_reply, thread_id, thread_was_recreated)`.

    Reuses the persisted thread so the assistant sees prior turns. Recreates
    the thread and re-attaches the PDF when the old one is gone.
    """
    client = OpenAI(api_key=api_key)

    if model_uses_responses_api(model):
        vector_store_id = _ensure_vector_store(client, cached_vector_store_id, file_id)
        create_kwargs = {
            "model": model,
            "instructions": CHAT_INSTRUCTIONS,
            "input": [{"role": "user", "content": user_message}],
            "tools": [{"type": "file_search", "vector_store_ids": [vector_store_id]}],
        }
        is_new = not bool(cached_thread_id)
        if cached_thread_id:
            create_kwargs["previous_response_id"] = cached_thread_id
            try:
                response = client.responses.create(**create_kwargs)
            except APIStatusError:
                history = []
                for item in chat_history or []:
                    if not isinstance(item, dict):
                        continue
                    role = str(item.get("role") or "").strip()
                    if role not in {"user", "assistant"}:
                        continue
                    history.append({"role": role, "content": str(item.get("content") or "")})
                history.append({"role": "user", "content": user_message})
                response = client.responses.create(
                    model=model,
                    instructions=CHAT_INSTRUCTIONS,
                    input=history,
                    tools=[{"type": "file_search", "vector_store_ids": [vector_store_id]}],
                )
                is_new = True
        else:
            history = []
            for item in chat_history or []:
                if not isinstance(item, dict):
                    continue
                role = str(item.get("role") or "").strip()
                if role not in {"user", "assistant"}:
                    continue
                history.append({"role": role, "content": str(item.get("content") or "")})
            history.append({"role": "user", "content": user_message})
            response = client.responses.create(
                model=model,
                instructions=CHAT_INSTRUCTIONS,
                input=history,
                tools=[{"type": "file_search", "vector_store_ids": [vector_store_id]}],
            )

        reply = _strip_citations((getattr(response, "output_text", "") or "")).strip()
        return reply, response.id, is_new, vector_store_id

    thread_id, is_new = _ensure_chat_thread(client, cached_thread_id, file_id)

    attachments = None
    if is_new:
        attachments = [{"file_id": file_id, "tools": [{"type": "file_search"}]}]

    client.beta.threads.messages.create(
        thread_id=thread_id,
        role="user",
        content=user_message,
        **({"attachments": attachments} if attachments else {}),
    )
    run = client.beta.threads.runs.create_and_poll(
        thread_id=thread_id,
        assistant_id=assistant_id,
        poll_interval_ms=1500,
        instructions=CHAT_INSTRUCTIONS,
    )
    deadline = time.time() + timeout_s
    while run.status in ("queued", "in_progress") and time.time() < deadline:
        time.sleep(2)
        run = client.beta.threads.runs.retrieve(thread_id=thread_id, run_id=run.id)

    _log(f"chat thread={thread_id} run={run.id} status={run.status}")
    if run.status != "completed":
        last_err = getattr(run, "last_error", None)
        detail = f" ({last_err.message})" if last_err else ""
        raise RuntimeError(f"Chat run did not complete: {run.status}{detail}")

    messages = client.beta.threads.messages.list(
        thread_id=thread_id, order="desc", limit=5
    )
    raw = _extract_assistant_text(messages)
    reply = _strip_citations(raw).strip()
    return reply, thread_id, is_new, cached_vector_store_id or ""


# --- embeddings (unchanged) --------------------------------------------------

def get_embedding(text: str, api_key: str, model: str = "text-embedding-3-small") -> list:
    client = OpenAI(api_key=api_key)
    response = client.embeddings.create(input=text, model=model)
    return response.data[0].embedding


def cosine_similarity(a: list, b: list) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
