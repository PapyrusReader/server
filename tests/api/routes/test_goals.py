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


async def test_nonrecurring_calendar_goal_retains_future_dates(client, auth_headers):
    year = datetime.now(UTC).year + 1
    response = await client.post(
        "/v1/goals",
        headers=auth_headers,
        json={
            "title": "Next year's books",
            "goal_type": "books_count",
            "target_value": 12,
            "time_period": "yearly",
            "is_recurring": False,
            "timezone": "Europe/Vilnius",
            "start_date": f"{year}-01-01",
            "end_date": f"{year}-12-31",
        },
    )
    assert response.status_code == 201, response.text
    saved = response.json()
    fetched = (await client.get(f"/v1/goals/{saved['goal_id']}", headers=auth_headers)).json()

    for goal in [saved, fetched]:
        assert goal["start_date"] == f"{year}-01-01"
        assert goal["end_date"] == f"{year}-12-31"
        assert goal["is_recurring"] is False


@pytest.mark.parametrize("field", ["title", "target_value", "is_active", "is_archived"])
async def test_null_goal_settings_are_rejected_without_modification(client, auth_headers, goal_id, field):
    before = (await client.get(f"/v1/goals/{goal_id}", headers=auth_headers)).json()
    response = await client.patch(f"/v1/goals/{goal_id}", headers=auth_headers, json={field: None})
    assert response.status_code == 422
    after = (await client.get(f"/v1/goals/{goal_id}", headers=auth_headers)).json()
    assert after == before


async def test_goal_description_can_be_cleared(client, auth_headers, goal_id):
    response = await client.patch(f"/v1/goals/{goal_id}", headers=auth_headers, json={"description": "A note"})
    assert response.status_code == 200
    assert response.json()["description"] == "A note"
    response = await client.patch(f"/v1/goals/{goal_id}", headers=auth_headers, json={"description": None})
    assert response.status_code == 200
    assert response.json()["description"] is None


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


async def test_create_selected_books_goal_round_trips_and_bounds_target(client, auth_headers):
    books = []

    for title in ["First selected book", "Second selected book"]:
        book_id = str(uuid4())
        response = await client.post(
            "/v1/sync/powersync-upload",
            headers=auth_headers,
            json={
                "batch": [
                    {"type": "books", "op": "PUT", "id": book_id, "data": {"title": title, "author": "Test Author"}}
                ]
            },
        )
        assert response.status_code == 200, response.text
        books.append(book_id)

    today = datetime.now(UTC).date().isoformat()
    request = {
        "title": "Finish these books",
        "goal_type": "books_count",
        "target_value": 2,
        "time_period": "daily",
        "start_date": today,
        "end_date": today,
        "scope": "book",
        "book_ids": books,
    }
    response = await client.post("/v1/goals", headers=auth_headers, json=request)
    assert response.status_code == 201, response.text
    goal = response.json()
    assert set(goal["book_ids"]) == set(books)
    assert goal["scope_id"] in books
    result = await client.get(f"/v1/goals/{goal['goal_id']}", headers=auth_headers)
    assert set(result.json()["book_ids"]) == set(books)
    invalid = await client.post("/v1/goals", headers=auth_headers, json={**request, "target_value": 12})
    assert invalid.status_code == 400
    single = await client.post(
        "/v1/goals", headers=auth_headers, json={**request, "book_ids": books[:1], "target_value": 1}
    )
    assert single.status_code == 201
    assert single.json()["scope_id"] == books[0]
    assert single.json()["book_ids"] == []
    saved = await client.patch(
        f"/v1/goals/{single.json()['goal_id']}", headers=auth_headers, json={"title": "One selected book"}
    )
    assert saved.status_code == 200
    assert saved.json()["scope_id"] == books[0]
