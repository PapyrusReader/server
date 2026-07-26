"""Tests for authenticated media storage routes."""

import asyncio
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import UploadFile
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from papyrus.core.exceptions import ConflictError
from papyrus.models import MediaAsset, SyncBook, User
from papyrus.services import media as media_service


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


async def test_import_media_path_copies_download_and_keeps_source_for_seeding(
    auth_user: dict[str, str],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    media_root = tmp_path / "media"
    source_path = tmp_path / "downloads" / "book.epub"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"downloaded epub")
    monkeypatch.setattr("papyrus.main.settings.media_storage_root", str(media_root), raising=False)
    monkeypatch.setattr("papyrus.main.settings.file_storage_quota_bytes", 1_073_741_824)
    book = await _create_owned_book(db_session, auth_user["user_id"])

    asset = await media_service.import_media_path(
        db_session,
        UUID(auth_user["user_id"]),
        book_id=book.book_id,
        kind="book_file",
        source_path=source_path,
    )

    assert asset.original_filename == "book.epub"
    assert asset.size_bytes == len(b"downloaded epub")
    assert asset.sha256 == "b7783cce10abf92f10284f7089ffd31daf92e0b532284a6e14488b20834b5e16"
    assert (media_root / asset.storage_path).read_bytes() == b"downloaded epub"
    assert source_path.read_bytes() == b"downloaded epub"

    await db_session.refresh(book)
    assert book.file_media_id == asset.asset_id


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


async def test_upload_removes_new_file_when_commit_fails(
    auth_user: dict[str, str],
    db_session: AsyncSession,
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr("papyrus.main.settings.media_storage_root", str(tmp_path), raising=False)
    monkeypatch.setattr("papyrus.main.settings.file_storage_quota_bytes", 1_073_741_824)
    book = await _create_owned_book(db_session, auth_user["user_id"])

    async def fail_commit() -> None:
        raise RuntimeError("database commit failed")

    monkeypatch.setattr(db_session, "commit", fail_commit)

    with pytest.raises(RuntimeError, match="database commit failed"):
        await media_service.upload_media(
            db_session,
            UUID(auth_user["user_id"]),
            book_id=book.book_id,
            kind="book_file",
            file=UploadFile(filename="example.epub", file=BytesIO(b"epub bytes")),
        )

    assert [path for path in tmp_path.rglob("*") if path.is_file()] == []


async def test_replacing_media_keeps_existing_file_when_commit_fails(
    auth_user: dict[str, str],
    db_session: AsyncSession,
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr("papyrus.main.settings.media_storage_root", str(tmp_path), raising=False)
    monkeypatch.setattr("papyrus.main.settings.file_storage_quota_bytes", 1_073_741_824)
    book = await _create_owned_book(db_session, auth_user["user_id"])
    first = await media_service.upload_media(
        db_session,
        UUID(auth_user["user_id"]),
        book_id=book.book_id,
        kind="book_file",
        file=UploadFile(filename="book.epub", file=BytesIO(b"first book")),
    )
    first_path = tmp_path / first.storage_path
    assert first_path.read_bytes() == b"first book"

    async def fail_commit() -> None:
        raise RuntimeError("database commit failed")

    monkeypatch.setattr(db_session, "commit", fail_commit)

    with pytest.raises(RuntimeError, match="database commit failed"):
        await media_service.upload_media(
            db_session,
            UUID(auth_user["user_id"]),
            book_id=book.book_id,
            kind="book_file",
            file=UploadFile(filename="book.epub", file=BytesIO(b"second book")),
        )

    assert first_path.read_bytes() == b"first book"
    assert sorted(path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()) == [b"first book"]


async def test_concurrent_same_kind_uploads_leave_one_asset(
    auth_user: dict[str, str],
    test_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr("papyrus.main.settings.media_storage_root", str(tmp_path), raising=False)
    monkeypatch.setattr("papyrus.main.settings.file_storage_quota_bytes", 1_073_741_824)
    user_id = UUID(auth_user["user_id"])
    async with test_session_maker() as setup_session:
        book = await _create_owned_book(setup_session, auth_user["user_id"])
        book_id = book.book_id

    async def upload(contents: bytes) -> MediaAsset:
        async with test_session_maker() as session:
            return await media_service.upload_media(
                session,
                user_id,
                book_id=book_id,
                kind="book_file",
                file=UploadFile(filename="book.epub", file=BytesIO(contents)),
            )

    first, second = await asyncio.gather(upload(b"first"), upload(b"second"))

    async with test_session_maker() as session:
        assets = list((await session.execute(select(MediaAsset).where(MediaAsset.book_id == book_id))).scalars())
        stored_book = await session.get(SyncBook, book_id)

    assert first.asset_id != second.asset_id
    assert len(assets) == 1
    assert stored_book is not None
    assert stored_book.file_media_id == assets[0].asset_id
    assert [path for path in tmp_path.rglob("*") if path.is_file()] == [tmp_path / assets[0].storage_path]


async def test_user_upload_lock_blocks_a_second_session(
    auth_user: dict[str, str],
    test_session_maker: async_sessionmaker[AsyncSession],
):
    user_id = UUID(auth_user["user_id"])
    async with test_session_maker() as first_session, test_session_maker() as second_session:
        await media_service._lock_user_uploads(first_session, user_id)
        second_lock = asyncio.create_task(media_service._lock_user_uploads(second_session, user_id))
        await asyncio.sleep(0.05)
        assert not second_lock.done()
        await first_session.rollback()
        await asyncio.wait_for(second_lock, timeout=1)


async def test_concurrent_uploads_enforce_aggregate_user_quota(
    auth_user: dict[str, str],
    test_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch,
    tmp_path: Path,
):
    monkeypatch.setattr("papyrus.main.settings.media_storage_root", str(tmp_path), raising=False)
    monkeypatch.setattr("papyrus.main.settings.file_storage_quota_bytes", 5)
    user_id = UUID(auth_user["user_id"])
    async with test_session_maker() as setup_session:
        first_book = await _create_owned_book(setup_session, auth_user["user_id"])
        second_book = await _create_owned_book(setup_session, auth_user["user_id"])
        book_ids = (first_book.book_id, second_book.book_id)

    async def upload(book_id: UUID) -> MediaAsset:
        async with test_session_maker() as session:
            return await media_service.upload_media(
                session,
                user_id,
                book_id=book_id,
                kind="book_file",
                file=UploadFile(filename="book.epub", file=BytesIO(b"1234")),
            )

    results = await asyncio.gather(*(upload(book_id) for book_id in book_ids), return_exceptions=True)

    async with test_session_maker() as session:
        asset_count = await session.scalar(
            select(func.count()).select_from(MediaAsset).where(MediaAsset.owner_user_id == user_id)
        )
        used_bytes = await session.scalar(
            select(func.coalesce(func.sum(MediaAsset.size_bytes), 0)).where(MediaAsset.owner_user_id == user_id)
        )

    assert sum(isinstance(result, MediaAsset) for result in results) == 1
    assert sum(isinstance(result, ConflictError) for result in results) == 1
    assert asset_count == 1
    assert used_bytes == 4
    assert sum(path.stat().st_size for path in tmp_path.rglob("*") if path.is_file()) == 4
