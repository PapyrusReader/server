"""Books-only PowerSync upload service."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.core.exceptions import ForbiddenError, ValidationError
from papyrus.models import SyncBook
from papyrus.schemas.sync import PowerSyncCrudMutation
from papyrus.services import media as media_service

BOOK_FIELDS = frozenset(
    {
        "title",
        "subtitle",
        "author",
        "co_authors",
        "isbn",
        "isbn13",
        "publisher",
        "language",
        "page_count",
        "description",
        "cover_image_url",
        "file_media_id",
        "cover_media_id",
        "reading_status",
        "current_page",
        "current_position",
        "current_cfi",
        "is_favorite",
        "rating",
        "custom_metadata",
        "added_at",
        "owner_user_id",
        "updated_at",
    }
)
SERVER_CONTROLLED_FIELDS = frozenset({"owner_user_id", "updated_at"})


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid(value: object, field_name: str) -> UUID:
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise ValidationError(f"{field_name} must be a valid UUID") from exc


def _validate_payload(payload: dict[str, object]) -> dict[str, object]:
    unknown = payload.keys() - BOOK_FIELDS
    if unknown:
        raise ValidationError(f"Unsupported book fields: {', '.join(sorted(unknown))}")
    return {key: value for key, value in payload.items() if key not in SERVER_CONTROLLED_FIELDS}


def _optional_text(payload: dict[str, object], key: str, default: str | None = None) -> str | None:
    if key not in payload:
        return default
    value = payload[key]
    return None if value is None else str(value)


def _optional_uuid(payload: dict[str, object], key: str, default: UUID | None = None) -> UUID | None:
    if key not in payload:
        return default
    value = payload[key]
    if value is None:
        return None
    return _uuid(value, key)


def _required_text(payload: dict[str, object], key: str, default: str | None = None) -> str:
    value = _optional_text(payload, key, default)
    if value is None or not value:
        raise ValidationError(f"{key} is required")
    return value


def _optional_int(payload: dict[str, object], key: str, default: int | None = None) -> int | None:
    if key not in payload:
        return default
    value = payload[key]
    if value is None:
        return None
    if not isinstance(value, int | float | str) or isinstance(value, bool):
        raise ValidationError(f"{key} must be an integer")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{key} must be an integer") from exc


def _optional_float(payload: dict[str, object], key: str, default: float | None = None) -> float | None:
    if key not in payload:
        return default
    value = payload[key]
    if value is None:
        return None
    if not isinstance(value, int | float | str) or isinstance(value, bool):
        raise ValidationError(f"{key} must be a number")
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{key} must be a number") from exc


def _optional_bool(payload: dict[str, object], key: str, default: bool = False) -> bool:
    if key not in payload:
        return default
    value = payload[key]
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _optional_datetime(payload: dict[str, object], key: str, default: datetime) -> datetime:
    if key not in payload:
        return default
    value = payload[key]
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{key} must be an ISO datetime") from exc


def _optional_string_list(payload: dict[str, object], key: str, default: list[str] | None = None) -> list[str] | None:
    if key not in payload:
        return default
    value = payload[key]
    if value is None:
        return None
    if not isinstance(value, list):
        raise ValidationError(f"{key} must be a list")
    return [str(item) for item in value]


def _optional_json_object(
    payload: dict[str, object], key: str, default: dict[str, object] | None = None
) -> dict[str, object] | None:
    if key not in payload:
        return default
    value = payload[key]
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValidationError(f"{key} must be an object")
    return value


async def _get_owned_book(session: AsyncSession, user_id: UUID, book_id: UUID) -> SyncBook | None:
    book = await session.get(SyncBook, book_id)
    if book is not None and book.owner_user_id != user_id:
        raise ForbiddenError("Cannot access another user's book")
    return book


async def apply_powersync_upload_batch(
    session: AsyncSession,
    user_id: UUID,
    batch: list[PowerSyncCrudMutation],
) -> int:
    """Apply one PowerSync CRUD transaction and commit it atomically."""
    applied_count = 0
    try:
        for mutation in batch:
            applied_count += await _apply_book_mutation(session, user_id, mutation)
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    return applied_count


async def _apply_book_mutation(
    session: AsyncSession,
    user_id: UUID,
    mutation: PowerSyncCrudMutation,
) -> int:
    book_id = _uuid(mutation.id, "id")
    operation = mutation.op.upper()

    if operation == "DELETE":
        book = await _get_owned_book(session, user_id, book_id)
        if book is None:
            return 0
        await media_service.delete_book_media(session, user_id, book_id)
        await session.delete(book)
        return 1

    payload = _validate_payload(mutation.op_data or {})
    book = await _get_owned_book(session, user_id, book_id)
    now = _now()

    if book is None:
        book = SyncBook(
            book_id=book_id,
            owner_user_id=user_id,
            title=_required_text(payload, "title", "Untitled Book"),
            added_at=_optional_datetime(payload, "added_at", now),
            updated_at=now,
        )
        session.add(book)

    book.title = _required_text(payload, "title", book.title)
    book.subtitle = _optional_text(payload, "subtitle", book.subtitle)
    book.author = _optional_text(payload, "author", book.author)
    book.co_authors = _optional_string_list(payload, "co_authors", book.co_authors)
    book.isbn = _optional_text(payload, "isbn", book.isbn)
    book.isbn13 = _optional_text(payload, "isbn13", book.isbn13)
    book.publisher = _optional_text(payload, "publisher", book.publisher)
    book.language = _optional_text(payload, "language", book.language)
    book.page_count = _optional_int(payload, "page_count", book.page_count)
    book.description = _optional_text(payload, "description", book.description)
    book.cover_image_url = _optional_text(payload, "cover_image_url", book.cover_image_url)
    book.file_media_id = await media_service.validate_media_reference(
        session,
        user_id,
        book.book_id,
        _optional_uuid(payload, "file_media_id", book.file_media_id),
        field_name="file_media_id",
        expected_kind="book_file",
    )
    book.cover_media_id = await media_service.validate_media_reference(
        session,
        user_id,
        book.book_id,
        _optional_uuid(payload, "cover_media_id", book.cover_media_id),
        field_name="cover_media_id",
        expected_kind="cover_image",
    )
    book.reading_status = _optional_text(payload, "reading_status", book.reading_status)
    book.current_page = _optional_int(payload, "current_page", book.current_page)
    book.current_position = _optional_float(payload, "current_position", book.current_position)
    book.current_cfi = _optional_text(payload, "current_cfi", book.current_cfi)
    book.is_favorite = _optional_bool(payload, "is_favorite", book.is_favorite)
    book.rating = _optional_int(payload, "rating", book.rating)
    book.custom_metadata = _optional_json_object(payload, "custom_metadata", book.custom_metadata)
    book.updated_at = now
    return 1
