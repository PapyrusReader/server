"""Owned activity-backed reading progress and statistics routes."""

from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.api.deps import CurrentUserId, Pagination
from papyrus.core.database import get_db
from papyrus.schemas.progress import CreateReadingSessionRequest, ReadingSession, ReadingSessionList, ReadingStatistics
from papyrus.services import progress as service

router = APIRouter()
DBSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("/sessions", response_model=ReadingSessionList, summary="List reading sessions")
async def list_reading_sessions(
    user_id: CurrentUserId,
    pagination: Pagination,
    db: DBSession,
    book_id: UUID | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> ReadingSessionList:
    return await service.list_sessions(db, user_id, pagination, book_id, start_date, end_date)


@router.post("/sessions", response_model=ReadingSession, summary="Record reading session")
async def create_reading_session(
    user_id: CurrentUserId, request: CreateReadingSessionRequest, db: DBSession
) -> ReadingSession:
    return await service.create_session(db, user_id, request)


@router.get("/statistics", response_model=ReadingStatistics, summary="Get reading statistics")
async def get_reading_statistics(
    user_id: CurrentUserId, db: DBSession, start_date: date | None = None, end_date: date | None = None
) -> ReadingStatistics:
    return await service.statistics(db, user_id, start_date, end_date)
