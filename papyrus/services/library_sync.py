"""Ownership, references, and offline deletion semantics for library mutations."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.core.exceptions import ForbiddenError, ValidationError
from papyrus.models import (
    SyncAnnotation,
    SyncBook,
    SyncBookShelf,
    SyncBookTag,
    SyncNote,
    SyncShelf,
    SyncTag,
    SyncTombstone,
)
from papyrus.schemas.sync import PowerSyncCrudMutation
from papyrus.services import media as media_service
from papyrus.services.library_validation import convert_value, normalize_book_payload, uuid_value

MODELS: dict[str, Any] = {
    "books": SyncBook,
    "shelves": SyncShelf,
    "tags": SyncTag,
    "notes": SyncNote,
    "annotations": SyncAnnotation,
    "book_shelves": SyncBookShelf,
    "book_tags": SyncBookTag,
}
PRIMARY_KEYS = {
    "books": "book_id",
    "shelves": "shelf_id",
    "tags": "tag_id",
    "notes": "note_id",
    "annotations": "annotation_id",
    "book_shelves": "id",
    "book_tags": "id",
}
REFERENCES = {"book_id": "books", "shelf_id": "shelves", "tag_id": "tags", "parent_shelf_id": "shelves"}
MEMBERSHIPS = {"book_shelves", "book_tags"}


async def owned_row(session: AsyncSession, user_id: UUID, table: str, row_id: UUID | str) -> Any:
    row = await session.get(MODELS[table], row_id)

    if row is not None and row.owner_user_id != user_id:
        raise ForbiddenError(f"Cannot access another user's {table} row")

    return row


async def tombstoned(session: AsyncSession, user_id: UUID, table: str, row_id: UUID) -> bool:
    marker = await session.get(SyncTombstone, (table, row_id))

    if marker is not None and marker.owner_user_id != user_id:
        raise ForbiddenError("Cannot access another user's deleted entity")

    return marker is not None


async def mark_deleted(session: AsyncSession, user_id: UUID, table: str, row_id: UUID) -> None:
    if not await tombstoned(session, user_id, table, row_id):
        session.add(SyncTombstone(table_name=table, entity_id=row_id, owner_user_id=user_id))
        await session.flush()


async def delete_entity(session: AsyncSession, user_id: UUID, table: str, row_id: UUID, row: Any) -> list[Path]:
    """Record deletion before cascading so delayed entity writes cannot revive it."""
    await mark_deleted(session, user_id, table, row_id)
    paths: list[Path] = []

    if row is None:
        return paths

    if table == "books":
        for child_table in ("notes", "annotations"):
            model = MODELS[child_table]
            result = await session.execute(select(model).where(model.book_id == row_id))

            for child in result.scalars():
                await mark_deleted(session, user_id, child_table, getattr(child, PRIMARY_KEYS[child_table]))

        paths = await media_service.delete_book_media(session, user_id, row_id)

    if table == "shelves":
        await session.execute(
            update(SyncShelf)
            .where(SyncShelf.parent_shelf_id == row_id)
            .values(parent_shelf_id=None, updated_at=datetime.now(UTC))
        )

    await session.delete(row)
    await session.flush()
    return paths


async def validate_references(
    session: AsyncSession,
    user_id: UUID,
    table: str,
    row_id: UUID | str,
    values: dict[str, Any],
    row: Any,
    *,
    deleting: bool = False,
) -> bool:
    """Check every reference even when another parent has a deletion marker."""
    stale = False

    for field, target in REFERENCES.items():
        if field not in MODELS[table].__table__.columns or field == PRIMARY_KEYS[table]:
            continue

        ref = values.get(field, getattr(row, field, None))

        if ref is None:
            continue

        parent = await owned_row(session, user_id, target, ref)
        deleted_parent = await tombstoned(session, user_id, target, ref)
        stale |= deleted_parent

        if parent is None and not deleted_parent and not deleting:
            raise ValidationError(f"{field} was not found")

        if field == "parent_shelf_id" and not deleted_parent and not deleting:
            visited = {row_id}

            while parent is not None:
                if parent.shelf_id in visited:
                    raise ValidationError("Shelf hierarchy cannot contain cycles")

                visited.add(parent.shelf_id)
                parent = (
                    await owned_row(session, user_id, "shelves", parent.parent_shelf_id)
                    if parent.parent_shelf_id is not None
                    else None
                )

    return stale


def membership_values(table: str, raw_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    parts = raw_id.split(":")

    if len(parts) != 2:
        raise ValidationError("Membership id must be '<book_uuid>:<target_uuid>'")

    book_id, target_id = (uuid_value(part, "id") for part in parts)

    if raw_id != f"{book_id}:{target_id}":
        raise ValidationError("Membership id must use canonical UUIDs")

    target_key = "shelf_id" if table == "book_shelves" else "tag_id"
    pair = {"book_id": book_id, target_key: target_id}

    for key, expected in pair.items():
        if key in payload and uuid_value(payload[key], key) != expected:
            raise ValidationError("Membership fields must match its deterministic id")

    return {**payload, **pair}


async def apply_library_mutation(
    session: AsyncSession,
    user_id: UUID,
    mutation: PowerSyncCrudMutation,
) -> tuple[int, list[Path]]:
    table = mutation.table
    model = MODELS[table]
    is_membership = table in MEMBERSHIPS
    row_id = mutation.id if is_membership else uuid_value(mutation.id, "id")
    payload = dict(mutation.op_data or {})
    payload = {key: value for key, value in payload.items() if key not in {"owner_user_id", "updated_at"}}

    if table == "books":
        payload = normalize_book_payload(payload)

    if is_membership:
        payload = membership_values(table, mutation.id, payload)

    row = await owned_row(session, user_id, table, row_id)

    if not is_membership and await tombstoned(session, user_id, table, uuid_value(row_id, "id")):
        return 0, []

    if mutation.op.upper() == "PATCH" and row is None:
        return 0, []

    if mutation.op.upper() == "DELETE":
        if is_membership:
            await validate_references(session, user_id, table, row_id, payload, row, deleting=True)

            if row is not None:
                await session.execute(delete(model).where(model.id == row_id))

            return int(row is not None), []

        paths = await delete_entity(session, user_id, table, uuid_value(row_id, "id"), row)
        return int(row is not None), paths

    values = {key: convert_value(model.__table__.columns[key], value) for key, value in payload.items()}
    stale_parent = await validate_references(session, user_id, table, row_id, values, row)

    if stale_parent:
        return 0, []

    if table == "annotations" and values.get("color", "yellow") not in {
        "yellow",
        "green",
        "blue",
        "pink",
        "purple",
        "orange",
    }:
        raise ValidationError("Unsupported annotation color")

    if table == "books":
        for key, kind in (("file_media_id", "book_file"), ("cover_media_id", "cover_image")):
            if key in values:
                values[key] = await media_service.validate_media_reference(
                    session, user_id, uuid_value(row_id, "id"), values[key], field_name=key, expected_kind=kind
                )

    if row is None:
        if table == "books":
            values.setdefault("title", "Untitled Book")

        for column in model.__table__.columns:
            if (
                not column.nullable
                and not column.primary_key
                and column.name != "owner_user_id"
                and column.default is None
                and column.server_default is None
                and column.name not in values
            ):
                raise ValidationError(f"{column.name} is required")

        row = model(**{PRIMARY_KEYS[table]: row_id, "owner_user_id": user_id}, **values)
        session.add(row)
    else:
        for key, value in values.items():
            setattr(row, key, value)

    if "updated_at" in model.__table__.columns:
        row.updated_at = datetime.now(UTC)

    await session.flush()
    return 1, []
