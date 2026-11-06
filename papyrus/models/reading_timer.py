"""Owned timer state, including terminal records that prevent replayed saves."""

from sqlalchemy import CheckConstraint, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from papyrus.core.database import Base
from papyrus.models.tracking import TrackingRecord


class SyncReadingTimer(TrackingRecord, Base):
    __tablename__ = "reading_timers"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'saved', 'discarded')", name="reading_timers_status"),
        Index(
            "uq_reading_timers_active_owner", "owner_user_id", unique=True, postgresql_where=text("status = 'active'")
        ),
    )

    status: Mapped[str] = mapped_column(Text)
    base_version: Mapped[str | None] = mapped_column(Text)
