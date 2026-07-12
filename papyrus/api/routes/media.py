"""Authenticated private media routes."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, Response, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.api.deps import CurrentUserId
from papyrus.config import get_settings
from papyrus.core.database import get_db
from papyrus.core.exceptions import NotFoundError
from papyrus.core.rate_limit import limiter
from papyrus.schemas.media import MediaAssetResponse, MediaUsageResponse
from papyrus.services import media as media_service

router = APIRouter()
DBSession = Annotated[AsyncSession, Depends(get_db)]


@router.post("", response_model=MediaAssetResponse, status_code=status.HTTP_201_CREATED, summary="Upload private media")
@limiter.limit(lambda: f"{get_settings().rate_limit_upload}/minute")
async def upload_media(
    request: Request,
    user_id: CurrentUserId,
    db: DBSession,
    book_id: Annotated[UUID, Form()],
    kind: Annotated[str, Form()],
    file: Annotated[UploadFile, File()],
) -> MediaAssetResponse:
    """Upload a book file or cover image for the authenticated user."""
    asset = await media_service.upload_media(db, user_id, book_id=book_id, kind=kind, file=file)
    return MediaAssetResponse.model_validate(asset)


@router.get("/usage", response_model=MediaUsageResponse, summary="Get media storage usage")
async def get_media_usage(user_id: CurrentUserId, db: DBSession) -> MediaUsageResponse:
    """Return authenticated user's file storage usage."""
    used_bytes, quota_bytes, available_bytes = await media_service.usage(db, user_id)
    return MediaUsageResponse(used_bytes=used_bytes, quota_bytes=quota_bytes, available_bytes=available_bytes)


@router.get("/{asset_id}", summary="Download private media")
async def download_media(user_id: CurrentUserId, db: DBSession, asset_id: UUID) -> FileResponse:
    """Download an owned media asset."""
    asset = await media_service.get_owned_asset(db, user_id, asset_id)
    path = media_service.asset_path(asset)
    if not path.exists():
        raise NotFoundError("Media asset was not found")
    return FileResponse(path, media_type=asset.content_type, filename=asset.original_filename)


@router.delete("/{asset_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete private media")
async def delete_media(user_id: CurrentUserId, db: DBSession, asset_id: UUID) -> Response:
    """Delete an owned media asset."""
    await media_service.delete_media(db, user_id, asset_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
