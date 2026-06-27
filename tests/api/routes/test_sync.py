"""Tests for sync endpoints."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.models import SyncBook, User


async def test_sync_settings_are_public_and_hide_implementation_details(client: AsyncClient, monkeypatch):
    """Return public data sync settings for one-URL custom server discovery."""
    from papyrus.main import settings as app_settings

    monkeypatch.setattr(app_settings, "powersync_service_url", "https://sync.papyrus.test")
    monkeypatch.setattr(app_settings, "file_storage_quota_bytes", 1_073_741_824)

    response = await client.get("/v1/sync/settings")

    assert response.status_code == 200
    assert response.json() == {
        "data_sync_url": "https://sync.papyrus.test",
        "file_storage": {
            "supported": True,
            "quota_bytes": 1_073_741_824,
        },
    }
    assert "powersync" not in response.text.lower()


async def test_legacy_sync_routes_are_removed(client: AsyncClient, auth_headers: dict[str, str]):
    """PowerSync is the only supported synchronization contract."""
    assert (await client.get("/v1/sync/status", headers=auth_headers)).status_code == 404
    assert (await client.get("/v1/sync/changes", headers=auth_headers)).status_code == 404
    assert (await client.post("/v1/sync/changes", headers=auth_headers, json={})).status_code == 404
    assert (await client.get("/v1/sync/conflicts", headers=auth_headers)).status_code == 404
    assert (await client.post("/v1/sync/force", headers=auth_headers)).status_code == 404
    assert (await client.get("/v1/sync/config", headers=auth_headers)).status_code == 404


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


async def test_powersync_upload_rejects_partial_future_tables(
    client: AsyncClient,
    auth_headers: dict[str, str],
):
    """Annotations and reading sessions are not part of the books-only contract."""
    for table in ("annotations", "reading_sessions"):
        response = await client.post(
            "/v1/sync/powersync-upload",
            headers=auth_headers,
            json={"batch": [{"type": table, "op": "PUT", "id": str(uuid4()), "data": {}}]},
        )
        assert response.status_code == 422


async def test_powersync_upload_rejects_unknown_book_fields(
    client: AsyncClient,
    auth_headers: dict[str, str],
):
    response = await client.post(
        "/v1/sync/powersync-upload",
        headers=auth_headers,
        json={
            "batch": [
                {
                    "type": "books",
                    "op": "PUT",
                    "id": str(uuid4()),
                    "data": {"title": "Book", "unexpected": "value"},
                }
            ]
        },
    )
    assert response.status_code == 422


async def test_powersync_upload_controls_owner_and_updated_at(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
):
    book_id = uuid4()
    client_timestamp = datetime(2000, 1, 1, tzinfo=UTC)
    response = await client.post(
        "/v1/sync/powersync-upload",
        headers=auth_headers,
        json={
            "batch": [
                {
                    "type": "books",
                    "op": "PUT",
                    "id": str(book_id),
                    "data": {
                        "title": "Controlled fields",
                        "owner_user_id": str(uuid4()),
                        "updated_at": client_timestamp.isoformat(),
                    },
                }
            ]
        },
    )

    assert response.status_code == 200
    db_session.expire_all()
    book = await db_session.get(SyncBook, book_id)
    assert book is not None
    assert book.owner_user_id == UUID(auth_user["user_id"])
    assert book.updated_at > client_timestamp


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


async def test_powersync_upload_accepts_owned_media_references(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr("papyrus.main.settings.media_storage_root", str(tmp_path), raising=False)
    book_id = uuid4()
    book = SyncBook(
        book_id=book_id,
        owner_user_id=UUID(auth_user["user_id"]),
        title="Media Book",
        added_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(book)
    await db_session.commit()
    upload = await client.post(
        "/v1/media",
        headers=auth_headers,
        data={"book_id": str(book_id), "kind": "cover_image"},
        files={"file": ("cover.png", b"png bytes", "image/png")},
    )
    assert upload.status_code == 201
    asset_id = upload.json()["asset_id"]

    response = await client.post(
        "/v1/sync/powersync-upload",
        headers=auth_headers,
        json={
            "batch": [
                {
                    "type": "books",
                    "op": "PATCH",
                    "id": str(book_id),
                    "data": {"cover_media_id": asset_id},
                }
            ]
        },
    )

    assert response.status_code == 200
    db_session.expire_all()
    synced_book = await db_session.get(SyncBook, book_id)
    assert synced_book is not None
    assert str(synced_book.cover_media_id) == asset_id


async def test_powersync_upload_rejects_unknown_media_reference(
    client: AsyncClient,
    auth_headers: dict[str, str],
):
    response = await client.post(
        "/v1/sync/powersync-upload",
        headers=auth_headers,
        json={
            "batch": [
                {
                    "type": "books",
                    "op": "PUT",
                    "id": str(uuid4()),
                    "data": {"title": "Bad Media", "file_media_id": str(uuid4())},
                }
            ]
        },
    )

    assert response.status_code == 400
    assert response.json()["error"]["message"] == "file_media_id was not found"
