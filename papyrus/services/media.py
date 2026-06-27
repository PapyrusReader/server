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
from papyrus.models import MediaAsset, SyncBook

BOOK_EXTENSIONS = {"epub", "pdf", "mobi", "azw3", "txt", "cbr", "cbz"}
COVER_EXTENSIONS = {"jpg", "jpeg", "png", "webp", "gif"}
MEDIA_KINDS = {"book_file", "cover_image"}


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

    book = await session.get(SyncBook, book_id)
    if book is None:
        raise NotFoundError("Book was not found")
    if book.owner_user_id != user_id:
        raise ForbiddenError("Cannot access another user's book")

    filename = file.filename or "upload"
    extension = _extension(filename)
    content_type = file.content_type or "application/octet-stream"
    _validate_media_type(kind, extension, content_type)

    content = await file.read()
    if not content:
        raise ValidationError("Uploaded file is empty")

    existing = await _existing_asset_for_kind(session, book, kind)
    used = await _used_bytes(session, user_id)
    used_without_existing = used - (existing.size_bytes if existing is not None else 0)
    quota = get_settings().file_storage_quota_bytes
    if used_without_existing + len(content) > quota:
        raise ConflictError("Storage quota exceeded")

    asset_id = uuid4()
    storage_path = f"{user_id}/{book_id}/{asset_id}.{extension}"
    absolute_path = media_root() / storage_path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    absolute_path.write_bytes(content)

    asset = MediaAsset(
        asset_id=asset_id,
        owner_user_id=user_id,
        book_id=book_id,
        kind=kind,
        original_filename=filename,
        content_type=content_type,
        extension=extension,
        size_bytes=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
        storage_path=storage_path,
    )
    session.add(asset)

    if kind == "book_file":
        book.file_media_id = asset.asset_id
    else:
        book.cover_media_id = asset.asset_id

    if existing is not None:
        await session.delete(existing)
        _delete_physical_file(existing)

    await session.commit()
    await session.refresh(asset)
    return asset


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
    _delete_physical_file(asset)
    await session.commit()


async def delete_book_media(session: AsyncSession, user_id: UUID, book_id: UUID) -> None:
    result = await session.execute(
        select(MediaAsset).where(MediaAsset.owner_user_id == user_id, MediaAsset.book_id == book_id)
    )
    for asset in result.scalars():
        await session.delete(asset)
        _delete_physical_file(asset)


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
    result = await session.execute(select(func.coalesce(func.sum(MediaAsset.size_bytes), 0)).where(MediaAsset.owner_user_id == user_id))
    return int(result.scalar_one())


async def _existing_asset_for_kind(session: AsyncSession, book: SyncBook, kind: str) -> MediaAsset | None:
    asset_id = book.file_media_id if kind == "book_file" else book.cover_media_id
    if asset_id is None:
        return None
    return await session.get(MediaAsset, asset_id)


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


def _delete_physical_file(asset: MediaAsset) -> None:
    path = asset_path(asset)
    if path.exists():
        path.unlink()
