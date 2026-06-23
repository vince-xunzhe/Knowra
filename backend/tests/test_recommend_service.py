import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from services.recommend_service import _is_due


def dt(value: str) -> datetime:
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)


def test_recommendation_scheduler_catches_up_after_missed_slot():
    last = dt("2026-06-19T10:00:00")  # Friday
    now = dt("2026-06-23T15:00:00")   # Tuesday, after missed Monday slot

    assert _is_due(now, last)


def test_recommendation_scheduler_does_not_repeat_same_day_slot():
    last = dt("2026-06-22T00:30:00")  # Monday
    now = dt("2026-06-22T23:30:00")   # Same Monday, >20h later

    assert not _is_due(now, last)


def test_recommendation_scheduler_respects_min_gap():
    last = dt("2026-06-23T23:00:00")  # Tuesday manual refresh
    now = dt("2026-06-24T10:00:00")   # Wednesday slot, but too soon

    assert not _is_due(now, last)
