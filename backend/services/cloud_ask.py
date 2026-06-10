"""Cloud-mode Ask handler — answers questions on top of a user's wiki.

Design contract (see docs/SYNC-PROTOCOL.md §4):

  1. ``openai_api_key`` is request-scoped only. It never lands in a DB
     row, log line, or response body. After the OpenAI call returns
     it's eligible for GC.
  2. cloud_llm_calls captures task / model / tokens / latency / success
     only. ``prompt`` / ``completion`` content is intentionally
     dropped.
  3. Rate limit is per-user, in-process (60 calls / 5 minutes for v1).
     Production should swap for Redis when we go horizontal.
  4. The "agent" is intentionally lightweight in v1: title-LIKE search
     across this user's wiki_files, fetch top-K files from Storage,
     pack into a single prompt, single LLM call, parse [[paper:UUID]]
     / [[concept:UUID]] markers out of the answer for citations. No
     tool loop — that's W7 work once we have the desktop's ask_agent
     more abstracted.

Failure modes that hit real users:
  - ``OpenAIRateLimitError`` → propagated as 402 / 502 by the router
  - rate limit exceeded → 429 (no LLM call attempted)
  - empty wiki → still answer, just without citations
"""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Optional, Protocol

from sqlalchemy import func
from sqlalchemy.orm import Session

from cloud_models import CloudKnowledgeNode, CloudPaper, CloudLLMCall, WikiFile
from schemas.cloud import AskCitation, AskHistoryTurn, AskTokens, AskTraceStep
from services.storage import ObjectStorage, ascii_storage_key, get_storage

log = logging.getLogger(__name__)

# ── tunables ──────────────────────────────────────────────────────────

# Lightweight per-user rate limit. Process-local; resets on restart.
# Production should move to Redis when we scale beyond one node.
RATE_LIMIT_CALLS = 60
RATE_LIMIT_WINDOW_SECONDS = 5 * 60

# How many wiki files to pull into the prompt at most.
MAX_CONTEXT_FILES = 5
# How many chars per file to include (rough budget so total prompt
# stays under ~6K tokens with 5 files).
MAX_CONTEXT_CHARS_PER_FILE = 3000

DEFAULT_MODEL = "gpt-4o-mini"

SYSTEM_PROMPT = """\
你是一个研究助理。下面会给你若干篇论文 / 概念的 wiki 内容，请基于这些内容回答用户的问题。

规则：
- 优先综合给定资料作答；即使资料只是部分相关，也要尽量提炼出有用信息，并说明它来自哪几篇。
- 只有在给定资料完全没有任何相关信息时，才说"基于已有资料无法判断"，并简要建议用户换个问法或同步更多内容。
- 引用相关概念或论文时，使用 [[concept:UUID]] 或 [[paper:UUID]] 标记。
- 答案用中文，markdown 格式，不要超过 600 字。
- 不要编造资料中没有的事实，但可以基于资料做合理的归纳与解释。\
"""

# Citation markers we recognize in answers — same convention as desktop.
_CITATION_RE = re.compile(
    r"\[\[(paper|concept):([A-Za-z0-9\-_]{6,})\]\]"
)


# ── error types ────────────────────────────────────────────────────────


class AskError(Exception):
    error_code: str = "internal_error"
    http_status: int = 500


class RateLimited(AskError):
    error_code = "rate_limited"
    http_status = 429

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__(f"rate limit exceeded; retry in {retry_after_seconds}s")
        self.retry_after_seconds = retry_after_seconds


class UpstreamFailure(AskError):
    error_code = "upstream_error"
    http_status = 502


# ── rate limiter ───────────────────────────────────────────────────────


class _UserRateLimiter:
    """In-process per-user sliding window. Thread-safe."""

    def __init__(self, *, max_calls: int, window_seconds: int) -> None:
        self._max = max_calls
        self._window = window_seconds
        self._buckets: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check_and_record(self, user_id: str) -> None:
        now = time.monotonic()
        cutoff = now - self._window
        with self._lock:
            bucket = self._buckets.setdefault(user_id, deque())
            # Drop timestamps outside the window.
            while bucket and bucket[0] < cutoff:
                bucket.popleft()
            if len(bucket) >= self._max:
                # How long until the oldest entry rolls out?
                retry_after = max(1, int(bucket[0] + self._window - now))
                raise RateLimited(retry_after)
            bucket.append(now)


_RATE_LIMITER = _UserRateLimiter(
    max_calls=RATE_LIMIT_CALLS,
    window_seconds=RATE_LIMIT_WINDOW_SECONDS,
)


