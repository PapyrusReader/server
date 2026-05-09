"""Service-layer sync and PowerSync upload logic."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.core.exceptions import ForbiddenError, ValidationError
from papyrus.models import SyncAnnotation, SyncBook, SyncReadingSession
from papyrus.schemas.sync import PowerSyncCrudMutation


def _now() -> datetime:
    return datetime.now(UTC)


def _operation(value: str) -> str:
    return value.upper()


def _uuid(value: object, field_name: str) -> UUID:
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise ValidationError(f"{field_name} must be a valid UUID") from exc


def _optional_text(payload: dict[str, object], key: str, default: str | None = None) -> str | None:
    if key not in payload:
        return default

    value = payload[key]

    if value is None:
        return None

    return str(value)


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

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return int(value)

    if not isinstance(value, str):
        raise ValidationError(f"{key} must be an integer")

    try:
        return int(value)
    except ValueError as exc:
        raise ValidationError(f"{key} must be an integer") from exc


def _optional_float(payload: dict[str, object], key: str, default: float | None = None) -> float | None:
    if key not in payload:
        return default

    value = payload[key]

    if value is None:
        return None

    if isinstance(value, int | float):
        return float(value)

    if not isinstance(value, str):
        raise ValidationError(f"{key} must be a number")

    try:
        return float(value)
    except ValueError as exc:
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


def _optional_datetime(payload: dict[str, object], key: str, default: datetime | None = None) -> datetime | None:
    if key not in payload:
        return default

    value = payload[key]

    if value is None:
        return None

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
    payload: dict[str, object],
    key: str,
    default: dict[str, object] | None = None,
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
    """Apply a PowerSync CRUD batch to production source tables."""
    applied_count = 0

    for mutation in batch:
        table = mutation.table
        operation = _operation(mutation.op)

        if table == "books":
            applied_count += await _apply_book_mutation(session, user_id, mutation, operation)
            continue

        if table == "annotations":
            applied_count += await _apply_annotation_mutation(session, user_id, mutation, operation)
            continue

        if table == "reading_sessions":
            applied_count += await _apply_reading_session_mutation(session, user_id, mutation, operation)
            continue

        raise ValidationError("Unsupported PowerSync upload table")

    await session.commit()
    return applied_count


async def _apply_book_mutation(
    session: AsyncSession,
    user_id: UUID,
    mutation: PowerSyncCrudMutation,
    operation: str,
) -> int:
    book_id = _uuid(mutation.id, "id")

    if operation == "DELETE":
        book = await _get_owned_book(session, user_id, book_id)

        if book is None:
            return 0

        await session.delete(book)
        return 1

    if operation not in {"PUT", "PATCH"}:
        raise ValidationError("Unsupported PowerSync upload operation")

    payload = mutation.op_data or {}
    book = await _get_owned_book(session, user_id, book_id)
    now = _now()

    if book is None:
        book = SyncBook(
            book_id=book_id,
            owner_user_id=user_id,
            title=_required_text(payload, "title", "Untitled Book"),
            added_at=_optional_datetime(payload, "added_at", now) or now,
            updated_at=_optional_datetime(payload, "updated_at", now) or now,
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
    book.reading_status = _optional_text(payload, "reading_status", book.reading_status)
    book.current_page = _optional_int(payload, "current_page", book.current_page)
    book.current_position = _optional_float(payload, "current_position", book.current_position)
    book.current_cfi = _optional_text(payload, "current_cfi", book.current_cfi)
    book.is_favorite = _optional_bool(payload, "is_favorite", book.is_favorite)
    book.rating = _optional_int(payload, "rating", book.rating)
    book.custom_metadata = _optional_json_object(payload, "custom_metadata", book.custom_metadata)
    book.updated_at = _optional_datetime(payload, "updated_at", now) or now
    return 1


async def _apply_annotation_mutation(
    session: AsyncSession,
    user_id: UUID,
    mutation: PowerSyncCrudMutation,
    operation: str,
) -> int:
    annotation_id = _uuid(mutation.id, "id")

    if operation == "DELETE":
        annotation = await session.get(SyncAnnotation, annotation_id)

        if annotation is None:
            return 0

        if annotation.owner_user_id != user_id:
            raise ForbiddenError("Cannot delete another user's annotation")

        await session.delete(annotation)
        return 1

    if operation not in {"PUT", "PATCH"}:
        raise ValidationError("Unsupported PowerSync upload operation")

    payload = mutation.op_data or {}
    annotation = await session.get(SyncAnnotation, annotation_id)
    now = _now()

    if annotation is not None and annotation.owner_user_id != user_id:
        raise ForbiddenError("Cannot modify another user's annotation")

    book_id = _uuid(
        payload.get("book_id") if annotation is None else payload.get("book_id", annotation.book_id), "book_id"
    )
    await _require_owned_book(session, user_id, book_id)

    if annotation is None:
        annotation = SyncAnnotation(
            annotation_id=annotation_id,
            owner_user_id=user_id,
            book_id=book_id,
            selected_text=_required_text(payload, "selected_text"),
            highlight_color=_required_text(payload, "highlight_color", "#FFEB3B"),
            start_position=_required_text(payload, "start_position"),
            end_position=_required_text(payload, "end_position"),
            created_at=_optional_datetime(payload, "created_at", now) or now,
            updated_at=_optional_datetime(payload, "updated_at", now) or now,
        )
        session.add(annotation)

    annotation.book_id = book_id
    annotation.selected_text = _required_text(payload, "selected_text", annotation.selected_text)
    annotation.note = _optional_text(payload, "note", annotation.note)
    annotation.highlight_color = _required_text(payload, "highlight_color", annotation.highlight_color)
    annotation.start_position = _required_text(payload, "start_position", annotation.start_position)
    annotation.end_position = _required_text(payload, "end_position", annotation.end_position)
    annotation.chapter_title = _optional_text(payload, "chapter_title", annotation.chapter_title)
    annotation.chapter_index = _optional_int(payload, "chapter_index", annotation.chapter_index)
    annotation.page_number = _optional_int(payload, "page_number", annotation.page_number)
    annotation.updated_at = _optional_datetime(payload, "updated_at", now) or now
    return 1


async def _apply_reading_session_mutation(
    session: AsyncSession,
    user_id: UUID,
    mutation: PowerSyncCrudMutation,
    operation: str,
) -> int:
    session_id = _uuid(mutation.id, "id")

    if operation == "DELETE":
        reading_session = await session.get(SyncReadingSession, session_id)

        if reading_session is None:
            return 0

        if reading_session.owner_user_id != user_id:
            raise ForbiddenError("Cannot delete another user's reading session")

        await session.delete(reading_session)
        return 1

    if operation not in {"PUT", "PATCH"}:
        raise ValidationError("Unsupported PowerSync upload operation")

    payload = mutation.op_data or {}
    reading_session = await session.get(SyncReadingSession, session_id)
    now = _now()

    if reading_session is not None and reading_session.owner_user_id != user_id:
        raise ForbiddenError("Cannot modify another user's reading session")

    book_id = _uuid(
        payload.get("book_id") if reading_session is None else payload.get("book_id", reading_session.book_id),
        "book_id",
    )
    await _require_owned_book(session, user_id, book_id)

    if reading_session is None:
        start_time = _optional_datetime(payload, "start_time")

        if start_time is None:
            raise ValidationError("start_time is required")

        reading_session = SyncReadingSession(
            session_id=session_id,
            owner_user_id=user_id,
            book_id=book_id,
            start_time=start_time,
            created_at=_optional_datetime(payload, "created_at", now) or now,
        )
        session.add(reading_session)

    reading_session.book_id = book_id
    reading_session.start_time = (
        _optional_datetime(payload, "start_time", reading_session.start_time) or reading_session.start_time
    )
    reading_session.end_time = _optional_datetime(payload, "end_time", reading_session.end_time)
    reading_session.start_position = _optional_float(payload, "start_position", reading_session.start_position)
    reading_session.end_position = _optional_float(payload, "end_position", reading_session.end_position)
    reading_session.pages_read = _optional_int(payload, "pages_read", reading_session.pages_read)
    reading_session.duration_minutes = _optional_int(payload, "duration_minutes", reading_session.duration_minutes)
    reading_session.device_type = _optional_text(payload, "device_type", reading_session.device_type)
    reading_session.device_name = _optional_text(payload, "device_name", reading_session.device_name)
    return 1


async def _require_owned_book(session: AsyncSession, user_id: UUID, book_id: UUID) -> SyncBook:
    book = await _get_owned_book(session, user_id, book_id)

    if book is None:
        raise ValidationError("Referenced book does not exist")

    return book
