"""Tests for note endpoints."""

from httpx import AsyncClient


async def test_list_notes(client: AsyncClient, auth_headers: dict[str, str]):
    """Test listing all notes."""
    response = await client.get("/v1/notes", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "notes" in data


async def test_list_book_notes(client: AsyncClient, auth_headers: dict[str, str], book_id: str):
    """Test listing notes for a specific book."""
    response = await client.get(f"/v1/notes/books/{book_id}", headers=auth_headers)
    assert response.status_code == 200


async def test_create_note(client: AsyncClient, auth_headers: dict[str, str], book_id: str):
    """Test creating a note."""
    response = await client.post(
        f"/v1/notes/books/{book_id}",
        headers=auth_headers,
        json={
            "title": "My Note",
            "content": "This is the content of my note.",
            "is_pinned": False,
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "My Note"


async def test_get_note(client: AsyncClient, auth_headers: dict[str, str], note_id: str):
    """Test getting a note by ID."""
    response = await client.get(f"/v1/notes/{note_id}", headers=auth_headers)
    assert response.status_code == 200


async def test_update_note(client: AsyncClient, auth_headers: dict[str, str], note_id: str):
    """Test updating a note."""
    response = await client.patch(
        f"/v1/notes/{note_id}",
        headers=auth_headers,
        json={"title": "Updated Note Title"},
    )
    assert response.status_code == 200


async def test_delete_note(client: AsyncClient, auth_headers: dict[str, str], note_id: str):
    """Test deleting a note."""
    response = await client.delete(f"/v1/notes/{note_id}", headers=auth_headers)
    assert response.status_code == 204