# ── OpenAI call (injectable for tests) ─────────────────────────────────


@dataclass(frozen=True)
class LLMResult:
    answer: str
    model: str
    prompt_tokens: int
    completion_tokens: int


class LLMCaller(Protocol):
    """Stand-in for the OpenAI chat-completions client. Tests replace
    this with a deterministic fake so no live HTTP happens."""

    def __call__(
        self,
        *,
        api_key: str,
        model: str,
        messages: list[dict],
        reasoning_effort: Optional[str],
    ) -> LLMResult:
        ...


def _real_llm_call(
    *,
    api_key: str,
    model: str,
    messages: list[dict],
    reasoning_effort: Optional[str],
) -> LLMResult:
    """Default LLMCaller — talks to OpenAI via the installed SDK.

    ⚠️ This function is the ONLY place ``api_key`` is allowed to land
    in a stack frame outside the request lifecycle. Keep it minimal."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    kwargs: dict = {
        "model": model,
        "messages": messages,
        "temperature": 0.3,
    }
    if reasoning_effort in {"low", "medium", "high"}:
        # gpt-5 family supports reasoning_effort; gpt-4o ignores it.
        kwargs["reasoning_effort"] = reasoning_effort
    response = client.chat.completions.create(**kwargs)
    usage = getattr(response, "usage", None)
    return LLMResult(
        answer=(response.choices[0].message.content or "").strip(),
        model=response.model or model,
        prompt_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
        completion_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
    )


# ── retrieval ──────────────────────────────────────────────────────────


_CJK_RE = re.compile(r"[一-鿿㐀-䶿]+")
_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9]{2,}")
# Chinese stop-ish fragments that match almost everything → drop so they
# don't pull in irrelevant context. Not exhaustive, just the worst
# offenders for substring search.
_CJK_STOP = {"什么", "怎么", "如何", "为什么", "哪些", "介绍", "讲讲", "一下", "这个", "那个", "可以", "我们", "他们"}


def _tokenize_query(question: str) -> list[str]:
    """Tokenize a question for substring retrieval, CJK-aware.

    English/number runs → whole words (>=2 chars). CJK runs → all
    adjacent 2-char bigrams (Chinese has no spaces, and a whole phrase
    rarely substring-matches; bigrams like "深度"/"估计" do). This is
    what lets a Chinese question actually hit the Chinese content in
    papers.raw_llm_response and knowledge_nodes.content.
    """
    q = (question or "").strip()
    if not q:
        return []
    tokens: list[str] = []
    for m in _ASCII_WORD_RE.finditer(q.lower()):
        tokens.append(m.group(0))
    for run in _CJK_RE.findall(q):
        if len(run) == 1:
            tokens.append(run)
        else:
            for i in range(len(run) - 1):
                bg = run[i:i + 2]
                if bg not in _CJK_STOP:
                    tokens.append(bg)
    # De-dup, preserve order, cap so a long question doesn't explode the
    # number of LIKE queries.
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out[:24]


def _find_relevant_wiki(
    db: Session, *, user_id: str, question: str, limit: int = MAX_CONTEXT_FILES,
) -> list[WikiFile]:
    """Retrieve the wiki files most likely to answer the question.

    The v1 implementation only matched wiki TITLES — fatal for Chinese
    questions, since titles are mostly English paper names. This version
    scores each wiki file by how many query tokens hit across THREE
    Chinese-searchable signals (all in Postgres, no Storage reads):

      1. wiki title           (English mostly)
      2. the linked paper's raw_llm_response (Chinese extraction)
      3. linked knowledge_nodes' title/content/tags (Chinese)

    Files are ranked by hit count; ties broken by recency. If nothing
    matches at all, we fall back to the most-recently-compiled files so
    the model always has SOME grounding rather than answering blind.
    """
    tokens = _tokenize_query(question)
    wiki_files: list[WikiFile] = (
        db.query(WikiFile).filter(WikiFile.user_id == user_id).all()
    )
    if not wiki_files:
        return []
    if not tokens:
        return _recent_wiki(wiki_files, limit)

    # Build lookups: paper_id → wiki file (only paper-kind wikis link a
    # paper). We score by matching tokens against the paper's Chinese
    # extraction text + the wiki title.
    papers = {
        str(p.id): p
        for p in db.query(CloudPaper).filter(CloudPaper.user_id == user_id).all()
    }
    nodes = (
        db.query(CloudKnowledgeNode)
        .filter(CloudKnowledgeNode.user_id == user_id)
        .all()
    )
    # paper_id → concatenated Chinese node text (title+content+tags) of
    # nodes that cite that paper, so concept-level vocabulary also helps
    # surface the paper's wiki page.
    node_text_by_paper: dict[str, str] = {}
    for n in nodes:
        spids = n.source_paper_ids if isinstance(n.source_paper_ids, list) else []
        blob = " ".join(filter(None, [
            n.title or "",
            (n.content or "")[:1500],
            " ".join(t for t in (n.tags or []) if isinstance(t, str)),
        ])).lower()
        for pid in spids:
            node_text_by_paper[str(pid)] = node_text_by_paper.get(str(pid), "") + " " + blob

    def score(wf: WikiFile) -> int:
        haystack = (wf.title or "").lower()
        pid = str(wf.paper_id) if wf.paper_id else None
        if pid and pid in papers:
            haystack += " " + (papers[pid].raw_llm_response or "").lower()
        if pid and pid in node_text_by_paper:
            haystack += " " + node_text_by_paper[pid]
        return sum(1 for t in tokens if t in haystack)

    scored = [(score(wf), wf) for wf in wiki_files]
    hits = [(s, wf) for s, wf in scored if s > 0]
    if not hits:
        return _recent_wiki(wiki_files, limit)
    hits.sort(key=lambda sw: (sw[0], _compiled_sort_key(sw[1])), reverse=True)
    return [wf for _, wf in hits[:limit]]


def _compiled_sort_key(wf: WikiFile):
    ts = getattr(wf, "compiled_at", None) or getattr(wf, "updated_at", None)
    return ts.timestamp() if ts else 0.0


def _recent_wiki(wiki_files: list[WikiFile], limit: int) -> list[WikiFile]:
    """Fallback context: most-recently-compiled paper wikis. Better to
    answer from *something* than to always say '无法判断' when title
    search whiffs."""
    paper_wikis = [w for w in wiki_files if w.kind == "paper"] or wiki_files
    return sorted(paper_wikis, key=_compiled_sort_key, reverse=True)[:limit]


def _user_storage_path(user_id: str, rel_path: str) -> str:
    # Must match sync._storage_path EXACTLY (no "wiki/" prefix — the bucket
    # is passed separately by the storage client) and apply the same ASCII
    # key transform, otherwise read_bytes() looks up the wrong object and
    # Ask silently gets no wiki context. The earlier "wiki/" prefix here was
    # a latent bug (double-bucket path).
    return f"{user_id}/{ascii_storage_key(rel_path)}"


def _build_messages(
    *,
    question: str,
    history: list[AskHistoryTurn],
    wiki_files: list[WikiFile],
    storage: ObjectStorage,
    user_id: str,
) -> tuple[list[dict], list[AskCitation]]:
    """Construct OpenAI chat messages plus a citation candidate list.

    Citations returned here are "possible references" derived from
    fetched wiki files — the answer parser will keep only the ones
    actually mentioned in the LLM's reply."""
    context_blocks: list[str] = []
    candidate_citations: list[AskCitation] = []
    for wf in wiki_files:
        body = storage.read_bytes(_user_storage_path(user_id, wf.rel_path))
        if not body:
            continue
        text = body.decode("utf-8", errors="ignore")[:MAX_CONTEXT_CHARS_PER_FILE]
        # Choose a citation ref based on kind. If we know paper_id /
        # concept_id we use those; otherwise fall back to the file UUID.
        if wf.kind == "paper" and wf.paper_id:
            ref = f"[[paper:{wf.paper_id}]]"
            kind = "paper"
        elif wf.kind in {"concept", "technique", "dataset"} and wf.concept_id:
            ref = f"[[concept:{wf.concept_id}]]"
            kind = "concept"
        else:
            ref = f"[[{wf.kind}:{wf.id}]]"
            kind = wf.kind
        context_blocks.append(
            f"### {wf.title or wf.rel_path}  {ref}\n{text}"
        )
        candidate_citations.append(AskCitation(
            kind=kind,
            ref=ref,
            file_id=wf.id,
            rel_path=wf.rel_path,
            title=wf.title,
        ))

    context_section = "\n\n---\n\n".join(context_blocks) if context_blocks else "（暂无相关 wiki 内容）"
    user_content = f"问题：{question}\n\n参考资料：\n\n{context_section}"

    messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in history[-10:]:
        if turn.role in {"user", "assistant"}:
            messages.append({"role": turn.role, "content": turn.content})
    messages.append({"role": "user", "content": user_content})
    return messages, candidate_citations


