"""Tracking payloads shared by REST and PowerSync."""

from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from papyrus.schemas.goal import GoalType, TimePeriod


class TrackingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID


class GoalRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at: AwareDatetime
    target: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=255)
    active: bool = True
    archived: bool = False


class GoalDefinition(TrackingPayload):
    title: str | None = Field(default=None, max_length=255)
    description: str | None = None
    goal_type: GoalType
    target_value: int = Field(ge=1)
    time_period: TimePeriod
    start_date: AwareDatetime
    end_date: AwareDatetime
    created_at: AwareDatetime
    timezone: str = "UTC"
    scope: Literal["library", "book", "shelf"] = "library"
    scope_id: UUID | None = None
    book_ids: list[UUID] = Field(default_factory=list, max_length=1000)

    minimum_minutes: int = Field(
        default=5,
        ge=1,
        le=1440,
    )

    rules: list[GoalRule] = Field(default_factory=list, max_length=10000)
    is_active: bool = True
    is_recurring: bool = True
    is_archived: bool = False

    @field_validator("book_ids")
    @classmethod
    def unique_books(cls, value: list[UUID]) -> list[UUID]:
        return sorted(set(value), key=str)

    @property
    def selected_book_ids(self) -> list[UUID]:
        return self.book_ids or ([self.scope_id] if self.scope == "book" and self.scope_id else [])

    @field_validator("timezone")
    @classmethod
    def valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be an IANA identifier") from exc

        return value

    @model_validator(mode="after")
    def valid_rules(self) -> "GoalDefinition":
        if self.end_date <= self.start_date:
            raise ValueError("end_date must follow start_date")

        if (self.scope == "library") != (self.scope_id is None):
            raise ValueError("Only book and shelf scopes require scope_id")

        if self.book_ids and (self.scope != "book" or self.scope_id not in self.book_ids):
            raise ValueError("book_ids require a book scope containing scope_id")

        if self.book_ids and self.goal_type == GoalType.BOOKS_COUNT and self.target_value > len(self.book_ids):
            raise ValueError("Target cannot exceed the number of selected books")

        if len(self.book_ids) == 1:
            self.book_ids = []

        if self.time_period == TimePeriod.CUSTOM and self.is_recurring:
            raise ValueError("Custom deadlines cannot recur")

        dates = [rule.at for rule in self.rules]

        if dates != sorted(set(dates)):
            raise ValueError("Rule revisions must have unique ascending timestamps")

        if dates and dates[0] != self.created_at:
            raise ValueError("First rule must begin at goal creation")

        if self.rules:
            last = self.rules[-1]

            if (last.target, last.title, last.active, last.archived) != (
                self.target_value,
                self.title,
                self.is_active,
                self.is_archived,
            ):
                raise ValueError("Current settings must match the last rule")

        return self


class PageCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    key: str = Field(min_length=1, max_length=500)
    start: float = Field(ge=0, le=1)
    end: float = Field(ge=0, le=1)
    pages_per_unit: float = Field(gt=0, le=100000)
    estimated: bool = False

    @model_validator(mode="after")
    def ordered(self) -> "PageCoverage":
        if self.end <= self.start:
            raise ValueError("Coverage must have positive extent")

        return self


class Activity(TrackingPayload):
    session_id: UUID | None = None
    book_id: UUID
    book_title: str = Field(min_length=1, max_length=500)
    start_time: AwareDatetime
    end_time: AwareDatetime
    created_at: AwareDatetime
    source: Literal["reader", "manual"] = "manual"
    kind: Literal["reading", "completion", "reversal"] = "reading"

    device_id: str = Field(
        default="manual",
        min_length=1,
        max_length=255,
    )

    shelf_ids: list[UUID] = Field(default_factory=list, max_length=1000)

    pages: int = Field(
        default=0,
        ge=0,
        le=100000,
    )

    coverage: list[PageCoverage] = Field(default_factory=list, max_length=1000)
    note: str | None = Field(default=None, max_length=10000)
    correction_of: UUID | None = None

    @model_validator(mode="after")
    def valid_activity(self) -> "Activity":
        if self.end_time < self.start_time:
            raise ValueError("end_time must not precede start_time")

        if self.end_time > self.created_at:
            raise ValueError("Reading activity cannot be in the future")

        if (self.kind == "reversal") != (self.correction_of is not None):
            raise ValueError("Only reversals require correction_of")

        if self.kind != "reading" and (self.pages or self.coverage or self.end_time != self.start_time):
            raise ValueError("Completion/reversal records are points without pages")

        if self.source == "manual" and self.coverage:
            raise ValueError("Manual pages must use pages, not reader coverage")

        if self.source == "reader" and self.pages:
            raise ValueError("Reader pages require document coverage")

        return self


class PeriodRecord(TrackingPayload):
    goal_id: UUID
    definition: GoalDefinition

    @model_validator(mode="after")
    def valid_snapshot(self) -> "PeriodRecord":
        if self.goal_id != self.definition.id or self.definition.is_recurring:
            raise ValueError("Period snapshots must identify their non-recurring definition")

        return self
