from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.api.deps import get_current_auth_session
from papyrus.core.database import get_db
from papyrus.models import AuthSession
from papyrus.schemas.admin import DirectorySort, UserDirectory
from papyrus.services import admin


async def require_admin(auth_session: Annotated[AuthSession, Depends(get_current_auth_session)]) -> None:
    if auth_session.user.role != "admin":
        raise HTTPException(
            status_code=403, detail={"code": "ADMIN_REQUIRED", "message": "Administrator role required"}
        )


router = APIRouter(dependencies=[Depends(require_admin)])


@router.get("/users", summary="Inspect accounts and server library storage")
async def list_users(
    response: Response,
    db: Annotated[AsyncSession, Depends(get_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    sort_by: DirectorySort = "display_name",
    descending: bool = False,
) -> UserDirectory:
    response.headers["Cache-Control"] = "private, no-store"
    return await admin.list_users(db, page, limit, sort_by, descending)
