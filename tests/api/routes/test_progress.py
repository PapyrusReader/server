"""Tests for reading progress and statistics endpoints."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from httpx import AsyncClient


@pytest.fixture
async def book_id(client, auth_headers):
    identifier = str(uuid4())

    result = await client.post(
        "/v1/sync/powersync-upload",
        headers=auth_headers,
        json={
            "batch": [
                {"type": "books", "op": "PUT", "id": identifier, "data": {"title": "Reading book", "author": "Author"}}
            ]
        },
    )

    assert result.status_code == 200
    return identifier


async def test_list_reading_sessions(client: AsyncClient, auth_headers: dict[str, str]):
    """Test listing reading sessions."""
    response = await client.get("/v1/progress/sessions", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "sessions" in data
    assert "pagination" in data


async def test_create_reading_session(client: AsyncClient, auth_headers: dict[str, str], book_id: str):
    """Test creating a reading session."""
    now = datetime.now(UTC)

    response = await client.post(
        "/v1/progress/sessions",
        headers=auth_headers,
        json={
            "book_id": book_id,
            "start_time": now.isoformat(),
            "start_position": 0.25,
            "end_position": 0.35,
            "device_type": "android",
            "pages_read": 20,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["book_id"] == book_id
    assert data["start_position"] == 0.25
    assert data["end_position"] == 0.35
    assert data["device_type"] == "android"
    sessions = (await client.get("/v1/progress/sessions", headers=auth_headers)).json()
    assert sessions["sessions"][0] == data


async def test_get_reading_statistics(client: AsyncClient, auth_headers: dict[str, str]):
    """Test getting reading statistics."""
    response = await client.get("/v1/progress/statistics", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "period" in data
    assert "totals" in data
    assert "daily_breakdown" in data
    assert "books_breakdown" in data


async def test_get_reading_statistics_with_date_range(client: AsyncClient, auth_headers: dict[str, str]):
    """Test getting reading statistics with date range."""
    response = await client.get(
        "/v1/progress/statistics",
        headers=auth_headers,
        params={
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
        },
    )

    assert response.status_code == 200


async def test_manual_session_retry_and_real_statistics(client, auth_headers, book_id):
    from datetime import timedelta

    end = datetime.now(UTC)

    payload = {
        "session_id": str(uuid4()),
        "book_id": book_id,
        "start_time": (end - timedelta(minutes=20)).isoformat(),
        "end_time": end.isoformat(),
        "pages_read": 15,
    }

    first = await client.post("/v1/progress/sessions", headers=auth_headers, json=payload)
    second = await client.post("/v1/progress/sessions", headers=auth_headers, json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json()["session_id"] == second.json()["session_id"]
    sessions = (await client.get("/v1/progress/sessions", headers=auth_headers)).json()
    assert sessions["pagination"]["total"] == 1
    stats = (await client.get("/v1/progress/statistics", headers=auth_headers)).json()["totals"]
    assert stats["reading_time_minutes"] == 20
    assert stats["pages_read"] == 15


async def test_missing_book_is_rejected(client, auth_headers):
    response = await client.post(
        "/v1/progress/sessions",
        headers=auth_headers,
        json={"book_id": str(uuid4()), "start_time": datetime.now(UTC).isoformat(), "pages_read": 2},
    )

    assert response.status_code == 404


async def test_statistics_group_reader_checkpoints_as_one_session(client, auth_headers, book_id):
    end = datetime.now(UTC)
    session_id = str(uuid4())

    response = await client.post(
        "/v1/sync/powersync-upload",
        headers=auth_headers,
        json={
            "batch": [
                {
                    "type": "reading_activities",
                    "op": "PUT",
                    "id": identifier,
                    "data": {
                        "payload": {
                            "id": identifier,
                            "session_id": session_id,
                            "book_id": book_id,
                            "book_title": "Reading book",
                            "source": "reader",
                            "start_position": index / 10,
                            "end_position": (index + 1) / 10,
                            "device_type": "linux",
                            "start_time": (end - timedelta(minutes=20 - index * 10)).isoformat(),
                            "end_time": (end - timedelta(minutes=10 - index * 10)).isoformat(),
                            "created_at": end.isoformat(),
                        }
                    },
                }
                for index, identifier in enumerate([str(uuid4()), str(uuid4())])
            ]
        },
    )

    assert response.status_code == 200, response.text
    sessions = (await client.get("/v1/progress/sessions", headers=auth_headers)).json()
    assert sessions["pagination"]["total"] == 1
    assert sessions["sessions"][0]["duration_minutes"] == 20
    assert sessions["sessions"][0]["start_position"] == 0
    assert sessions["sessions"][0]["end_position"] == 0.2
    assert sessions["sessions"][0]["device_type"] == "linux"

    result = await client.get(
        "/v1/progress/statistics",
        headers=auth_headers,
        params={"start_date": (end - timedelta(minutes=20)).date().isoformat(), "end_date": end.date().isoformat()},
    )

    assert result.status_code == 200
    statistics = result.json()
    assert statistics["totals"]["sessions_count"] == 1
    assert statistics["totals"]["average_session_minutes"] == 20
    assert statistics["books_breakdown"][0]["sessions_count"] == 1


async def test_concurrent_session_retry_reuses_generated_timestamps(client, auth_headers, book_id, monkeypatch):
    import asyncio

    from papyrus.services import progress

    upload = progress.apply_powersync_upload_batch

    async def delayed_upload(*args, **kwargs):
        await asyncio.sleep(0.1)
        return await upload(*args, **kwargs)

    monkeypatch.setattr(progress, "apply_powersync_upload_batch", delayed_upload)

    payload = {
        "session_id": str(uuid4()),
        "book_id": book_id,
        "start_time": (datetime.now(UTC) - timedelta(minutes=10)).isoformat(),
        "start_position": 0.2,
        "end_position": 0.4,
        "device_type": "android",
        "pages_read": 12,
    }

    first, second = await asyncio.gather(
        client.post("/v1/progress/sessions", headers=auth_headers, json=payload),
        client.post("/v1/progress/sessions", headers=auth_headers, json=payload),
    )

    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()

    for field, value in [("start_position", 0.3), ("end_position", 0.5), ("device_type", "windows")]:
        response = await client.post("/v1/progress/sessions", headers=auth_headers, json={**payload, field: value})

        assert response.status_code == 400


async def test_statistics_excludes_sessions_ending_at_period_start(client, auth_headers, book_id):
    boundary = datetime(2026, 1, 2, tzinfo=UTC)

    response = await client.post(
        "/v1/progress/sessions",
        headers=auth_headers,
        json={
            "book_id": book_id,
            "start_time": (boundary - timedelta(minutes=10)).isoformat(),
            "end_time": boundary.isoformat(),
            "pages_read": 5,
        },
    )

    assert response.status_code == 200
    params = {"start_date": "2026-01-02", "end_date": "2026-01-02"}
    result = await client.get("/v1/progress/statistics", headers=auth_headers, params=params)
    statistics = result.json()
    assert statistics["totals"]["sessions_count"] == 0
    assert statistics["totals"]["pages_read"] == 0
    assert statistics["totals"]["reading_time_minutes"] == 0
    assert statistics["books_breakdown"] == []
    assert statistics["daily_breakdown"][0]["sessions_count"] == 0

    response = await client.post(
        "/v1/progress/sessions",
        headers=auth_headers,
        json={
            "book_id": book_id,
            "start_time": boundary.isoformat(),
            "end_time": boundary.isoformat(),
            "pages_read": 3,
        },
    )

    assert response.status_code == 200
    result = await client.get("/v1/progress/statistics", headers=auth_headers, params=params)
    statistics = result.json()
    assert statistics["totals"]["sessions_count"] == 1
    assert statistics["totals"]["pages_read"] == 3
    assert statistics["daily_breakdown"][0]["sessions_count"] == 1


async def test_current_streak_is_independent_of_statistics_period(client, auth_headers, book_id, monkeypatch):
    from papyrus.services import progress

    now = datetime(2026, 3, 1, 12, tzinfo=UTC)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return now

    monkeypatch.setattr(progress, "datetime", FrozenDateTime)

    for days_ago in range(3):
        end = now - timedelta(days=days_ago)

        response = await client.post(
            "/v1/progress/sessions",
            headers=auth_headers,
            json={
                "book_id": book_id,
                "start_time": (end - timedelta(minutes=10)).isoformat(),
                "end_time": end.isoformat(),
            },
        )

        assert response.status_code == 200

    for params in [{}, {"start_date": "2026-02-01", "end_date": "2026-02-01"}]:
        response = await client.get("/v1/progress/statistics", headers=auth_headers, params=params)
        assert response.status_code == 200
        assert response.json()["totals"]["current_streak"] == 3

    current = (await client.get("/v1/progress/statistics", headers=auth_headers)).json()
    assert current["totals"]["longest_streak"] == 1
