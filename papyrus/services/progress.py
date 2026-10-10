"""Real activity-backed sessions and cross-device statistics."""

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID, uuid4

from pydantic import ValidationError as PayloadError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.api.deps import PaginationParams
from papyrus.core.exceptions import NotFoundError, ValidationError
from papyrus.models import SyncBookShelf
from papyrus.schemas.common import Pagination
from papyrus.schemas.goal import GoalType, TimePeriod
from papyrus.schemas.progress import (
    BookBreakdown,
    CreateReadingSessionRequest,
    DailyBreakdown,
    ReadingSession,
    ReadingSessionList,
    ReadingStatistics,
    StatisticsPeriod,
    StatisticsTotals,
)
from papyrus.schemas.sync import PowerSyncCrudMutation
from papyrus.schemas.tracking import Activity, GoalDefinition
from papyrus.services.goal_progress import effective_activities, project_goal, union_length
from papyrus.services.goals import activities
from papyrus.services.library_sync import owned_row
from papyrus.services.sync import apply_powersync_upload_batch, lock_owner_mutations


def session_response(activity: Activity) -> ReadingSession:
    return ReadingSession(
        session_id=activity.id,
        book_id=activity.book_id,
        book_title=activity.book_title,
        start_time=activity.start_time,
        end_time=activity.end_time,
        pages_read=activity.pages,
        duration_minutes=int((activity.end_time - activity.start_time).total_seconds()) // 60,
        start_position=activity.start_position,
        end_position=activity.end_position,
        device_type=activity.device_type if activity.device_type is not None else activity.source,
        device_name=activity.device_id,
        created_at=activity.created_at,
    )


def grouped_sessions(entries: list[Activity]) -> list[ReadingSession]:
    groups: dict[tuple[UUID, UUID], list[Activity]] = {}

    for activity in entries:
        groups.setdefault((activity.book_id, activity.session_id or activity.id), []).append(activity)

    sessions = []

    for group in groups.values():
        first = min(group, key=lambda entry: entry.start_time)
        last = max(group, key=lambda entry: entry.end_time)
        end = last.end_time
        result = session_response(first)
        result.end_time = end
        result.end_position = last.end_position

        result.duration_minutes = (
            int(union_length([(entry.start_time.timestamp(), entry.end_time.timestamp()) for entry in group])) // 60
        )

        projection = project_goal(
            totals_definition(first.start_time, end + timedelta(microseconds=1)),
            group,
            end + timedelta(microseconds=1),
        )

        result.pages_read = int(projection["pages"])
        sessions.append(result)

    return sorted(sessions, key=lambda entry: entry.start_time, reverse=True)


async def list_sessions(
    session: AsyncSession,
    user_id: UUID,
    pagination: PaginationParams,
    book_id: UUID | None,
    start_date: date | None,
    end_date: date | None,
) -> ReadingSessionList:
    ledger = effective_activities(await activities(session, user_id))

    entries = sorted(
        (
            entry
            for entry in ledger
            if entry.kind == "reading"
            and (book_id is None or entry.book_id == book_id)
            and (start_date is None or entry.end_time.date() >= start_date)
            and (end_date is None or entry.start_time.date() <= end_date)
        ),
        key=lambda entry: entry.start_time,
        reverse=True,
    )

    sessions = grouped_sessions(entries)
    total = len(sessions)
    pages = (total + pagination.limit - 1) // pagination.limit

    return ReadingSessionList(
        sessions=sessions[pagination.offset : pagination.offset + pagination.limit],
        pagination=Pagination(
            page=pagination.page,
            limit=pagination.limit,
            total=total,
            total_pages=pages,
            has_next=pagination.page < pages,
            has_prev=pagination.page > 1,
        ),
    )


async def create_session(session: AsyncSession, user_id: UUID, request: CreateReadingSessionRequest) -> ReadingSession:
    await lock_owner_mutations(session, user_id)

    book = await owned_row(
        session,
        user_id,
        "books",
        request.book_id,
    )

    if book is None:
        raise NotFoundError("Book was not found")

    if request.session_id is not None:
        existing = await owned_row(
            session,
            user_id,
            "reading_activities",
            request.session_id,
        )

        if existing is not None:
            original = Activity.model_validate(existing.payload)
            start = request.start_time.replace(tzinfo=UTC) if request.start_time.tzinfo is None else request.start_time
            requested_end = request.end_time

            if requested_end is not None and requested_end.tzinfo is None:
                requested_end = requested_end.replace(tzinfo=UTC)

            if (
                original.book_id != request.book_id
                or original.start_time != start
                or (requested_end is not None and original.end_time != requested_end)
                or original.start_position != request.start_position
                or original.end_position != request.end_position
                or original.device_type != request.device_type
                or original.pages != (request.pages_read or 0)
                or original.device_id != (request.device_name or "manual")
            ):
                raise ValidationError("Session id already identifies different activity")

            return session_response(original)

    result = await session.execute(
        select(SyncBookShelf.shelf_id).where(
            SyncBookShelf.owner_user_id == user_id, SyncBookShelf.book_id == request.book_id
        )
    )

    shelves = list(result.scalars())

    try:
        start = request.start_time.replace(tzinfo=UTC) if request.start_time.tzinfo is None else request.start_time
        end = request.end_time or datetime.now(UTC)
        end = end.replace(tzinfo=UTC) if end.tzinfo is None else end

        activity = Activity(
            id=request.session_id or uuid4(),
            book_id=request.book_id,
            book_title=book.title,
            start_time=start,
            end_time=end,
            created_at=datetime.now(UTC),
            start_position=request.start_position,
            end_position=request.end_position,
            device_type=request.device_type,
            pages=request.pages_read or 0,
            device_id=request.device_name or "manual",
            shelf_ids=shelves,
        )
    except PayloadError as exc:
        raise ValidationError(str(exc)) from exc

    await apply_powersync_upload_batch(
        session,
        user_id,
        [
            PowerSyncCrudMutation(
                table="reading_activities",
                op="PUT",
                id=str(activity.id),
                op_data={"payload": activity.model_dump(mode="json")},
            )
        ],
    )

    return session_response(activity)


