"""Deterministic goal projections over owned activity; no shared counters."""

from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from papyrus.schemas.tracking import Activity, GoalDefinition, GoalRule


def calendar_period(goal: GoalDefinition, at: datetime) -> tuple[datetime, datetime]:
    if goal.is_archived and goal.rules:
        at = min(at, goal.rules[-1].at)

    local = at.astimezone(ZoneInfo(goal.timezone))
    day = local.replace(hour=0, minute=0, second=0, microsecond=0)
    period = goal.time_period

    if period == "daily":
        start, end = day, day + timedelta(days=1)
    elif period == "weekly":
        start = day - timedelta(days=day.weekday())
        end = start + timedelta(days=7)
    elif period == "monthly":
        start = day.replace(day=1)
        end = start.replace(year=start.year + (start.month == 12), month=start.month % 12 + 1)
    elif period == "yearly":
        start = day.replace(month=1, day=1)
        end = start.replace(year=start.year + 1)
    else:
        return goal.start_date, goal.end_date

    return start.astimezone(UTC), end.astimezone(UTC)


def rule_at(goal: GoalDefinition, at: datetime) -> GoalRule:
    rule = GoalRule(
        at=goal.created_at, target=goal.target_value, title=goal.title, active=goal.is_active, archived=goal.is_archived
    )

    for revision in goal.rules:
        if revision.at > at:
            break

        rule = revision

    return rule


def union_length(ranges: list[tuple[float, float]]) -> float:
    if not ranges:
        return 0

    ranges = sorted(ranges)
    start, end = ranges[0]
    total = 0.0

    for left, right in ranges[1:]:
        if left <= end:
            end = max(end, right)
        else:
            total += end - start
            start, end = left, right

    return total + end - start


def effective_activities(ledger: list[Activity]) -> list[Activity]:
    unique = {activity.id: activity for activity in ledger}
    reversed_ids = {activity.correction_of for activity in unique.values() if activity.kind == "reversal"}
    return [activity for activity in unique.values() if activity.kind != "reversal" and activity.id not in reversed_ids]


def project_goal(goal: GoalDefinition, ledger: list[Activity], now: datetime) -> dict[str, Any]:
    start, end = (
        calendar_period(goal, now) if goal.is_recurring and now >= goal.start_date else (goal.start_date, goal.end_date)
    )
    cutoff, limit = max(start, goal.created_at), min(end, now)
    rules = [revision.at for revision in goal.rules if cutoff < revision.at < limit]
    zone = ZoneInfo(goal.timezone)
    times: dict[datetime, list[tuple[float, float]]] = defaultdict(list)
    coverage: dict[tuple[object, ...], list[tuple[float, float]]] = defaultdict(list)
    scales: dict[tuple[object, ...], float] = {}
    finished = set()
    manual_pages = 0
    estimated = False
    counted = []

    for activity in sorted(effective_activities(ledger), key=lambda activity: (activity.end_time, str(activity.id))):
        if goal.scope == "book" and activity.book_id not in goal.selected_book_ids:
            continue

        if goal.scope == "shelf" and goal.scope_id not in activity.shelf_ids:
            continue

        contributed = False
        point = (
            activity.end_time - timedelta(microseconds=1)
            if activity.kind == "reading" and activity.end_time > activity.start_time
            else activity.end_time
        )
        point_rule = rule_at(goal, point)

        if cutoff <= point < end and point <= now and point_rule.active and not point_rule.archived:
            if activity.kind == "completion":
                finished.add(activity.book_id)
                contributed = True
            elif activity.kind == "reading":
                manual_pages += activity.pages
                contributed = bool(activity.pages or activity.coverage)
                day = point.astimezone(zone).date()

                for extent in activity.coverage:
                    key = (day, activity.book_id, extent.key)
                    coverage[key].append((extent.start, extent.end))
                    scales.setdefault(key, extent.pages_per_unit)
                    estimated |= extent.estimated

        if activity.kind == "reading":
            cursor = max(activity.start_time, cutoff)
            stop_at = min(activity.end_time, limit)

            while cursor < stop_at:
                local = cursor.astimezone(zone)
                midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
                stop = min((midnight + timedelta(days=1)).astimezone(UTC), stop_at)
                stop = min([stop, *(boundary for boundary in rules if cursor < boundary < stop)])
                active = rule_at(goal, cursor)

                if active.active and not active.archived:
                    times[midnight.astimezone(UTC)].append((cursor.timestamp(), stop.timestamp()))
                    contributed = True

                cursor = stop

        if contributed:
            counted.append(str(activity.id))

    daily_seconds = {day: union_length(intervals) for day, intervals in times.items()}
    qualified = {day for day, seconds in daily_seconds.items() if seconds >= goal.minimum_minutes * 60}
    pages = manual_pages + sum(union_length(intervals) * scales[key] for key, intervals in coverage.items())
    seconds = int(sum(daily_seconds.values()))
    rule = rule_at(goal, end - timedelta(microseconds=1) if end <= now else now)
    value = {
        "books_count": len(finished),
        "pages_count": int(pages),
        "reading_time": seconds // 60,
        "reading_days": len(qualified),
    }[goal.goal_type]
    fractional = (
        seconds / 60 if goal.goal_type == "reading_time" else pages if goal.goal_type == "pages_count" else value
    )

    return {
        "current_value": value,
        "progress_percentage": min(100, fractional / rule.target * 100),
        "target_value": rule.target,
        "title": rule.title,
        "start_date": start,
        "end_date": end,
        "seconds": seconds,
        "pages": pages,
        "finished_books": len(finished),
        "days": len(qualified),
        "estimated": estimated,
        "activity_ids": counted,
    }
