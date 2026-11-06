"""Resolve timer branches under the upload owner's lock before applying their effects."""

from datetime import UTC, datetime
from uuid import UUID, uuid5

from pydantic import ValidationError as PayloadError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.core.exceptions import ForbiddenError, ValidationError
from papyrus.models import SyncReadingTimer
from papyrus.schemas.reading_timer import TimerPayload
from papyrus.schemas.sync import PowerSyncCrudMutation
from papyrus.schemas.tracking import Activity
from papyrus.services.library_sync import tombstoned
from papyrus.services.tracking_validation import check_reference

BOOK_PROGRESS_FIELDS = {
    "owner_user_id",
    "updated_at",
    "current_page",
    "current_position",
    "last_read_at",
    "started_at",
    "reading_status",
    "completed_at",
}


def validate_effects(batch: list[PowerSyncCrudMutation], timer: TimerPayload, status: str) -> None:
    """A terminal timer and its ledger/progress writes form one indivisible transaction."""
    effects = [mutation for mutation in batch if mutation.table != "reading_timers"]

    if status != "saved":
        if effects:
            raise ValidationError("Only saving a timer may write reading history")

        return

    if timer.phase != "finished" or sum((part.end - part.start).total_seconds() for part in timer.intervals) < 1:
        raise ValidationError("Saved timers require at least one second of finished reading")

    expected = {uuid5(timer.id, str(index)): part for index, part in enumerate(timer.intervals)}
    seen: set[UUID] = set()

    for mutation in effects:
        if mutation.op.upper() == "DELETE":
            raise ValidationError("Saving a timer cannot delete library data")

        if mutation.table == "books":
            if (
                mutation.id != str(timer.book_id)
                or mutation.op.upper() != "PATCH"
                or (mutation.op_data or {}).keys() - BOOK_PROGRESS_FIELDS
            ):
                raise ValidationError("Timer save may only update its book's progress")

            continue

        if mutation.table != "reading_activities":
            raise ValidationError("Unexpected timer save effect")

        activity = Activity.model_validate((mutation.op_data or {}).get("payload"))

        if activity.id in seen or str(activity.id) != mutation.id or activity.book_id != timer.book_id:
            raise ValidationError("Timer activity identity does not match its session")

        seen.add(activity.id)

        if activity.kind == "completion" and activity.id == timer.id:
            continue

        interval = expected.get(activity.id)

        if (
            interval is None
            or activity.session_id != timer.id
            or activity.kind != "reading"
            or activity.start_time != interval.start
            or activity.end_time != interval.end
        ):
            raise ValidationError("Saved reading intervals must match the timer")

    if not expected.keys() <= seen:
        raise ValidationError("Timer save is missing reading intervals")


async def apply_timer_transaction(
    session: AsyncSession,
    user_id: UUID,
    batch: list[PowerSyncCrudMutation],
) -> bool:
    """First accepted branch wins; terminal rows prevent retries from recording time twice."""
    mutations = [mutation for mutation in batch if mutation.table == "reading_timers"]

    if len(mutations) != 1:
        raise ValidationError("A timer transaction must contain exactly one state transition")

    mutation = mutations[0]
    values = mutation.op_data or {}

    if mutation.op.upper() == "DELETE":
        raise ValidationError("Close timers with a saved or discarded state")

    try:
        timer = TimerPayload.model_validate(values.get("payload"))
        row_id = UUID(mutation.id)
    except (PayloadError, ValueError) as exc:
        raise ValidationError(str(exc)) from exc

    if timer.id != row_id:
        raise ValidationError("Timer payload id must match row id")

    row = await session.get(SyncReadingTimer, row_id, populate_existing=True)

    if row is not None and row.owner_user_id != user_id:
        raise ForbiddenError("Cannot access another user's timer")

    status = values.get("status", row.status if row is not None else None)
    base = values.get("base_version")

    if not isinstance(status, str) or status not in {"active", "saved", "discarded"}:
        raise ValidationError("Invalid timer status")

    if base is not None and (not isinstance(base, str) or not base or len(base) > 100):
        raise ValidationError("Invalid timer base version")

    if row is not None:
        previous = TimerPayload.model_validate(row.payload)

        if row.status != "active" or timer.version == previous.version or base != previous.version:
            return False

        fixed = ("book_id", "book_title", "started_at", "device_id", "shelf_ids", "start_page")

        if (
            any(getattr(previous, field) != getattr(timer, field) for field in fixed)
            or timer.revision <= previous.revision
            or timer.intervals[: len(previous.intervals)] != previous.intervals
        ):
            raise ValidationError("Timer transitions cannot rewrite their identity or recorded intervals")
    elif base is not None or status != "active":
        return False

    try:
        validate_effects(batch, timer, status)
    except PayloadError as exc:
        raise ValidationError(str(exc)) from exc

    await check_reference(session, user_id, "books", timer.book_id)

    for shelf_id in timer.shelf_ids:
        await check_reference(session, user_id, "shelves", shelf_id)

    deleted_book = await tombstoned(session, user_id, "books", timer.book_id)

    if deleted_book and status != "discarded":
        status = "discarded"

    accepted = not deleted_book or values.get("status") == "discarded"

    if row is None:
        active = await session.scalar(
            select(SyncReadingTimer.id).where(
                SyncReadingTimer.owner_user_id == user_id,
                SyncReadingTimer.status == "active",
            )
        )

        if active is not None:
            status = "discarded"
            accepted = False

        row = SyncReadingTimer(id=row_id, owner_user_id=user_id)
        session.add(row)

    row.payload = timer.model_dump(mode="json")
    row.status = status
    row.base_version = base
    row.updated_at = datetime.now(UTC)
    await session.flush()
    return accepted