def totals_definition(start: datetime, end: datetime, book_id: UUID | None = None) -> GoalDefinition:
    return GoalDefinition(
        id=UUID(int=0),
        goal_type=GoalType.READING_TIME,
        target_value=1,
        time_period=TimePeriod.CUSTOM,
        start_date=start,
        end_date=end,
        created_at=start,
        is_recurring=False,
        scope="library" if book_id is None else "book",
        scope_id=book_id,
    )


def overlaps_period(activity: Activity, start: datetime, end: datetime) -> bool:
    if activity.start_time == activity.end_time:
        return start <= activity.start_time < end

    return activity.start_time < end and activity.end_time > start


def current_reading_streak(ledger: list[Activity], now: datetime) -> int:
    """Union complete day ranges and partial boundary days without walking long activities."""
    boundaries: dict[date, list[tuple[float, float]]] = defaultdict(list)
    qualified_ranges: list[tuple[int, int]] = []

    for activity in ledger:
        if activity.kind != "reading":
            continue

        start = activity.start_time.astimezone(UTC)
        end = min(activity.end_time, now).astimezone(UTC)

        if start >= end:
            continue

        if start.date() == end.date():
            boundaries[start.date()].append((start.timestamp(), end.timestamp()))
            continue

        next_day = datetime.combine(start.date() + timedelta(days=1), time.min, UTC)
        last_day = datetime.combine(end.date(), time.min, UTC)
        boundaries[start.date()].append((start.timestamp(), next_day.timestamp()))
        boundaries[end.date()].append((last_day.timestamp(), end.timestamp()))
        first_complete, last_complete = start.date().toordinal() + 1, end.date().toordinal() - 1

        if first_complete <= last_complete:
            qualified_ranges.append((first_complete, last_complete))

    for day, intervals in boundaries.items():
        if union_length(intervals) >= 300:
            qualified_ranges.append((day.toordinal(), day.toordinal()))

    merged: list[tuple[int, int]] = []

    for first, last in sorted(qualified_ranges):
        if merged and first <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))

    today = now.astimezone(UTC).date().toordinal()

    if merged and merged[-1][1] in {today, today - 1}:
        return merged[-1][1] - merged[-1][0] + 1

    return 0


async def statistics(
    session: AsyncSession, user_id: UUID, start_date: date | None, end_date: date | None
) -> ReadingStatistics:
    today = datetime.now(UTC).date()
    first, last = start_date or today.replace(day=1), end_date or today

    if last < first or (last - first).days > 3660:
        raise ValidationError("Choose an ordered statistics period of at most ten years")

    start = datetime.combine(first, time.min, UTC)
    end = datetime.combine(last + timedelta(days=1), time.min, UTC)
    ledger = await activities(session, user_id)
    now = datetime.now(UTC)
    total = project_goal(totals_definition(start, end), ledger, now)
    real = effective_activities(ledger)

    counted = [entry for entry in real if entry.kind == "reading" and overlaps_period(entry, start, end)]

    daily = []
    longest = streak = 0

    for index in range((last - first).days + 1):
        day = start + timedelta(days=index)
        next_day = day + timedelta(days=1)
        values = project_goal(totals_definition(day, next_day), ledger, now)

        count = len({entry.session_id or entry.id for entry in counted if overlaps_period(entry, day, next_day)})

        daily.append(
            DailyBreakdown(
                date=day.date(),
                reading_time_minutes=values["seconds"] // 60,
                pages_read=int(values["pages"]),
                sessions_count=count,
            )
        )

        if values["seconds"] >= 300:
            streak += 1
            longest = max(longest, streak)
        else:
            streak = 0

    books = {entry.book_id: entry.book_title for entry in counted}
    breakdown = []

    for book_id, title in books.items():
        values = project_goal(totals_definition(start, end, book_id), ledger, now)

        breakdown.append(
            BookBreakdown(
                book_id=book_id,
                title=title,
                reading_time_minutes=values["seconds"] // 60,
                pages_read=int(values["pages"]),
                sessions_count=len({entry.session_id or entry.id for entry in counted if entry.book_id == book_id}),
            )
        )

    sessions_count = len({entry.session_id or entry.id for entry in counted})

    return ReadingStatistics(
        period=StatisticsPeriod(start_date=first, end_date=last),
        totals=StatisticsTotals(
            reading_time_minutes=total["seconds"] // 60,
            pages_read=int(total["pages"]),
            books_completed=total["finished_books"],
            sessions_count=sessions_count,
            average_session_minutes=total["seconds"] / 60 / sessions_count if sessions_count else 0,
            reading_days=total["days"],
            current_streak=current_reading_streak(real, now),
            longest_streak=longest,
        ),
        daily_breakdown=daily,
        books_breakdown=breakdown,
    )
