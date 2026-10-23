"""Restricted account deletion and recovery contracts."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field

from papyrus.schemas.auth import ClientType


class AccountPasswordRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    expected_user_id: UUID | None = None


class AccountGoogleExchangeRequest(BaseModel):
    code: str = Field(min_length=1, max_length=512)
    verifier: str = Field(min_length=43, max_length=128)


class AccountActionRequest(BaseModel):
    authorization_token: str = Field(min_length=1, max_length=512)


class AccountRecoveryRequest(AccountActionRequest):
    client_type: ClientType = "unknown"
    device_label: str | None = Field(default=None, max_length=255)


class AccountDeletionAuthorization(BaseModel):
    authorization_token: str
    user_id: UUID
    email: str | None
    deletion_pending: bool
    deletion_due_at: datetime
    expires_at: datetime


class AccountDeletionStatus(BaseModel):
    deletion_due_at: datetime | None
    message: str
