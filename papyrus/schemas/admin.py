from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

from papyrus.schemas.common import Pagination
from papyrus.schemas.user import AccountRole


class DirectoryUser(BaseModel):
    user_id: UUID
    display_name: str
    email: str | None
    role: AccountRole
    status: Literal["active", "unverified", "disabled", "pending_deletion"]
    created_at: datetime
    last_sign_in_at: datetime | None
    book_count: int
    used_bytes: int


class UserDirectory(BaseModel):
    users: list[DirectoryUser]
    quota_bytes: int
    pagination: Pagination
