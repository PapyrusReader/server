"""Tests for sync endpoints."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.models import SyncBook, User


async def test_get_sync_status(client: AsyncClient, auth_headers: dict[str, str]):
    """Test getting sync status."""
    response = await client.get("/v1/sync/status", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "status" in data


async def test_pull_changes(client: AsyncClient, auth_headers: dict[str, str]):
    """Test pulling changes from server."""
    response = await client.get("/v1/sync/changes", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "changes" in data
    assert "server_timestamp" in data


async def test_push_changes(client: AsyncClient, auth_headers: dict[str, str]):
    """Test pushing changes to server."""
    now = datetime.now(UTC)
    response = await client.post(
        "/v1/sync/changes",
        headers=auth_headers,
        json={
            "changes": [
                {
                    "entity_type": "book",
                    "entity_id": str(uuid4()),
                    "operation": "update",
                    "data": {"title": "Updated Title"},
                    "timestamp": now.isoformat(),
                }
            ],
            "device_id": "device_123",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "accepted" in data
    assert "rejected" in data


async def test_powersync_upload_applies_book_mutation(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
):
    """Test production PowerSync upload endpoint applies owned book mutations."""
    book_id = str(uuid4())
    response = await client.post(
        "/v1/sync/powersync-upload",
        headers=auth_headers,
        json={
            "batch": [
                {
                    "type": "books",
                    "op": "PUT",
                    "id": book_id,
                    "data": {
                        "title": "PowerSync Book",
                        "author": "PowerSync Author",
                    },
                }
            ]
        },
    )
    assert response.status_code == 200
    assert response.json()["applied_count"] == 1

    book = await db_session.get(SyncBook, UUID(book_id))
    assert book is not None
    assert book.owner_user_id == UUID(auth_user["user_id"])
    assert book.title == "PowerSync Book"


async def test_powersync_upload_applies_book_patch(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
):
    """Test production PowerSync upload endpoint patches owned book mutations."""
    book = SyncBook(
        book_id=uuid4(),
        owner_user_id=UUID(auth_user["user_id"]),
        title="Original Title",
        author="Original Author",
        added_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(book)
    await db_session.commit()
    book_id = book.book_id

    response = await client.post(
        "/v1/sync/powersync-upload",
        headers=auth_headers,
        json={
            "batch": [
                {
                    "type": "books",
                    "op": "PATCH",
                    "id": str(book.book_id),
                    "data": {"title": "Patched Title"},
                }
            ]
        },
    )
    assert response.status_code == 200
    assert response.json()["applied_count"] == 1

    db_session.expire_all()
    patched_book = await db_session.get(SyncBook, book_id)
    assert patched_book is not None
    assert patched_book.title == "Patched Title"
    assert patched_book.author == "Original Author"


async def test_powersync_upload_applies_book_delete(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
):
    """Test production PowerSync upload endpoint deletes owned books."""
    book = SyncBook(
        book_id=uuid4(),
        owner_user_id=UUID(auth_user["user_id"]),
        title="Delete Me",
        added_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(book)
    await db_session.commit()
    book_id = book.book_id

    response = await client.post(
        "/v1/sync/powersync-upload",
        headers=auth_headers,
        json={"batch": [{"type": "books", "op": "DELETE", "id": str(book.book_id)}]},
    )
    assert response.status_code == 200
    assert response.json()["applied_count"] == 1

    db_session.expire_all()
    deleted_book = await db_session.get(SyncBook, book_id)
    assert deleted_book is None


async def test_powersync_upload_rejects_unsupported_table(
    client: AsyncClient,
    auth_headers: dict[str, str],
):
    """Test production PowerSync upload endpoint rejects unsupported tables."""
    response = await client.post(
        "/v1/sync/powersync-upload",
        headers=auth_headers,
        json={"batch": [{"type": "shelves", "op": "PUT", "id": str(uuid4()), "data": {"name": "Shelf"}}]},
    )
    assert response.status_code == 422


async def test_powersync_upload_rejects_cross_user_book_mutation(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
):
    """Test production PowerSync upload endpoint rejects cross-user writes."""
    other_user = User(
        display_name="Other User",
        primary_email="other-sync@example.com",
        primary_email_verified=True,
        last_login_at=datetime.now(UTC),
    )
    db_session.add(other_user)
    await db_session.flush()
    foreign_book = SyncBook(
        book_id=uuid4(),
        owner_user_id=other_user.user_id,
        title="Foreign Book",
        added_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(foreign_book)
    await db_session.commit()

    response = await client.post(
        "/v1/sync/powersync-upload",
        headers=auth_headers,
        json={
            "batch": [
                {
                    "type": "books",
                    "op": "PATCH",
                    "id": str(foreign_book.book_id),
                    "data": {"title": "Unauthorized Edit"},
                }
            ]
        },
    )
    assert response.status_code == 403


async def test_get_sync_conflicts(client: AsyncClient, auth_headers: dict[str, str]):
    """Test getting sync conflicts."""
    response = await client.get("/v1/sync/conflicts", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()
    assert "conflicts" in data


async def test_force_sync(client: AsyncClient, auth_headers: dict[str, str]):
    """Test forcing a full sync."""
    response = await client.post("/v1/sync/force", headers=auth_headers)
    assert response.status_code == 204


async def test_get_metadata_server_config(client: AsyncClient, auth_headers: dict[str, str]):
    """Test getting metadata server configuration."""
    response = await client.get("/v1/sync/config", headers=auth_headers)
    assert response.status_code == 200


async def test_create_metadata_server_config(client: AsyncClient, auth_headers: dict[str, str]):
    """Test creating metadata server configuration."""
    response = await client.post(
        "/v1/sync/config",
        headers=auth_headers,
        json={
            "server_url": "https://api.papyrus.app",
            "sync_enabled": True,
            "sync_interval_seconds": 30,
        },
    )
    assert response.status_code == 201


async def test_delete_metadata_server_config(client: AsyncClient, auth_headers: dict[str, str]):
    """Test deleting metadata server configuration."""
    response = await client.delete("/v1/sync/config", headers=auth_headers)
    assert response.status_code == 204


async def test_test_metadata_server_connection(client: AsyncClient, auth_headers: dict[str, str]):
    """Test testing metadata server connection."""
    response = await client.post("/v1/sync/config/test", headers=auth_headers)
    assert response.status_code == 200
