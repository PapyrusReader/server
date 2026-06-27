"""PowerSync upload routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.api.deps import CurrentUserId
from papyrus.config import get_settings
from papyrus.core.database import get_db
from papyrus.core.rate_limit import limiter
from papyrus.schemas.sync import (
    DataSyncSettingsResponse,
    FileStorageSettings,
    PowerSyncUploadRequest,
    PowerSyncUploadResponse,
)
from papyrus.services import sync as sync_service

router = APIRouter()
DBSession = Annotated[AsyncSession, Depends(get_db)]


@router.get(
    "/settings",
    response_model=DataSyncSettingsResponse,
    summary="Get public data sync settings",
)
async def get_data_sync_settings() -> DataSyncSettingsResponse:
    """Return public sync capabilities for client custom-server setup."""
    settings = get_settings()
    return DataSyncSettingsResponse(
        data_sync_url=settings.powersync_service_url,
        file_storage=FileStorageSettings(
            supported=True,
            quota_bytes=settings.file_storage_quota_bytes,
        ),
    )


@router.post(
    "/powersync-upload",
    response_model=PowerSyncUploadResponse,
    summary="Upload PowerSync client-side mutations",
)
@limiter.limit(lambda: f"{get_settings().rate_limit_batch}/minute")
async def upload_powersync_changes(
    request: Request,
    user_id: CurrentUserId,
    payload: PowerSyncUploadRequest,
    db: DBSession,
) -> PowerSyncUploadResponse:
    """Apply one PowerSync CRUD transaction atomically."""
    applied_count = await sync_service.apply_powersync_upload_batch(db, user_id, payload.batch)
    return PowerSyncUploadResponse(applied_count=applied_count)
