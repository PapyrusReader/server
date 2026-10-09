"""Owned goal definitions and derived progress exposed through REST."""

from datetime import UTC, datetime, time, timedelta
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ValidationError as PayloadError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.core.exceptions import NotFoundError, ValidationError
from papyrus.models import SyncReadingActivity, SyncReadingGoal
from papyrus.schemas.goal import CreateGoalRequest, Goal, UpdateGoalRequest
from papyrus.schemas.sync import PowerSyncCrudMutation
from papyrus.schemas.tracking import Activity, GoalDefinition, GoalRule
from papyrus.services.goal_progress import calendar_period, project_goal
from papyrus.services.library_sync import owned_row
from papyrus.services.sync import apply_powersync_upload_batch


async def activities(session: AsyncSession, user_id: UUID) -> list[Activity]:
    result = await session.execute(select(SyncReadingActivity).where(SyncReadingActivity.owner_user_id == user_id))
    return [Activity.model_validate(row.payload) for row in result.scalars()]


def goal_response(definition: GoalDefinition, ledger: list[Activity]) -> Goal:
    now = datetime.now(UTC)
    progress = project_goal(definition, ledger, now)
    zone = ZoneInfo(definition.timezone)

    return Goal(
        goal_id=definition.id,
        title=progress["title"] or definition.title or "Reading goal",
        description=definition.description,
        goal_type=definition.goal_type,
        target_value=progress["target_value"],
        current_value=progress["current_value"],
        progress_percentage=progress["progress_percentage"],
        time_period=definition.time_period,
        start_date=progress["start_date"].astimezone(zone).date(),
        end_date=(progress["end_date"] - timedelta(microseconds=1)).astimezone(zone).date(),
        is_active=definition.is_active,
        is_archived=definition.is_archived,
        is_recurring=definition.is_recurring,
        timezone=definition.timezone,
        scope=definition.scope,
        scope_id=definition.scope_id,
        book_ids=definition.book_ids,
        minimum_minutes=definition.minimum_minutes,
        is_completed=progress["progress_percentage"] >= 100,
        created_at=definition.created_at,
    )


async def list_goals(session: AsyncSession, user_id: UUID, active: bool | None) -> list[Goal]:
    result = await session.execute(select(SyncReadingGoal).where(SyncReadingGoal.owner_user_id == user_id))
    definitions = [GoalDefinition.model_validate(row.payload) for row in result.scalars()]
    ledger = await activities(session, user_id)
    return [goal_response(goal, ledger) for goal in definitions if active is None or goal.is_active == active]


async def get_definition(session: AsyncSession, user_id: UUID, goal_id: UUID) -> GoalDefinition:
    row = await owned_row(
        session,
        user_id,
        "reading_goals",
        goal_id,
    )

    if row is None:
        raise NotFoundError("Goal was not found")

    return GoalDefinition.model_validate(row.payload)


async def get_goal(session: AsyncSession, user_id: UUID, goal_id: UUID) -> Goal:
    return goal_response(await get_definition(session, user_id, goal_id), await activities(session, user_id))


async def create_goal(session: AsyncSession, user_id: UUID, request: CreateGoalRequest) -> Goal:
    now = datetime.now(UTC)

    try:
        zone = ZoneInfo(request.timezone)

        definition = GoalDefinition(
            id=uuid4(),
            title=request.title,
            description=request.description,
            goal_type=request.goal_type,
            target_value=request.target_value,
            time_period=request.time_period,
            start_date=datetime.combine(request.start_date, time.min, zone).astimezone(UTC),
            end_date=datetime.combine(request.end_date + timedelta(days=1), time.min, zone).astimezone(UTC),
            created_at=now,
            timezone=request.timezone,
            scope=request.scope,
            scope_id=request.scope_id
            or (min(request.book_ids, key=str) if request.scope == "book" and request.book_ids else None),
            book_ids=request.book_ids,
            minimum_minutes=request.minimum_minutes,
            is_recurring=request.is_recurring and request.time_period != "custom",
            rules=[
                GoalRule(
                    at=now,
                    title=request.title,
                    target=request.target_value,
                )
            ],
        )
    except (PayloadError, ZoneInfoNotFoundError) as exc:
        raise ValidationError(str(exc)) from exc

    if definition.is_recurring and definition.time_period != "custom":
        definition.start_date, definition.end_date = calendar_period(definition, now)

    await apply_powersync_upload_batch(
        session,
        user_id,
        [
            PowerSyncCrudMutation(
                table="reading_goals",
                op="PUT",
                id=str(definition.id),
                op_data={"payload": definition.model_dump(mode="json")},
            )
        ],
    )

    return goal_response(definition, [])


async def update_goal(session: AsyncSession, user_id: UUID, goal_id: UUID, request: UpdateGoalRequest) -> Goal:
    definition = await get_definition(session, user_id, goal_id)

    if request.end_date is not None:
        raise ValidationError("Create a replacement goal to change its deadline")

    now = max(
        datetime.now(UTC),
        definition.rules[-1].at + timedelta(microseconds=1)
        if definition.rules
        else definition.created_at + timedelta(microseconds=1),
    )

    changes = request.model_dump(exclude_unset=True, exclude={"end_date"})

    if changes.get("is_archived"):
        changes["is_active"] = False

    updated = definition.model_copy(update=changes)

    rule = GoalRule(
        at=now,
        title=updated.title,
        target=updated.target_value,
        active=updated.is_active,
        archived=updated.is_archived,
    )

    initial = definition.rules or [
        GoalRule(
            at=definition.created_at,
            title=definition.title,
            target=definition.target_value,
            active=definition.is_active,
            archived=definition.is_archived,
        )
    ]

    updated.rules = [*initial, rule]

    await apply_powersync_upload_batch(
        session,
        user_id,
        [
            PowerSyncCrudMutation(
                table="reading_goals",
                op="PATCH",
                id=str(goal_id),
                op_data={"payload": updated.model_dump(mode="json")},
            )
        ],
    )

    return goal_response(updated, await activities(session, user_id))


async def delete_goal(session: AsyncSession, user_id: UUID, goal_id: UUID) -> None:
    await get_definition(session, user_id, goal_id)

    await apply_powersync_upload_batch(
        session,
        user_id,
        [
            PowerSyncCrudMutation(
                table="reading_goals",
                op="DELETE",
                id=str(goal_id),
            )
        ],
    )
