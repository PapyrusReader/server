"""Device handoff, competing branches, terminal saves and ownership."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4, uuid5

import pytest
from sqlalchemy import select

from papyrus.core.exceptions import ForbiddenError, ValidationError
from papyrus.models import SyncBook, SyncReadingActivity, SyncReadingTimer
from papyrus.schemas.reading_timer import TimerInterval, TimerPayload
from papyrus.schemas.sync import PowerSyncCrudMutation
from papyrus.schemas.tracking import Activity
from papyrus.services.sync import apply_powersync_upload_batch
from tests.services.test_sync import _create_book, _create_user


async def setup(session):
    user = await _create_user(session, f"{uuid4()}@example.com")
    book = await _create_book(session, user)
    start = datetime.now(UTC) - timedelta(minutes=5)

    timer = TimerPayload(
        id=uuid4(),
        book_id=book.book_id,
        book_title=book.title,
        started_at=start,
        running_since=start,
        device_id="browser",
        shelf_ids=[],
        start_page=0,
        phase="running",
        intervals=[],
        revision=0,
        version=str(uuid4()),
        target_seconds=1800,
    )

    await session.commit()
    return user.user_id, timer


def transition(timer, seconds=60, **values):
    return timer.model_copy(
        update={
            "version": str(uuid4()),
            "revision": timer.revision + 1,
            "phase": "paused",
            "running_since": None,
            "intervals": [TimerInterval(start=timer.started_at, end=timer.started_at + timedelta(seconds=seconds))],
            **values,
        }
    )


def mutation(timer, previous=None, status="active"):
    return PowerSyncCrudMutation(
        table="reading_timers",
        op="PUT" if previous is None else "PATCH",
        id=str(timer.id),
        op_data={
            "payload": timer.model_dump(mode="json"),
            "status": status,
            "base_version": previous.version if previous else None,
        },
    )


def saved(timer, previous, page=12):
    activity = Activity(
        id=uuid5(timer.id, "0"),
        session_id=timer.id,
        book_id=timer.book_id,
        book_title=timer.book_title,
        start_time=timer.intervals[0].start,
        end_time=timer.intervals[0].end,
        created_at=datetime.now(UTC),
        pages=page,
    )

    return [
        mutation(timer, previous, "saved"),
        PowerSyncCrudMutation(
            table="reading_activities",
            op="PUT",
            id=str(activity.id),
            op_data={"payload": activity.model_dump(mode="json")},
        ),
        PowerSyncCrudMutation(table="books", op="PATCH", id=str(timer.book_id), op_data={"current_page": page}),
    ]


async def test_start_pause_resume_and_finish_are_owned_shared_state(test_session_maker):
    async with test_session_maker() as browser, test_session_maker() as phone:
        user, timer = await setup(browser)
        await apply_powersync_upload_batch(browser, user, [mutation(timer)])
        remote = await phone.get(SyncReadingTimer, timer.id)
        assert TimerPayload.model_validate(remote.payload) == timer
        paused = transition(timer)
        await apply_powersync_upload_batch(phone, user, [mutation(paused, timer)])
        await browser.refresh(await browser.get(SyncReadingTimer, timer.id))
        resumed = transition(paused, phase="running", running_since=timer.started_at + timedelta(minutes=2))
        await apply_powersync_upload_batch(browser, user, [mutation(resumed, paused)])
        await phone.refresh(remote)
        assert remote.payload["running_since"] == resumed.model_dump(mode="json")["running_since"]
        assert remote.payload["intervals"] == paused.model_dump(mode="json")["intervals"]


async def test_stale_branch_and_descendants_cannot_replace_accepted_state(test_session_maker):
    async with test_session_maker() as session:
        user, timer = await setup(session)
        await apply_powersync_upload_batch(session, user, [mutation(timer)])
        winner, stale = transition(timer), transition(timer, seconds=90)
        assert await apply_powersync_upload_batch(session, user, [mutation(winner, timer)]) == 1
        assert await apply_powersync_upload_batch(session, user, [mutation(stale, timer)]) == 0
        descendant = transition(
            stale, seconds=90, phase="running", running_since=timer.started_at + timedelta(minutes=2)
        )
        assert await apply_powersync_upload_batch(session, user, [mutation(descendant, stale)]) == 0
        assert (await session.get(SyncReadingTimer, timer.id)).payload["version"] == winner.version


@pytest.mark.parametrize("status", ["saved", "discarded"])
@pytest.mark.parametrize("phase", ["paused", "running", "finished"])
async def test_sparse_offline_transition_after_terminal_state_does_not_block_uploads(test_session_maker, status, phase):
    async with test_session_maker() as session:
        user, timer = await setup(session)
        await apply_powersync_upload_batch(session, user, [mutation(timer)])
        terminal = transition(timer, phase="finished")
        batch = saved(terminal, timer) if status == "saved" else [mutation(terminal, timer, status)]
        await apply_powersync_upload_batch(session, user, batch)

        stale = transition(
            timer,
            seconds=90,
            phase=phase,
            running_since=timer.started_at + timedelta(minutes=2) if phase == "running" else None,
        )

        patch = mutation(stale, timer)
        del patch.op_data["status"]
        assert await apply_powersync_upload_batch(session, user, [patch]) == 0
        row = await session.get(SyncReadingTimer, timer.id)
        assert row.status == status
        assert row.payload["version"] == terminal.version
        history = (await session.scalars(select(SyncReadingActivity))).all()
        assert len(history) == (1 if status == "saved" else 0)

        if status == "saved":
            assert (await session.get(SyncBook, timer.book_id)).current_page == 12

        next_timer = timer.model_copy(update={"id": uuid4(), "version": str(uuid4())})
        assert await apply_powersync_upload_batch(session, user, [mutation(next_timer)]) == 1


async def test_concurrent_save_and_retries_write_history_and_progress_once(test_session_maker):
    async with test_session_maker() as setup_session:
        user, timer = await setup(setup_session)
        await apply_powersync_upload_batch(setup_session, user, [mutation(timer)])

    first, second = transition(timer, phase="finished"), transition(timer, seconds=120, phase="finished")

    async def send(batch):
        async with test_session_maker() as session:
            return await apply_powersync_upload_batch(session, user, batch)

    batches = [saved(first, timer, 12), saved(second, timer, 24)]
    results = await asyncio.gather(*(send(batch) for batch in batches))
    assert sorted(results) == [0, 3]
    winner = first if results[0] else second
    assert await send(batches[0]) == 0
    assert await send(batches[1]) == 0
    assert await send([mutation(timer)]) == 0

    async with test_session_maker() as session:
        rows = (await session.scalars(select(SyncReadingActivity))).all()
        assert len(rows) == 1
        assert rows[0].payload["end_time"] == winner.model_dump(mode="json")["intervals"][0]["end"]
        assert (await session.get(SyncBook, timer.book_id)).current_page == (12 if results[0] else 24)
        assert (await session.get(SyncReadingTimer, timer.id)).status == "saved"


async def test_concurrent_starts_converge_and_discard_cannot_be_revived(test_session_maker):
    async with test_session_maker() as session:
        user, timer = await setup(session)
        other = timer.model_copy(update={"id": uuid4(), "version": str(uuid4())})
        await apply_powersync_upload_batch(session, user, [mutation(timer)])
        assert await apply_powersync_upload_batch(session, user, [mutation(other)]) == 0
        assert (await session.get(SyncReadingTimer, other.id)).status == "discarded"
        discarded = transition(timer)
        assert await apply_powersync_upload_batch(session, user, [mutation(discarded, timer, "discarded")]) == 1
        assert await apply_powersync_upload_batch(session, user, [mutation(transition(timer), timer)]) == 0
        assert await apply_powersync_upload_batch(session, user, [mutation(other)]) == 0
        third = timer.model_copy(update={"id": uuid4(), "version": str(uuid4())})
        assert await apply_powersync_upload_batch(session, user, [mutation(third)]) == 1


async def test_ownership_validates_timer_and_payload_book(test_session_maker):
    async with test_session_maker() as session:
        owner, timer = await setup(session)
        other, other_timer = await setup(session)
        await apply_powersync_upload_batch(session, owner, [mutation(timer)])

        with pytest.raises(ForbiddenError):
            await apply_powersync_upload_batch(session, other, [mutation(transition(timer), timer)])

        with pytest.raises(ForbiddenError):
            await apply_powersync_upload_batch(
                session, other, [mutation(other_timer.model_copy(update={"book_id": timer.book_id}))]
            )


async def test_invalid_save_rolls_back_state_and_history(test_session_maker):
    async with test_session_maker() as session:
        user, timer = await setup(session)
        await apply_powersync_upload_batch(session, user, [mutation(timer)])
        batch = saved(transition(timer, phase="finished"), timer)
        batch[-1].op_data["title"] = "Not a progress update"

        with pytest.raises(ValidationError):
            await apply_powersync_upload_batch(session, user, batch)

        row = await session.get(SyncReadingTimer, timer.id)
        assert row.status == "active"
        assert row.payload["version"] == timer.version
        assert (await session.scalars(select(SyncReadingActivity))).all() == []


async def test_deleted_book_closes_timer_without_resurrecting_book(test_session_maker):
    async with test_session_maker() as session:
        user, timer = await setup(session)
        await apply_powersync_upload_batch(session, user, [mutation(timer)])
        await apply_powersync_upload_batch(
            session, user, [PowerSyncCrudMutation(table="books", op="DELETE", id=str(timer.book_id))]
        )
        assert await apply_powersync_upload_batch(session, user, saved(transition(timer, phase="finished"), timer)) == 0
        assert (await session.get(SyncReadingTimer, timer.id)).status == "discarded"
        assert await session.get(SyncBook, timer.book_id) is None
        assert (await session.scalars(select(SyncReadingActivity))).all() == []
