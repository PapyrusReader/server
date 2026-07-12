"""Private media storage service."""

from __future__ import annotations

import hashlib
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.config import get_settings
from papyrus.core.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationError
from papyrus.models import MediaAsset, SyncBook, User

BOOK_EXTENSIONS = {"epub", "pdf", "mobi", "azw3", "txt", "cbr", "cbz"}
COVER_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}
MEDIA_KINDS = {"book_file", "cover_image"}
UPLOAD_CHUNK_SIZE = 1024 * 1024


def media_root() -> Path:
    return Path(get_settings().media_storage_root)


async def usage(session: AsyncSession, user_id: UUID) -> tuple[int, int, int]:
    """Return used, quota, and available bytes for a user."""
    used = await _used_bytes(session, user_id)
    quota = get_settings().file_storage_quota_bytes
    return used, quota, max(quota - used, 0)


async def upload_media(
    session: AsyncSession,
    user_id: UUID,
    *,
    book_id: UUID,
    kind: str,
    file: UploadFile,
) -> MediaAsset:
    """Validate and persist an uploaded media asset."""
    if kind not in MEDIA_KINDS:
        raise ValidationError("Unsupported media kind")

    await _lock_user_uploads(session, user_id)
    book = (
        await session.execute(select(SyncBook).where(SyncBook.book_id == book_id).with_for_update())
    ).scalar_one_or_none()
    if book is None:
        raise NotFoundError("Book was not found")
    if book.owner_user_id != user_id:
        raise ForbiddenError("Cannot access another user's book")

    filename = file.filename or "upload"
    extension = _extension(filename)
    content_type = file.content_type or "application/octet-stream"
    _validate_media_type(kind, extension, content_type)

    existing = await _existing_asset_for_kind(session, book, kind)
    used = await _used_bytes(session, user_id)
    used_without_existing = used - (existing.size_bytes if existing is not None else 0)
    quota = get_settings().file_storage_quota_bytes

    asset_id = uuid4()
    storage_path = f"{user_id}/{book_id}/{asset_id}.{extension}"
    absolute_path = media_root() / storage_path
    temp_path = absolute_path.with_name(f".{asset_id}.{extension}.tmp")
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    final_file_written = False

    try:
        size_bytes, sha256_hex = await _write_upload_to_temp_file(
            file,
            temp_path,
            quota_remaining=quota - used_without_existing,
        )
        temp_path.replace(absolute_path)
        final_file_written = True

        asset = MediaAsset(
            asset_id=asset_id,
            owner_user_id=user_id,
            book_id=book_id,
            kind=kind,
            original_filename=filename,
            content_type=content_type,
            extension=extension,
            size_bytes=size_bytes,
            sha256=sha256_hex,
            storage_path=storage_path,
        )
        if existing is not None:
            await session.delete(existing)
            await session.flush()

        session.add(asset)
        if kind == "book_file":
            book.file_media_id = asset.asset_id
        else:
            book.cover_media_id = asset.asset_id

        await session.commit()
        if existing is not None:
            delete_physical_file(existing)
        await session.refresh(asset)
        return asset
    except Exception:
        await session.rollback()
        _delete_path(temp_path)
        if final_file_written:
            _delete_path(absolute_path)
        _delete_empty_parent_dirs(absolute_path.parent, stop_at=media_root())
        raise


async def get_owned_asset(session: AsyncSession, user_id: UUID, asset_id: UUID) -> MediaAsset:
    asset = await session.get(MediaAsset, asset_id)
    if asset is None:
        raise NotFoundError("Media asset was not found")
    if asset.owner_user_id != user_id:
        raise NotFoundError("Media asset was not found")
    return asset