def _parse_citations(
    answer: str, candidates: list[AskCitation]
) -> list[AskCitation]:
    """Keep only the citations whose ref appears in the answer body."""
    refs_in_answer = {f"[[{m.group(1)}:{m.group(2)}]]" for m in _CITATION_RE.finditer(answer)}
    out: list[AskCitation] = []
    seen: set[str] = set()
    for c in candidates:
        if c.ref in refs_in_answer and c.ref not in seen:
            out.append(c)
            seen.add(c.ref)
    # Catch references the LLM made up that don't map to a fetched
    # candidate — we still surface them so the mobile UI can warn the
    # user about "phantom" links.
    for ref in refs_in_answer:
        if ref in seen:
            continue
        m = _CITATION_RE.match(ref)
        if not m:
            continue
        out.append(AskCitation(kind=m.group(1), ref=ref))
        seen.add(ref)
    return out


# ── telemetry ──────────────────────────────────────────────────────────


def _record_telemetry(
    db: Session,
    *,
    user_id: str,
    task: str,
    model: str,
    tokens: AskTokens,
    latency_ms: int,
    success: bool,
    error_class: Optional[str] = None,
) -> None:
    """Write a cloud_llm_calls row. Best-effort — never raises."""
    try:
        db.add(CloudLLMCall(
            user_id=user_id,
            task=task,
            provider="openai",
            model=model,
            prompt_tokens=tokens.prompt or None,
            completion_tokens=tokens.completion or None,
            total_tokens=tokens.total or None,
            latency_ms=latency_ms,
            success=success,
            error_class=error_class,
        ))
        db.commit()
    except Exception:  # noqa: BLE001
        log.warning("cloud_llm_calls write failed", exc_info=True)


