"""Services for the PowerSync sandbox demo."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.core.exceptions import ForbiddenError, ValidationError
from papyrus.models import PowerSyncDemoItem
from papyrus.schemas.powersync_demo import PowerSyncUploadMutation


def _normalize_operation(value: str) -> str:
    return value.upper()


def _now() -> datetime:
    return datetime.now(UTC)


async def list_demo_items(session: AsyncSession, user_id: UUID) -> list[PowerSyncDemoItem]:
    """Return demo items owned by the authenticated user."""
    result = await session.execute(
        select(PowerSyncDemoItem)
        .where(PowerSyncDemoItem.owner_user_id == user_id)
        .order_by(PowerSyncDemoItem.updated_at.desc(), PowerSyncDemoItem.item_id.desc())
    )

    return list(result.scalars())


async def apply_upload_batch(session: AsyncSession, user_id: UUID, batch: list[PowerSyncUploadMutation]) -> int:
    """Apply a batch of PowerSync CRUD operations to the source database."""
    applied_count = 0

    for mutation in batch:
        if mutation.table != "demo_items":
            raise ValidationError("Unsupported PowerSync upload table")

        operation = _normalize_operation(mutation.op)

        if operation == "PUT":
            applied_count += await _apply_put(session, user_id, mutation)
            continue

        if operation == "PATCH":
            applied_count += await _apply_patch(session, user_id, mutation)
            continue

        if operation == "DELETE":
            applied_count += await _apply_delete(session, user_id, mutation)
            continue

        raise ValidationError("Unsupported PowerSync upload operation")

    await session.commit()
    return applied_count


async def _apply_put(session: AsyncSession, user_id: UUID, mutation: PowerSyncUploadMutation) -> int:
    item_id = UUID(mutation.id)
    item = await session.get(PowerSyncDemoItem, item_id)
    payload = mutation.op_data or {}
    now = _now()

    if item is not None and item.owner_user_id != user_id:
        raise ForbiddenError("Cannot modify another user's demo item")

    if item is None:
        item = PowerSyncDemoItem(
            item_id=item_id,
            owner_user_id=user_id,
            title=str(payload.get("title") or "Untitled Item"),
            notes=_coerce_optional_text(payload.get("notes")),
            created_at=now,
            updated_at=now,
        )

        session.add(item)
        return 1

    item.title = str(payload.get("title") or item.title)
    item.notes = _coerce_optional_text(payload.get("notes"), default=item.notes)
    item.updated_at = now
    return 1


async def _apply_patch(session: AsyncSession, user_id: UUID, mutation: PowerSyncUploadMutation) -> int:
    item = await session.get(PowerSyncDemoItem, UUID(mutation.id))

    if item is None:
        return 0

    if item.owner_user_id != user_id:
        raise ForbiddenError("Cannot modify another user's demo item")

    payload = mutation.op_data or {}

    if "title" in payload and payload["title"] is not None:
        item.title = str(payload["title"])

    if "notes" in payload:
        item.notes = _coerce_optional_text(payload["notes"])

    item.updated_at = _now()
    return 1


async def _apply_delete(session: AsyncSession, user_id: UUID, mutation: PowerSyncUploadMutation) -> int:
    item = await session.get(PowerSyncDemoItem, UUID(mutation.id))

    if item is None:
        return 0

    if item.owner_user_id != user_id:
        raise ForbiddenError("Cannot delete another user's demo item")

    await session.delete(item)
    return 1


def _coerce_optional_text(value: object, *, default: str | None = None) -> str | None:
    if value is None:
        return default if default is not None else None

    return str(value)