async def delete_media(session: AsyncSession, user_id: UUID, asset_id: UUID) -> None:
    asset = await get_owned_asset(session, user_id, asset_id)
    book = await session.get(SyncBook, asset.book_id)
    if book is not None:
        if book.file_media_id == asset.asset_id:
            book.file_media_id = None
        if book.cover_media_id == asset.asset_id:
            book.cover_media_id = None
    await session.delete(asset)
    await session.commit()
    delete_physical_file(asset)


async def delete_book_media(session: AsyncSession, user_id: UUID, book_id: UUID) -> list[Path]:
    result = await session.execute(
        select(MediaAsset).where(MediaAsset.owner_user_id == user_id, MediaAsset.book_id == book_id)
    )
    deleted_paths: list[Path] = []
    for asset in result.scalars():
        deleted_paths.append(asset_path(asset))
        await session.delete(asset)
    return deleted_paths


async def validate_media_reference(
    session: AsyncSession,
    user_id: UUID,
    book_id: UUID,
    asset_id: UUID | None,
    *,
    field_name: str,
    expected_kind: str,
) -> UUID | None:
    if asset_id is None:
        return None
    asset = await session.get(MediaAsset, asset_id)
    if asset is None:
        raise ValidationError(f"{field_name} was not found")
    if asset.owner_user_id != user_id or asset.book_id != book_id:
        raise ForbiddenError(f"{field_name} does not belong to this book")
    if asset.kind != expected_kind:
        raise ValidationError(f"{field_name} has the wrong media kind")
    return asset.asset_id


def asset_path(asset: MediaAsset) -> Path:
    return media_root() / asset.storage_path


async def _used_bytes(session: AsyncSession, user_id: UUID) -> int:
    result = await session.execute(
        select(func.coalesce(func.sum(MediaAsset.size_bytes), 0)).where(MediaAsset.owner_user_id == user_id)
    )
    return int(result.scalar_one())


async def _lock_user_uploads(session: AsyncSession, user_id: UUID) -> None:
    """Serialize quota and replacement decisions for one user's uploads."""
    await session.execute(select(User.user_id).where(User.user_id == user_id).with_for_update())


async def _write_upload_to_temp_file(
    file: UploadFile,
    temp_path: Path,
    *,
    quota_remaining: int,
) -> tuple[int, str]:
    hasher = hashlib.sha256()
    size_bytes = 0

    with temp_path.open("wb") as output:
        while chunk := await file.read(UPLOAD_CHUNK_SIZE):
            size_bytes += len(chunk)
            if size_bytes > quota_remaining:
                raise ConflictError("Storage quota exceeded")
            hasher.update(chunk)
            output.write(chunk)

    if size_bytes == 0:
        raise ValidationError("Uploaded file is empty")

    return size_bytes, hasher.hexdigest()


async def _existing_asset_for_kind(session: AsyncSession, book: SyncBook, kind: str) -> MediaAsset | None:
    return (
        await session.execute(select(MediaAsset).where(MediaAsset.book_id == book.book_id, MediaAsset.kind == kind))
    ).scalar_one_or_none()


def _extension(filename: str) -> str:
    extension = Path(filename).suffix.lower().lstrip(".")
    if not extension:
        raise ValidationError("Uploaded file must include a file extension")
    return extension


def _validate_media_type(kind: str, extension: str, content_type: str) -> None:
    if kind == "book_file" and extension not in BOOK_EXTENSIONS:
        raise ValidationError("Unsupported book file type")
    if kind == "cover_image" and (extension not in COVER_EXTENSIONS or not content_type.startswith("image/")):
        raise ValidationError("Unsupported cover image type")


def delete_physical_file(asset: MediaAsset) -> None:
    _delete_path(asset_path(asset))


def delete_physical_paths(paths: list[Path]) -> None:
    for path in paths:
        _delete_path(path)


def _delete_path(path: Path) -> None:
    if path.exists():
        path.unlink()


def _delete_empty_parent_dirs(path: Path, *, stop_at: Path) -> None:
    current = path
    stop = stop_at.resolve()
    while current.resolve() != stop:
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent
