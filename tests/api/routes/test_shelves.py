"""Tests for shelf endpoints."""

from uuid import uuid4

from httpx import AsyncClient


async def test_list_shelves(client: AsyncClient, auth_headers: dict[str, str]):
    """Test listing shelves."""
    response = await client.get("/v1/shelves", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "shelves" in data


async def test_create_shelf(client: AsyncClient, auth_headers: dict[str, str]):
    """Test creating a shelf."""
    response = await client.post(
        "/v1/shelves",
        headers=auth_headers,
        json={
            "name": "Test Shelf",
            "color": "#FF5722",
            "description": "A test shelf",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Shelf"


async def test_get_shelf(client: AsyncClient, auth_headers: dict[str, str], shelf_id: str):
    """Test getting a shelf by ID."""
    response = await client.get(f"/v1/shelves/{shelf_id}", headers=auth_headers)
    assert response.status_code == 200


async def test_update_shelf(client: AsyncClient, auth_headers: dict[str, str], shelf_id: str):
    """Test updating a shelf."""
    response = await client.patch(
        f"/v1/shelves/{shelf_id}",
        headers=auth_headers,
        json={"name": "Updated Shelf Name"},
    )
    assert response.status_code == 200


async def test_delete_shelf(client: AsyncClient, auth_headers: dict[str, str], shelf_id: str):
    """Test deleting a shelf."""
    response = await client.delete(f"/v1/shelves/{shelf_id}", headers=auth_headers)
    assert response.status_code == 204


async def test_list_shelf_books(client: AsyncClient, auth_headers: dict[str, str], shelf_id: str):
    """Test listing books in a shelf."""
    response = await client.get(f"/v1/shelves/{shelf_id}/books", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "books" in data


async def test_add_book_to_shelf(
    client: AsyncClient, auth_headers: dict[str, str], shelf_id: str, book_id: str
):
    """Test adding a book to a shelf."""
    response = await client.post(
        f"/v1/shelves/{shelf_id}/books/{book_id}",
        headers=auth_headers,
    )
    assert response.status_code == 204


async def test_remove_book_from_shelf(
    client: AsyncClient, auth_headers: dict[str, str], shelf_id: str, book_id: str
):
    """Test removing a book from a shelf."""
    response = await client.delete(
        f"/v1/shelves/{shelf_id}/books/{book_id}",
        headers=auth_headers,
    )
    assert response.status_code == 204


async def test_remove_multiple_books_from_shelf(
    client: AsyncClient, auth_headers: dict[str, str], shelf_id: str, book_id: str
):
    """Test removing multiple books from a shelf."""
    book_ids = [book_id, str(uuid4())]
    response = await client.post(
        f"/v1/shelves/{shelf_id}/books/remove",
        headers=auth_headers,
        json={"book_ids": book_ids},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["removed_count"] == 2
