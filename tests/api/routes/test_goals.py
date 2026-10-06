"""Owned persisted goal REST contract, including new reading-day metric."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from httpx import AsyncClient


@pytest.fixture
async def goal_id(client: AsyncClient, auth_headers: dict[str, str]):
    today = datetime.now(UTC).date()
    response = await client.post(
        "/v1/goals",
        headers=auth_headers,
        json={
            "title": "Read 12 books this year",
            "goal_type": "books_count",
            "target_value": 12,
            "time_period": "yearly",
            "start_date": f"{today.year}-01-01",
            "end_date": f"{today.year}-12-31",
        },
    )
    assert response.status_code == 201
    return response.json()["goal_id"]


async def test_empty_goals_have_no_examples(client, auth_headers):
    response = await client.get("/v1/goals", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["goals"] == []


async def test_get_goal(client, auth_headers, goal_id):
    response = await client.get(f"/v1/goals/{goal_id}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["current_value"] == 0
    assert response.json()["goal_type"] == "books_count"


async def test_update_goal(client, auth_headers, goal_id):
    response = await client.patch(f"/v1/goals/{goal_id}", headers=auth_headers, json={"target_value": 15})
    assert response.status_code == 200
    assert response.json()["target_value"] == 15
    assert (await client.get(f"/v1/goals/{goal_id}", headers=auth_headers)).json()["target_value"] == 15


async def test_delete_goal(client, auth_headers, goal_id):
    assert (await client.delete(f"/v1/goals/{goal_id}", headers=auth_headers)).status_code == 204
    assert (await client.get(f"/v1/goals/{goal_id}", headers=auth_headers)).status_code == 404


async def test_unknown_goal_is_not_an_example(client, auth_headers):
    assert (await client.get(f"/v1/goals/{uuid4()}", headers=auth_headers)).status_code == 404


async def test_create_reading_days_goal(client, auth_headers):
    today = datetime.now(UTC).date().isoformat()
    response = await client.post(
        "/v1/goals",
        headers=auth_headers,
        json={
            "title": "Read on five days",
            "goal_type": "reading_days",
            "target_value": 5,
            "time_period": "weekly",
            "start_date": today,
            "end_date": "2027-12-31",
            "timezone": "Europe/Vilnius",
            "minimum_minutes": 5,
        },
    )
    assert response.status_code == 201
    assert response.json()["goal_type"] == "reading_days"
    assert response.json()["timezone"] == "Europe/Vilnius"
