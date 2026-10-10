"""Owned tracking records survive library entity deletion."""

from uuid import UUID, uuid4

from sqlalchemy import Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from papyrus.core.database import Base
from papyrus.models.library import LibraryEntity


class TrackingRecord(LibraryEntity):
    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)


class SyncReadingGoal(TrackingRecord, Base):
    __tablename__ = "reading_goals"


class SyncReadingActivity(TrackingRecord, Base):
    __tablename__ = "reading_activities"


class SyncGoalPeriod(TrackingRecord, Base):
    __tablename__ = "goal_periods"
