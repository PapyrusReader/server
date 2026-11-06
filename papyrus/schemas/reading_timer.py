"""Timestamp-based timer payloads shared with the offline client."""

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class TimerInterval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: AwareDatetime
    end: AwareDatetime


class TimerPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    book_id: UUID
    book_title: str = Field(min_length=1, max_length=500)
    started_at: AwareDatetime
    device_id: str = Field(min_length=1, max_length=255)
    shelf_ids: list[UUID] = Field(max_length=1000)
    start_page: int = Field(ge=0)
    phase: Literal["running", "paused", "finished"]
    target_seconds: int | None = Field(default=None, ge=60, le=86400)
    running_since: AwareDatetime | None = None
    intervals: list[TimerInterval] = Field(max_length=10000)
    revision: int = Field(ge=0)
    version: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def valid_timeline(self) -> "TimerPayload":
        latest = self.started_at
        recorded = timedelta()
        future = datetime.now(UTC) + timedelta(minutes=5)

        for interval in self.intervals:
            if interval.start < latest or interval.end <= interval.start:
                raise ValueError("Timer intervals must be positive and ordered without overlap")

            latest = interval.end
            recorded += interval.end - interval.start

        if (self.phase == "running") != (self.running_since is not None):
            raise ValueError("Only running timers require running_since")

        if self.running_since is not None:
            if self.running_since < latest:
                raise ValueError("Running time cannot overlap recorded intervals")

            latest = self.running_since

        if latest > future:
            raise ValueError("Timer cannot begin in the future")

        if self.target_seconds is not None and recorded > timedelta(seconds=self.target_seconds):
            raise ValueError("Recorded time exceeds the countdown goal")

        return self