# ── orchestrator ──────────────────────────────────────────────────────


def run_cloud_ask(
    db: Session,
    *,
    user_id: str,
    question: str,
    openai_api_key: str,
    model: Optional[str],
    history: list[AskHistoryTurn],
    reasoning_effort: Optional[str],
    llm_caller: Optional[LLMCaller] = None,
) -> dict:
    """Execute the ask flow end-to-end.

    Args:
      db, user_id: as resolved from the JWT in the router.
      question: validated by Pydantic.
      openai_api_key: request-scoped; not stored.
      llm_caller: optional override for testing; defaults to the real
        OpenAI client.
    """
    _RATE_LIMITER.check_and_record(user_id)
    started = time.monotonic()
    chosen_model = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    storage = get_storage()
    caller = llm_caller or _real_llm_call

    trace: list[AskTraceStep] = []

    # Step 1 — retrieval
    step_t = time.monotonic()
    wiki_hits = _find_relevant_wiki(db, user_id=user_id, question=question)
    trace.append(AskTraceStep(
        step=0, name="search",
        summary=f"matched {len(wiki_hits)} wiki entries by title",
        duration_ms=int((time.monotonic() - step_t) * 1000),
    ))

    # Step 2 — synthesize
    step_t = time.monotonic()
    messages, candidate_citations = _build_messages(
        question=question, history=history,
        wiki_files=wiki_hits, storage=storage, user_id=user_id,
    )
    try:
        result = caller(
            api_key=openai_api_key,
            model=chosen_model,
            messages=messages,
            reasoning_effort=reasoning_effort,
        )
    except Exception as exc:  # noqa: BLE001
        latency_ms = int((time.monotonic() - started) * 1000)
        _record_telemetry(
            db,
            user_id=user_id,
            task="ask_mobile",
            model=chosen_model,
            tokens=AskTokens(),
            latency_ms=latency_ms,
            success=False,
            error_class=type(exc).__name__,
        )
        raise UpstreamFailure(str(exc)) from exc

    tokens = AskTokens(
        prompt=result.prompt_tokens,
        completion=result.completion_tokens,
        total=result.prompt_tokens + result.completion_tokens,
    )
    trace.append(AskTraceStep(
        step=1, name="synthesize",
        summary=f"openai · {result.model} · {tokens.total} tokens",
        duration_ms=int((time.monotonic() - step_t) * 1000),
    ))

    citations = _parse_citations(result.answer, candidate_citations)

    latency_ms = int((time.monotonic() - started) * 1000)
    _record_telemetry(
        db,
        user_id=user_id,
        task="ask_mobile",
        model=result.model,
        tokens=tokens,
        latency_ms=latency_ms,
        success=True,
    )

    return {
        "answer": result.answer,
        "citations": citations,
        "trace": trace,
        "tokens": tokens,
        "model": result.model,
    }
