"""Statistics streaks union activity boundaries without expanding every calendar day."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from papyrus.schemas.tracking import Activity
from papyrus.services import progress


def activity(start: datetime, end: datetime) -> Activity:
    return Activity(
        id=uuid4(),
        book_id=uuid4(),
        book_title="Book",
        start_time=start,
        end_time=end,
        created_at=end,
    )


def test_streak_unions_partial_days_and_does_not_double_count_overlaps():
    today = datetime(2026, 3, 1, tzinfo=UTC)
    now = today + timedelta(hours=12)

    entries = [
        activity(today - timedelta(days=2), today - timedelta(days=2) + timedelta(minutes=5)),
        activity(today - timedelta(minutes=2), today + timedelta(minutes=2)),
        activity(today - timedelta(minutes=2), today + timedelta(minutes=2)),
        activity(today - timedelta(minutes=5), today - timedelta(minutes=2)),
        activity(today + timedelta(hours=1), today + timedelta(hours=1, minutes=3)),
    ]

    assert progress.current_reading_streak(entries, now) == 3
    assert progress.current_reading_streak(entries[:-1], now) == 2
    assert progress.current_reading_streak(entries, now + timedelta(days=2)) == 0


def test_streak_clips_future_time_and_ignores_non_reading_records():
    today = datetime(2026, 3, 1, tzinfo=UTC)
    entry = activity(today, today + timedelta(days=5))
    assert progress.current_reading_streak([entry], today + timedelta(minutes=4)) == 0
    assert progress.current_reading_streak([entry], today + timedelta(minutes=5)) == 1
    completion = entry.model_copy(update={"kind": "completion"})
    assert progress.current_reading_streak([completion], today + timedelta(days=5)) == 0


def test_streak_cost_does_not_scale_with_activity_duration(monkeypatch):
    now = datetime(2026, 3, 1, 12, tzinfo=UTC)
    start = datetime(1, 1, 1, tzinfo=UTC)
    combines = 0

    class CountingDateTime(datetime):
        @classmethod
        def combine(cls, *args, **kwargs):
            nonlocal combines
            combines += 1
            assert combines <= 4
            return datetime.combine(*args, **kwargs)

    monkeypatch.setattr(progress, "datetime", CountingDateTime)
    entries = [activity(start, now), activity(start, now)]
    assert progress.current_reading_streak(entries, now) == now.date().toordinal()
    assert combines == 4
