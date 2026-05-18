from __future__ import annotations

from typing import Optional


PIPELINE_STATUS_SCANNING = "scanning"
PIPELINE_STATUS_EXTRACTING = "extracting"
PIPELINE_STATUS_PARSING = "parsing"
PIPELINE_STATUS_GRAPHING = "graphing"
PIPELINE_STATUS_FAILED = "failed"
PIPELINE_STATUS_DONE = "done"

PIPELINE_STATUSES = {
    PIPELINE_STATUS_SCANNING,
    PIPELINE_STATUS_EXTRACTING,
    PIPELINE_STATUS_PARSING,
    PIPELINE_STATUS_GRAPHING,
    PIPELINE_STATUS_FAILED,
    PIPELINE_STATUS_DONE,
}

_TEMPORARY_ERROR_KEYWORDS = (
    "timeout",
    "timed out",
    "connection reset",
    "connection aborted",
    "temporary",
    "temporarily",
    "try again",
    "rate limit",
    "too many requests",
    "service unavailable",
    "bad gateway",
    "gateway timeout",
    "overloaded",
    "network",
    "解析失败",
    "空响应",
)

_PERMANENT_ERROR_KEYWORDS = (
    "api key",
    "authentication",
    "unauthorized",
    "permission denied",
    "invalid_request_error",
    "not configured",
    "directory not found",
    "pdf not found",
    "file not found",
)


def normalize_processing_status(raw: Optional[str]) -> str:
    value = (raw or "").strip().lower()
    return value if value in PIPELINE_STATUSES else PIPELINE_STATUS_SCANNING


def compute_backoff_seconds(
    retry_index: int,
    *,
    base_seconds: float = 1.5,
    max_seconds: float = 20.0,
) -> float:
    """Exponential backoff for retries.

    `retry_index=1` means the first retry after the first failure.
    """
    if retry_index <= 0:
        return 0.0
    delay = base_seconds * (2 ** (retry_index - 1))
    return min(max_seconds, delay)


def short_error_reason(exc: Exception, *, max_len: int = 500) -> str:
    message = (str(exc) or exc.__class__.__name__).strip()
    if len(message) <= max_len:
        return message
    return message[: max_len - 1] + "…"


def _extract_status_code(exc: Exception) -> Optional[int]:
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            value = getattr(response, "status_code", None)
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            pass
    return None


def is_recoverable_error(exc: Exception) -> bool:
    if isinstance(exc, (FileNotFoundError, PermissionError)):
        return False

    status_code = _extract_status_code(exc)
    if status_code is not None:
        if status_code in {408, 409, 425, 429}:
            return True
        if status_code >= 500:
            return True
        if 400 <= status_code < 500:
            return False

    text = (str(exc) or "").strip().lower()
    if any(keyword in text for keyword in _PERMANENT_ERROR_KEYWORDS):
        return False
    if any(keyword in text for keyword in _TEMPORARY_ERROR_KEYWORDS):
        return True

    # Default conservative mode: unknown crashes are treated as non-recoverable
    # so we do not loop forever on deterministic code bugs.
    return False

