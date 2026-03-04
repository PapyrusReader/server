"""Tests for annotation endpoints."""

from httpx import AsyncClient


async def test_list_annotations(client: AsyncClient, auth_headers: dict[str, str]):
    """Test listing all annotations."""
    response = await client.get("/v1/annotations", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "annotations" in data
    assert "pagination" in data


async def test_list_book_annotations(client: AsyncClient, auth_headers: dict[str, str], book_id: str):
    """Test listing annotations for a specific book."""
    response = await client.get(f"/v1/annotations/books/{book_id}", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "annotations" in data


async def test_create_annotation(client: AsyncClient, auth_headers: dict[str, str], book_id: str):
    """Test creating an annotation."""
    response = await client.post(
        f"/v1/annotations/books/{book_id}",
        headers=auth_headers,
        json={
            "selected_text": "This is a highlighted passage.",
            "note": "My note",
            "highlight_color": "#FFEB3B",
            "start_position": "epubcfi(/6/4[chap01]!/4/2/1:0)",
            "end_position": "epubcfi(/6/4[chap01]!/4/2/1:42)",
        },
    )

    assert response.status_code == 201
    data = response.json()
    assert data["selected_text"] == "This is a highlighted passage."


async def test_get_annotation(client: AsyncClient, auth_headers: dict[str, str], annotation_id: str):
    """Test getting an annotation by ID."""
    response = await client.get(f"/v1/annotations/{annotation_id}", headers=auth_headers)
    assert response.status_code == 200


async def test_update_annotation(client: AsyncClient, auth_headers: dict[str, str], annotation_id: str):
    """Test updating an annotation."""
    response = await client.patch(
        f"/v1/annotations/{annotation_id}",
        headers=auth_headers,
        json={"note": "Updated note"},
    )

    assert response.status_code == 200


async def test_delete_annotation(client: AsyncClient, auth_headers: dict[str, str], annotation_id: str):
    """Test deleting an annotation."""
    response = await client.delete(f"/v1/annotations/{annotation_id}", headers=auth_headers)
    assert response.status_code == 204


async def test_export_annotations_json(client: AsyncClient, auth_headers: dict[str, str]):
    """Test exporting annotations as JSON."""
    response = await client.post(
        "/v1/annotations/export",
        headers=auth_headers,
        json={"format": "json"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["format"] == "json"
    assert "content" in data


async def test_export_annotations_markdown(client: AsyncClient, auth_headers: dict[str, str]):
    """Test exporting annotations as Markdown."""
    response = await client.post(
        "/v1/annotations/export",
        headers=auth_headers,
        json={"format": "markdown"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["format"] == "markdown"
