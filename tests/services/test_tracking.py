"""Owned immutable activity, concurrent rules, and retained deletion history."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select

from papyrus.core.exceptions import ForbiddenError, ValidationError
from papyrus.models import SyncGoalPeriod, SyncReadingActivity, SyncReadingGoal
from papyrus.schemas.goal import GoalType
from papyrus.schemas.sync import PowerSyncCrudMutation
from papyrus.schemas.tracking import Activity, GoalDefinition, GoalRule, PeriodRecord
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


@pytest.mark.parametrize("operation", ["PUT", "PATCH"])
async def test_period_snapshot_retries_are_allowed_but_rule_changes_are_rejected(test_session_maker, operation):
    async with test_session_maker() as session:
        owner, _, goal, _ = await setup(session)
        period = PeriodRecord(id=uuid4(), goal_id=goal.id, definition=goal.model_copy(update={"is_recurring": False}))
        await apply_powersync_upload_batch(
            session, owner, [mutation("reading_goals", goal), mutation("goal_periods", period)]
        )
        retry = mutation("goal_periods", period).model_copy(update={"op": operation})
        await apply_powersync_upload_batch(session, owner, [retry])
        changed = period.model_copy(
            update={
                "definition": period.definition.model_copy(
                    update={"target_value": 50, "rules": [GoalRule(at=goal.created_at, target=50)]}
                )
            }
        )
        rewrite = mutation("goal_periods", changed).model_copy(update={"op": operation})

        with pytest.raises(ValidationError, match="immutable"):
            await apply_powersync_upload_batch(session, owner, [rewrite])

        persisted = await session.get(SyncGoalPeriod, period.id)
        assert PeriodRecord.model_validate(persisted.payload) == period


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


async def test_selected_book_goals_validate_every_owner_and_keep_selection_immutable(test_session_maker):
    async with test_session_maker() as session:
        owner, book_id, goal, _ = await setup(session)
        from papyrus.models import User

        user = await session.get(User, owner)
        other = await _create_book(session, user)
        foreign_owner = await _create_user(session, f"{uuid4()}@example.com")
        foreign = await _create_book(session, foreign_owner)
        other_id, foreign_id = other.book_id, foreign.book_id
        await session.commit()
        selected = goal.model_copy(update={"scope": "book", "scope_id": book_id, "book_ids": [book_id, other_id]})
        await apply_powersync_upload_batch(session, owner, [mutation("reading_goals", selected)])
        await apply_powersync_upload_batch(session, owner, [mutation("reading_goals", selected)])
        persisted = GoalDefinition.model_validate((await session.get(SyncReadingGoal, goal.id)).payload)
        assert set(persisted.selected_book_ids) == {book_id, other_id}
        changed = selected.model_copy(update={"book_ids": [book_id]})

        with pytest.raises(ValidationError, match="replacement"):
            await apply_powersync_upload_batch(session, owner, [mutation("reading_goals", changed)])

        history = PeriodRecord(
            id=uuid4(), goal_id=selected.id, definition=selected.model_copy(update={"is_recurring": False})
        )
        await apply_powersync_upload_batch(session, owner, [mutation("goal_periods", history)])
        assert set(
            GoalDefinition.model_validate(
                (await session.get(SyncGoalPeriod, history.id)).payload["definition"]
            ).book_ids
        ) == {book_id, other_id}
        foreign_history = history.model_copy(
            update={
                "id": uuid4(),
                "definition": history.definition.model_copy(update={"book_ids": [book_id, foreign_id]}),
            }
        )

        with pytest.raises(ForbiddenError):
            await apply_powersync_upload_batch(session, owner, [mutation("goal_periods", foreign_history)])

        invalid = selected.model_copy(update={"id": uuid4(), "book_ids": [book_id, foreign_id]})

        with pytest.raises(ForbiddenError):
            await apply_powersync_upload_batch(session, owner, [mutation("reading_goals", invalid)])

        assert await session.get(SyncReadingGoal, invalid.id) is None
        assert set(GoalDefinition.model_validate((await session.get(SyncReadingGoal, goal.id)).payload).book_ids) == {
            book_id,
            other_id,
        }


async def test_completion_target_is_bounded_by_distinct_selected_books(test_session_maker):
    async with test_session_maker() as session:
        owner, book_id, goal, _ = await setup(session)
        invalid = goal.model_copy(
            update={
                "scope": "book",
                "scope_id": book_id,
                "book_ids": [book_id, book_id],
                "goal_type": GoalType.BOOKS_COUNT,
                "target_value": 2,
                "rules": [GoalRule(at=goal.created_at, target=2)],
            }
        )

        with pytest.raises(ValidationError, match="number of selected books"):
            await apply_powersync_upload_batch(session, owner, [mutation("reading_goals", invalid)])

        single = invalid.model_copy(update={"book_ids": []})

        with pytest.raises(ValidationError, match="number of selected books"):
            await apply_powersync_upload_batch(session, owner, [mutation("reading_goals", single)])

        assert await session.get(SyncReadingGoal, goal.id) is None
