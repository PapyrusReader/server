"""Retain period rules when a goal definition is deleted."""

from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.models import SyncGoalPeriod, SyncReadingGoal
from papyrus.schemas.tracking import GoalDefinition, GoalRule, PeriodRecord
from papyrus.services.goal_progress import calendar_period, rule_at


async def preserve_goal_history(session: AsyncSession, user_id: UUID, row: SyncReadingGoal) -> None:
    goal = GoalDefinition.model_validate(row.payload)
    now = datetime.now(UTC)
    boundary = min(now, goal.rules[-1].at) if goal.is_archived and goal.rules else now
    start, end = goal.start_date, goal.end_date

    while start < boundary:
        stop = min(end, boundary)
        at = stop - timedelta(microseconds=1)
        rule = rule_at(goal, at)
        since_epoch = start - datetime(1970, 1, 1, tzinfo=UTC)
        microseconds = (since_epoch.days * 86400 + since_epoch.seconds) * 1000000 + since_epoch.microseconds
        identifier = uuid5(NAMESPACE_URL, f"papyrus:goal-period:{goal.id}:{microseconds}")
        existing = await session.get(SyncGoalPeriod, identifier)

        if existing is None:
            revisions = [revision for revision in goal.rules if revision.at < stop]
            archived = end > boundary

            if archived:
                revisions.append(GoalRule(at=stop, target=rule.target, title=rule.title, active=False, archived=True))

            definition = goal.model_copy(
                update={
                    "start_date": start,
                    "end_date": stop,
                    "target_value": rule.target,
                    "title": rule.title,
                    "is_recurring": False,
                    "is_active": False if archived else rule.active,
                    "is_archived": archived or rule.archived,
                    "rules": revisions,
                }
            )
            record = PeriodRecord(id=identifier, goal_id=goal.id, definition=definition)

            session.add(SyncGoalPeriod(id=identifier, owner_user_id=user_id, payload=record.model_dump(mode="json")))
            await session.flush()

        if not goal.is_recurring or end >= boundary:
            break

        start, end = calendar_period(goal, end)
