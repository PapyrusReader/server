"""Owned immutable activity, concurrent rules, and retained deletion history."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from papyrus.core.exceptions import ForbiddenError, ValidationError
from papyrus.models import SyncGoalPeriod, SyncReadingActivity, SyncReadingGoal
from papyrus.schemas.sync import PowerSyncCrudMutation
from papyrus.schemas.tracking import Activity, GoalDefinition, GoalRule
from papyrus.services.sync import apply_powersync_upload_batch
from tests.services.test_sync import _create_book, _create_user


def mutation(table, payload):
    return PowerSyncCrudMutation(
        table=table, op="PUT", id=str(payload.id), op_data={"payload": payload.model_dump(mode="json")}
    )


async def setup(session):
    owner = await _create_user(session, f"{uuid4()}@example.com")
    book = await _create_book(session, owner)
    now = datetime.now(UTC) - timedelta(minutes=30)
    goal = GoalDefinition(
        id=uuid4(),
        goal_type="reading_time",
        target_value=30,
        time_period="daily",
        start_date=now.replace(hour=0, minute=0, second=0, microsecond=0),
        end_date=(now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0),
        created_at=now,
        rules=[GoalRule(at=now, target=30)],
    )
    activity = Activity(
        id=uuid4(),
        book_id=book.book_id,
        book_title=book.title,
        start_time=now,
        end_time=now + timedelta(minutes=10),
        created_at=now + timedelta(minutes=10),
    )
    await session.commit()
    return owner.user_id, book.book_id, goal, activity


async def test_tracking_retries_are_idempotent_and_mutation_is_atomic(test_session_maker):
    async with test_session_maker() as session:
        owner, book_id, goal, activity = await setup(session)
        batch = [mutation("reading_goals", goal), mutation("reading_activities", activity)]
        await apply_powersync_upload_batch(session, owner, batch)
        await apply_powersync_upload_batch(session, owner, batch)
        assert len((await session.execute(select(SyncReadingActivity))).scalars().all()) == 1
        changed = activity.model_copy(update={"pages": 99})

        with pytest.raises(ValidationError):
            await apply_powersync_upload_batch(
                session,
                owner,
                [
                    PowerSyncCrudMutation(
                        table="books", op="PATCH", id=str(book_id), op_data={"title": "Should roll back"}
                    ),
                    mutation("reading_activities", changed),
                ],
            )

        assert (await session.get(SyncReadingActivity, activity.id)).payload["pages"] == 0
        from papyrus.models import SyncBook

        assert (await session.get(SyncBook, book_id)).title == activity.book_title


async def test_tracking_ownership_applies_to_payload_references(test_session_maker):
    async with test_session_maker() as session:
        owner, book_id, goal, activity = await setup(session)
        intruder = await _create_user(session, f"{uuid4()}@example.com")
        intruder_id = intruder.user_id
        await session.commit()

        with pytest.raises(ForbiddenError):
            await apply_powersync_upload_batch(session, intruder_id, [mutation("reading_activities", activity)])

        assert await session.get(SyncReadingActivity, activity.id) is None
        await apply_powersync_upload_batch(
            session, owner, [mutation("reading_goals", goal), mutation("reading_activities", activity)]
        )

        with pytest.raises(ForbiddenError):
            await apply_powersync_upload_batch(session, intruder_id, [mutation("reading_goals", goal)])


async def test_goal_rule_branches_merge_without_rewriting_existing_history(test_session_maker):
    async with test_session_maker() as session:
        owner, _, goal, _ = await setup(session)
        await apply_powersync_upload_batch(session, owner, [mutation("reading_goals", goal)])
        first = goal.model_copy(
            update={
                "target_value": 20,
                "rules": [*goal.rules, GoalRule(at=goal.created_at + timedelta(minutes=5), target=20)],
            }
        )
        second = goal.model_copy(
            update={
                "target_value": 40,
                "rules": [*goal.rules, GoalRule(at=goal.created_at + timedelta(minutes=10), target=40)],
            }
        )
        await apply_powersync_upload_batch(session, owner, [mutation("reading_goals", first)])
        await apply_powersync_upload_batch(session, owner, [mutation("reading_goals", second)])
        row = await session.get(SyncReadingGoal, goal.id)
        result = GoalDefinition.model_validate(row.payload)
        assert [rule.target for rule in result.rules] == [30, 20, 40]
        assert result.target_value == 40
        rewrite = goal.model_copy(update={"target_value": 10, "rules": [GoalRule(at=goal.created_at, target=10)]})

        with pytest.raises(ValidationError):
            await apply_powersync_upload_batch(session, owner, [mutation("reading_goals", rewrite)])


async def test_book_goal_deletion_preserves_activity_and_periods(test_session_maker):
    async with test_session_maker() as session:
        owner, book_id, goal, activity = await setup(session)
        await apply_powersync_upload_batch(
            session, owner, [mutation("reading_goals", goal), mutation("reading_activities", activity)]
        )
        await apply_powersync_upload_batch(
            session,
            owner,
            [
                PowerSyncCrudMutation(table="books", op="DELETE", id=str(book_id)),
                PowerSyncCrudMutation(table="reading_goals", op="DELETE", id=str(goal.id)),
            ],
        )
        assert await session.get(SyncReadingActivity, activity.id) is not None
        periods = (await session.execute(select(SyncGoalPeriod))).scalars().all()
        assert len(periods) == 1
        assert periods[0].payload["definition"]["is_archived"]
        assert await session.get(SyncReadingGoal, goal.id) is None
        await apply_powersync_upload_batch(session, owner, [mutation("reading_goals", goal)])
        assert await session.get(SyncReadingGoal, goal.id) is None
        at = datetime.now(UTC)
        reverse = Activity(
            id=uuid4(),
            book_id=book_id,
            book_title=activity.book_title,
            start_time=at,
            end_time=at,
            created_at=at,
            kind="reversal",
            correction_of=activity.id,
        )
        await apply_powersync_upload_batch(session, owner, [mutation("reading_activities", reverse)])
        assert await session.get(SyncReadingActivity, reverse.id) is not None
