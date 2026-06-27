"""Media asset schemas."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class MediaAssetResponse(BaseModel):
    """Uploaded media asset metadata."""

    model_config = ConfigDict(from_attributes=True)

    asset_id: UUID
    owner_user_id: UUID
    book_id: UUID
    kind: str
    original_filename: str
    content_type: str
    extension: str
    size_bytes: int
    sha256: str
    storage_path: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class MediaUsageResponse(BaseModel):
    """User media storage quota usage."""

    used_bytes: int
    quota_bytes: int
    available_bytes: int
