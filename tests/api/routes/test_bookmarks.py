"""Tests for bookmark endpoints."""

from httpx import AsyncClient


async def test_list_bookmarks(client: AsyncClient, auth_headers: dict[str, str]):
    """Test listing all bookmarks."""
    response = await client.get("/v1/bookmarks", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "bookmarks" in data


async def test_list_book_bookmarks(client: AsyncClient, auth_headers: dict[str, str], book_id: str):
    """Test listing bookmarks for a specific book."""
    response = await client.get(f"/v1/bookmarks/books/{book_id}", headers=auth_headers)
    assert response.status_code == 200


async def test_create_bookmark(client: AsyncClient, auth_headers: dict[str, str], book_id: str):
    """Test creating a bookmark."""
    response = await client.post(
        f"/v1/bookmarks/books/{book_id}",
        headers=auth_headers,
        json={
            "position": "epubcfi(/6/4[chap01]!/4/2/1:0)",
            "page_number": 42,
            "chapter_title": "Chapter 3",
            "note": "Interesting part",
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["page_number"] == 42


async def test_get_bookmark(client: AsyncClient, auth_headers: dict[str, str], bookmark_id: str):
    """Test getting a bookmark by ID."""
    response = await client.get(f"/v1/bookmarks/{bookmark_id}", headers=auth_headers)
    assert response.status_code == 200


async def test_update_bookmark(client: AsyncClient, auth_headers: dict[str, str], bookmark_id: str):
    """Test updating a bookmark."""
    response = await client.patch(
        f"/v1/bookmarks/{bookmark_id}",
        headers=auth_headers,
        json={"note": "Updated note"},
    )
    assert response.status_code == 200


async def test_delete_bookmark(client: AsyncClient, auth_headers: dict[str, str], bookmark_id: str):
    """Test deleting a bookmark."""
    response = await client.delete(f"/v1/bookmarks/{bookmark_id}", headers=auth_headers)
    assert response.status_code == 204
