"""Goal-related schemas."""

from datetime import date, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class GoalType(StrEnum):
    """Type of reading goal."""

    BOOKS_COUNT = "books_count"
    PAGES_COUNT = "pages_count"
    READING_TIME = "reading_time"
    READING_DAYS = "reading_days"


class TimePeriod(StrEnum):
    """Goal time period."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"
    CUSTOM = "custom"


class Goal(BaseModel):
    """Goal response schema."""

    model_config = ConfigDict(from_attributes=True)

    goal_id: UUID
    title: str
    description: str | None = None
    goal_type: GoalType
    target_value: int
    current_value: int | None = None
    progress_percentage: float | None = None
    time_period: TimePeriod
    start_date: date
    end_date: date
    is_recurring: bool = True
    is_archived: bool = False
    timezone: str = "UTC"
    scope: Literal["library", "book", "shelf"] = "library"
    scope_id: UUID | None = None
    minimum_minutes: int = 5
    is_active: bool = True
    is_completed: bool = False
    completed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class GoalList(BaseModel):
    """Goal list response."""

    goals: list[Goal]


class CreateGoalRequest(BaseModel):
    """Goal creation request."""

    is_recurring: bool = True
    timezone: str = "UTC"
    scope: Literal["library", "book", "shelf"] = "library"
    scope_id: UUID | None = None
    minimum_minutes: int = Field(default=5, ge=1, le=1440)
    title: str = Field(..., max_length=255)
    description: str | None = None
    goal_type: GoalType
    target_value: int = Field(..., ge=1)
    time_period: TimePeriod
    start_date: date
    end_date: date


class UpdateGoalRequest(BaseModel):
    """Goal update request."""

    title: str | None = Field(None, max_length=255)
    description: str | None = None
    target_value: int | None = Field(None, ge=1)
    end_date: date | None = None
    is_active: bool | None = None
    is_archived: bool | None = None
