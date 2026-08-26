from __future__ import annotations


PAPER_LEARNING_NOT_STARTED = "not_started"
PAPER_LEARNING_IN_PROGRESS = "learning"
PAPER_LEARNING_COMPLETED = "completed"

PAPER_LEARNING_STATUSES = {
    PAPER_LEARNING_NOT_STARTED,
    PAPER_LEARNING_IN_PROGRESS,
    PAPER_LEARNING_COMPLETED,
}


def normalize_learning_status(value: str | None) -> str:
    status = (value or "").strip()
    if status in PAPER_LEARNING_STATUSES:
        return status
    return PAPER_LEARNING_NOT_STARTED


def is_learning_status(value: str | None) -> bool:
    return (value or "").strip() in PAPER_LEARNING_STATUSES
