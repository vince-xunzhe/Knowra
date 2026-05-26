"""Per-LLM-call telemetry for the model gateway.

Single chokepoint that:
  - tracks the "current logical task" via a context variable so call sites
    can pass a task name without plumbing it through every signature;
  - wraps a callable that performs the actual API call, capturing latency,
    token usage and exception class;
  - writes a `LLMCall` row asynchronously in a background thread so the
    real LLM call never waits on the DB.

Failures inside telemetry are intentionally swallowed: an observability
bug must never take down a real LLM call. The only guaranteed effect of
`track_call(...)` from the caller's perspective is that the wrapped
callable runs exactly once.

Usage from a service module:

    from model_gateway.telemetry import task_context, track_call

    with task_context("paper_extract"):
        response = track_call(
            lambda: client.chat.completions.create(...),
            provider="openai",
            model="gpt-4o",
            surface="chat",
        )
"""
from __future__ import annotations

import contextvars
import logging
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Iterator, Optional

logger = logging.getLogger(__name__)

_current_task: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "llm_task", default=None
)


@contextmanager
def task_context(task: str) -> Iterator[None]:
    """Tag every LLM call inside this block with `task`. Nestable; the
    innermost block wins."""
    token = _current_task.set(task)
    try:
        yield
    finally:
        _current_task.reset(token)


def current_task() -> str:
    return _current_task.get() or "unknown"


def _extract_usage(result: Any) -> tuple[Optional[int], Optional[int], Optional[int]]:
    """Pull (prompt, completion, total) tokens from an OpenAI SDK response.
    Tolerates the different shapes between chat.completions / responses /
    embeddings. Returns all-None if no usage data is present."""
    usage = getattr(result, "usage", None)
    if usage is None:
        return None, None, None
    # chat.completions and responses both expose .prompt_tokens /
    # .completion_tokens / .total_tokens but under different attribute
    # names depending on the SDK minor version. Try the common ones.
    prompt = (
        getattr(usage, "prompt_tokens", None)
        or getattr(usage, "input_tokens", None)
    )
    completion = (
        getattr(usage, "completion_tokens", None)
        or getattr(usage, "output_tokens", None)
    )
    total = getattr(usage, "total_tokens", None)
    if total is None and (prompt is not None or completion is not None):
        total = (prompt or 0) + (completion or 0)
    return prompt, completion, total


def _write_row_async(payload: dict[str, Any]) -> None:
    """Spawn a daemon thread that writes one row and exits. Errors are
    swallowed because telemetry must not break the calling request."""

    def _do() -> None:
        try:
            # Imported lazily so a misconfigured DB or missing models
            # module can't crash module import.
            from database import SessionLocal  # type: ignore
            from models import LLMCall  # type: ignore

            db = SessionLocal()
            try:
                row = LLMCall(**payload)
                db.add(row)
                db.commit()
            finally:
                db.close()
        except Exception:  # noqa: BLE001
            # We're already a fire-and-forget thread; just log + move on.
            logger.debug("telemetry write failed", exc_info=True)

    threading.Thread(target=_do, daemon=True).start()


def log_call(
    *,
    task: Optional[str],
    provider: str,
    model: str,
    surface: Optional[str] = None,
    prompt_tokens: Optional[int] = None,
    completion_tokens: Optional[int] = None,
    total_tokens: Optional[int] = None,
    latency_ms: Optional[int] = None,
    success: bool = True,
    error_class: Optional[str] = None,
) -> None:
    """Record a single LLM call. Best-effort; never raises."""
    try:
        _write_row_async(
            dict(
                called_at=datetime.now(timezone.utc),
                task=task or "unknown",
                provider=provider or "unknown",
                model=model or "unknown",
                surface=surface,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                latency_ms=latency_ms,
                success=success,
                error_class=error_class,
            )
        )
    except Exception:  # noqa: BLE001
        logger.debug("telemetry log_call dispatch failed", exc_info=True)


def track_call(
    fn: Callable[[], Any],
    *,
    provider: str,
    model: str,
    surface: Optional[str] = None,
) -> Any:
    """Run `fn()` and log its outcome. The wrapped exception (if any) is
    re-raised verbatim — the only side effect is that one telemetry row
    is appended after `fn` returns or raises."""
    started = time.monotonic()
    task = current_task()
    try:
        result = fn()
    except Exception as exc:  # noqa: BLE001
        latency_ms = int((time.monotonic() - started) * 1000)
        log_call(
            task=task,
            provider=provider,
            model=model,
            surface=surface,
            latency_ms=latency_ms,
            success=False,
            error_class=type(exc).__name__,
        )
        raise
    else:
        latency_ms = int((time.monotonic() - started) * 1000)
        prompt, completion, total = _extract_usage(result)
        log_call(
            task=task,
            provider=provider,
            model=model,
            surface=surface,
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=total,
            latency_ms=latency_ms,
            success=True,
        )
        return result


def log_codex_cli_call(
    *,
    provider: str,
    model: str,
    started_at: float,
    success: bool,
    error_class: Optional[str] = None,
) -> None:
    """Codex CLI does not expose a usage object. Log a row with the model
    name + latency + success bit only so the dashboard can still count
    invocations / failure rate even for the CLI provider."""
    latency_ms = int((time.monotonic() - started_at) * 1000)
    log_call(
        task=current_task(),
        provider=provider,
        model=model,
        surface="codex_cli",
        latency_ms=latency_ms,
        success=success,
        error_class=error_class,
    )
