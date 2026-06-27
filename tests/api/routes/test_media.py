"""Tests for authenticated media storage routes."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.models import SyncBook, User


async def _create_owned_book(db_session: AsyncSession, user_id: str) -> SyncBook:
    book = SyncBook(
        book_id=uuid4(),
        owner_user_id=UUID(user_id),
        title="Media Book",
        author="Reader",
        added_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    db_session.add(book)
    await db_session.commit()
    return book


async def test_upload_media_persists_file_and_updates_usage(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr("papyrus.main.settings.media_storage_root", str(tmp_path), raising=False)
    monkeypatch.setattr("papyrus.main.settings.file_storage_quota_bytes", 1_073_741_824)
    book = await _create_owned_book(db_session, auth_user["user_id"])

    response = await client.post(
        "/v1/media",
        headers=auth_headers,
        data={"book_id": str(book.book_id), "kind": "book_file"},
        files={"file": ("example.epub", b"epub bytes", "application/epub+zip")},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["book_id"] == str(book.book_id)
    assert body["kind"] == "book_file"
    assert body["original_filename"] == "example.epub"
    assert body["content_type"] == "application/epub+zip"
    assert body["size_bytes"] == len(b"epub bytes")
    assert body["sha256"] == "227dae38658f29c3a8494e65302e70b406162c2f581845339dfa19cbfad839d4"
    assert (tmp_path / body["storage_path"]).read_bytes() == b"epub bytes"

    usage = await client.get("/v1/media/usage", headers=auth_headers)
    assert usage.status_code == 200
    assert usage.json() == {
        "used_bytes": len(b"epub bytes"),
        "quota_bytes": 1_073_741_824,
        "available_bytes": 1_073_741_824 - len(b"epub bytes"),
    }


async def test_download_and_delete_owned_media(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr("papyrus.main.settings.media_storage_root", str(tmp_path), raising=False)
    book = await _create_owned_book(db_session, auth_user["user_id"])
    upload = await client.post(
        "/v1/media",
        headers=auth_headers,
        data={"book_id": str(book.book_id), "kind": "cover_image"},
        files={"file": ("cover.jpg", b"jpeg bytes", "image/jpeg")},
    )
    assert upload.status_code == 201
    asset_id = upload.json()["asset_id"]

    download = await client.get(f"/v1/media/{asset_id}", headers=auth_headers)
    assert download.status_code == 200
    assert download.content == b"jpeg bytes"
    assert download.headers["content-type"] == "image/jpeg"

    delete = await client.delete(f"/v1/media/{asset_id}", headers=auth_headers)
    assert delete.status_code == 204
    assert not (tmp_path / upload.json()["storage_path"]).exists()
    assert (await client.get(f"/v1/media/{asset_id}", headers=auth_headers)).status_code == 404


async def test_upload_rejects_quota_overflow_without_persisting_file(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr("papyrus.main.settings.media_storage_root", str(tmp_path), raising=False)
    monkeypatch.setattr("papyrus.main.settings.file_storage_quota_bytes", 4)
    book = await _create_owned_book(db_session, auth_user["user_id"])

    response = await client.post(
        "/v1/media",
        headers=auth_headers,
        data={"book_id": str(book.book_id), "kind": "book_file"},
        files={"file": ("too-big.epub", b"12345", "application/epub+zip")},
    )

    assert response.status_code == 409
    assert response.json()["error"]["message"] == "Storage quota exceeded"
    assert list(tmp_path.rglob("*")) == []


async def test_upload_rejects_cross_user_book(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr("papyrus.main.settings.media_storage_root", str(tmp_path), raising=False)
    other_user = User(
        display_name="Other User",
        primary_email="other-media@example.com",
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
        "/v1/media",
        headers=auth_headers,
        data={"book_id": str(foreign_book.book_id), "kind": "book_file"},
        files={"file": ("foreign.epub", b"bytes", "application/epub+zip")},
    )

    assert response.status_code == 403
