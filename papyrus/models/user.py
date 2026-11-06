"""User database model."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import CheckConstraint, DateTime, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from papyrus.core.database import Base


class User(Base):
    """Persisted user account and profile state."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            "(email_verified_at IS NULL AND email_verification_source IS NULL) OR "
            "(primary_email IS NOT NULL AND email_verified_at IS NOT NULL "
            "AND email_verification_source IS NOT NULL "
            "AND email_verification_source IN ('papyrus_email', 'google'))",
            name="ck_users_email_verification",
        ),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=uuid4)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    avatar_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    primary_email: Mapped[str | None] = mapped_column(String(255), unique=True, nullable=True)

    email_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    email_verification_source: Mapped[str | None] = mapped_column(String(32), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    deletion_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deletion_due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    identities: Mapped[list[UserIdentity]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    password_credential: Mapped[PasswordCredential | None] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )

    sessions: Mapped[list[AuthSession]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    @property
    def email(self) -> str | None:
        return self.primary_email

    @property
    def email_verified(self) -> bool:
        return self.email_verified_at is not None


from papyrus.models.auth import AuthSession, PasswordCredential, UserIdentity  # noqa: E402
