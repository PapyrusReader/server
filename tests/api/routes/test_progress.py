"""Tests for reading progress and statistics endpoints."""

from datetime import UTC, datetime
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
            "pages_read": 20,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["book_id"] == book_id


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
