"""Tests for tag endpoints."""

from httpx import AsyncClient


async def test_list_tags(client: AsyncClient, auth_headers: dict[str, str]):
    """Test listing tags."""
    response = await client.get("/v1/tags", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "tags" in data


async def test_create_tag(client: AsyncClient, auth_headers: dict[str, str]):
    """Test creating a tag."""
    response = await client.post(
        "/v1/tags",
        headers=auth_headers,
        json={
            "name": "Fiction",
            "color": "#4A90D9",
            "description": "Fiction books",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Fiction"
    assert data["color"] == "#4A90D9"


async def test_get_tag(client: AsyncClient, auth_headers: dict[str, str], tag_id: str):
    """Test getting a tag by ID."""
    response = await client.get(f"/v1/tags/{tag_id}", headers=auth_headers)
    assert response.status_code == 200


async def test_update_tag(client: AsyncClient, auth_headers: dict[str, str], tag_id: str):
    """Test updating a tag."""
    response = await client.patch(
        f"/v1/tags/{tag_id}",
        headers=auth_headers,
        json={"name": "Updated Tag"},
    )
    assert response.status_code == 200


async def test_delete_tag(client: AsyncClient, auth_headers: dict[str, str], tag_id: str):
    """Test deleting a tag."""
    response = await client.delete(f"/v1/tags/{tag_id}", headers=auth_headers)
    assert response.status_code == 204
