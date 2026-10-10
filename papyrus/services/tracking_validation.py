"""Tracking ownership, immutable activity, and rule-history validation."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import ValidationError as PayloadError
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.core.exceptions import ValidationError
from papyrus.schemas.tracking import Activity, GoalDefinition, GoalRule, PeriodRecord
from papyrus.services.library_sync import owned_row, tombstoned


async def validate_tracking_mutation(
    session: AsyncSession,
    user_id: UUID,
    table: str,
    row_id: UUID | str,
    values: dict[str, Any],
    row: Any,
) -> dict[str, Any]:
    schemas: dict[str, type[GoalDefinition] | type[Activity] | type[PeriodRecord]] = {
        "reading_goals": GoalDefinition,
        "reading_activities": Activity,
        "goal_periods": PeriodRecord,
    }

    schema = schemas[table]
    raw = values.get("payload", row.payload if row is not None else None)

    try:
        parsed = schema.model_validate(raw)
    except PayloadError as exc:
        raise ValidationError(str(exc)) from exc

    if parsed.id != row_id:
        raise ValidationError("Payload id must match row id")

    if isinstance(parsed, GoalDefinition) and not parsed.rules:
        parsed.rules = [
            GoalRule(
                at=parsed.created_at,
                target=parsed.target_value,
                title=parsed.title,
                active=parsed.is_active,
                archived=parsed.is_archived,
            )
        ]

    payload = parsed.model_dump(mode="json")

    if (
        table in {"reading_activities", "goal_periods"}
        and row is not None
        and schema.model_validate(row.payload) != parsed
    ):
        raise ValidationError("Activity and period history are immutable; append a correction")

    if isinstance(parsed, GoalDefinition):
        if parsed.created_at > datetime.now(UTC) + timedelta(minutes=5):
            raise ValidationError("Goal creation cannot be in the future")

        if parsed.scope == "book" and parsed.goal_type.value == "books_count":
            old_target = row.payload["target_value"] if row is not None else None

            if parsed.target_value > len(parsed.selected_book_ids) and parsed.target_value != old_target:
                raise ValidationError("Target cannot exceed the number of selected books")

        if row is not None:
            old = GoalDefinition.model_validate(row.payload)

            fixed = (
                "goal_type",
                "time_period",
                "scope",
                "scope_id",
                "book_ids",
                "timezone",
                "created_at",
                "start_date",
                "end_date",
                "is_recurring",
                "minimum_minutes",
            )

            if any(getattr(old, field) != getattr(parsed, field) for field in fixed):
                raise ValidationError("Create a replacement to change goal metric, schedule, or scope")

            revisions = {revision.at: revision for revision in old.rules}

            for revision in parsed.rules:
                if revision.at in revisions and revisions[revision.at] != revision:
                    raise ValidationError("Existing rule history cannot be rewritten")

                revisions[revision.at] = revision

            parsed.rules = sorted(revisions.values(), key=lambda revision: revision.at)
            latest = parsed.rules[-1]
            parsed.title = latest.title
            parsed.target_value = latest.target
            parsed.is_active = latest.active
            parsed.is_archived = latest.archived
            payload = parsed.model_dump(mode="json")

        if parsed.scope == "book":
            for book_id in parsed.selected_book_ids:
                await check_reference(
                    session,
                    user_id,
                    "books",
                    book_id,
                )

        if parsed.scope_id is not None and parsed.scope != "book":
            await check_reference(
                session,
                user_id,
                "shelves",
                parsed.scope_id,
            )

    if isinstance(parsed, Activity):
        if parsed.created_at > datetime.now(UTC) + timedelta(minutes=5):
            raise ValidationError("Activity creation cannot be in the future")

        await check_reference(
            session,
            user_id,
            "books",
            parsed.book_id,
        )

        for shelf_id in parsed.shelf_ids:
            await check_reference(
                session,
                user_id,
                "shelves",
                shelf_id,
            )

        if parsed.correction_of is not None:
            original = await owned_row(
                session,
                user_id,
                "reading_activities",
                parsed.correction_of,
            )

            if (
                original is None
                or original.payload["kind"] == "reversal"
                or str(parsed.book_id) != original.payload["book_id"]
            ):
                raise ValidationError("Correction must reference an owned original activity for the same book")

    if isinstance(parsed, PeriodRecord):
        if row is not None:
            existing = PeriodRecord.model_validate(row.payload)

            if existing.goal_id != parsed.goal_id or existing.definition.start_date != parsed.definition.start_date:
                raise ValidationError("Period identity cannot change")

        await check_reference(
            session,
            user_id,
            "reading_goals",
            parsed.goal_id,
        )

        for book_id in parsed.definition.selected_book_ids:
            await check_reference(
                session,
                user_id,
                "books",
                book_id,
            )

    return {**values, "payload": payload}


async def check_reference(session: AsyncSession, user_id: UUID, table: str, row_id: UUID) -> None:
    parent = await owned_row(
        session,
        user_id,
        table,
        row_id,
    )

    deleted = await tombstoned(
        session,
        user_id,
        table,
        row_id,
    )

    if parent is None and not deleted:
        raise ValidationError(f"Tracking reference {table} was not found")
