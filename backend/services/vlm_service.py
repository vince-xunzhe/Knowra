"""Paper extraction via OpenAI Assistants API + file_search.

The PDF itself is uploaded to OpenAI (purpose="assistants"), attached to a
throwaway thread with the file_search tool, and the assistant returns a
structured JSON response built from its own layout-aware PDF pipeline.

This replaces the previous chat.completions path that stuffed pypdf-extracted
text + a first-page PNG into a multimodal user message.
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
) -> str:
    """Create a throwaway thread, attach the PDF, run the assistant, and
    return the raw assistant response text. Deletes the thread on exit."""
    thread = client.beta.threads.create()
    _log(f"thread={thread.id} assistant={assistant_id} file={file_id} — running")
    try:
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
        return raw
    finally:
        try:
            client.beta.threads.delete(thread.id)
        except Exception:
            pass


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


# --- public entry point ------------------------------------------------------

def extract_knowledge_from_paper(
    pdf_filepath: str,
    prompt: str,
    api_key: str,
    model: str,
    cached_file_id: Optional[str] = None,
    cached_assistant_id: Optional[str] = None,
) -> tuple[dict, str, str, str]:
    """Run structured extraction on a single PDF via Assistants + file_search.

    Returns (parsed_json, raw_response, file_id, assistant_id).
    Callers should persist file_id on the Paper row and assistant_id in config
    so subsequent runs reuse them.
    """
    client = OpenAI(api_key=api_key)

    assistant_id = _ensure_assistant(client, cached_assistant_id, model)
    file_id = _ensure_file(client, cached_file_id, pdf_filepath)

    raw = ""
    try:
        raw = _run_and_collect(client, assistant_id, file_id, prompt)
        parsed = _parse_json_lenient(raw)
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

    return parsed, raw, file_id, assistant_id


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
