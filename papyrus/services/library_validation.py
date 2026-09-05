"""Type conversion and backwards compatibility for library queue payloads."""

from datetime import UTC, datetime
from math import isfinite
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, Float, Integer, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB

from papyrus.core.exceptions import ValidationError
from papyrus.models.sync import SyncBook

PROMOTED_BOOK_FIELDS = frozenset(
    {
        "publication_date",
        "file_format",
        "file_size",
        "file_hash",
        "is_physical",
        "physical_location",
        "lent_to",
        "lent_at",
        "series_id",
        "series_name",
        "series_number",
        "started_at",
        "completed_at",
        "last_read_at",
    }
)


def uuid_value(value: object, name: str) -> UUID:
    try:
        return UUID(str(value))
    except ValueError as exc:
        raise ValidationError(f"{name} must be a valid UUID") from exc


def normalize_book_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep the legacy envelope intact while promoting queued legacy values."""
    envelope = payload.get("custom_metadata")

    if isinstance(envelope, dict):
        promoted = {}

        for key, value in envelope.items():
            if key not in PROMOTED_BOOK_FIELDS or key in payload:
                continue

            try:
                convert_value(SyncBook.__table__.columns[key], value)
            except ValidationError:
                continue

            promoted[key] = value

        return {**promoted, **payload}

    return payload


def finite_number(value: Any) -> bool:
    if not isinstance(value, int | float) or isinstance(value, bool):
        return False

    try:
        return isfinite(value)
    except OverflowError:
        return False


def validate_location(value: dict[str, Any]) -> dict[str, Any]:
    if value.keys() - {"chapter", "chapter_title", "page_number", "percentage"}:
        raise ValidationError("Unsupported location fields")

    page = value.get("page_number")

    if not isinstance(page, int) or isinstance(page, bool):
        raise ValidationError("location.page_number must be an integer")

    chapter = value.get("chapter")

    if chapter is not None and (not isinstance(chapter, int) or isinstance(chapter, bool)):
        raise ValidationError("location.chapter must be an integer")

    title = value.get("chapter_title")

    if title is not None and not isinstance(title, str):
        raise ValidationError("location.chapter_title must be text")

    percentage = value.get("percentage")

    if percentage is not None and not finite_number(percentage):
        raise ValidationError("location.percentage must be a finite number")

    return value


def convert_value(column: Any, value: Any) -> Any:
    """Validate each present value without manufacturing absent PATCH fields."""
    key = column.name

    if value is None:
        if not column.nullable:
            raise ValidationError(f"{key} cannot be null")

        return None

    column_type = column.type

    if isinstance(column_type, Uuid):
        return uuid_value(value, key)

    if isinstance(column_type, Boolean):
        if value in (True, False, 0, 1) and isinstance(value, bool | int):
            return bool(value)

        raise ValidationError(f"{key} must be a boolean")

    if isinstance(column_type, Integer | BigInteger):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValidationError(f"{key} must be an integer")

        limit = 2**63 if isinstance(column_type, BigInteger) else 2**31

        if not -limit <= value < limit:
            raise ValidationError(f"{key} is out of range")

        return value

    if isinstance(column_type, Float):
        if not finite_number(value):
            raise ValidationError(f"{key} must be a finite number")

        return float(value)

    if isinstance(column_type, DateTime):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)
        except (ValueError, OverflowError) as exc:
            raise ValidationError(f"{key} must be an ISO datetime") from exc

    if isinstance(column_type, JSONB):
        if key in {"tags", "co_authors"}:
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise ValidationError(f"{key} must be a list of strings")

            return value

        if not isinstance(value, dict):
            raise ValidationError(f"{key} must be an object")

        return validate_location(value) if key == "location" else value

    if isinstance(column_type, String):
        if not isinstance(value, str):
            raise ValidationError(f"{key} must be text")

        if column_type.length is not None and len(value) > column_type.length:
            raise ValidationError(f"{key} is too long")

        return value

    raise ValidationError(f"Unsupported field: {key}")
