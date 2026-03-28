"""API dependencies for dependency injection."""

from datetime import UTC, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from papyrus.core.database import get_db
from papyrus.core.security import decode_token
from papyrus.models import AuthSession

security = HTTPBearer()


async def get_current_access_token_payload(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> dict[str, Any]:
    """Extract and validate the current access token payload."""
    token = credentials.credentials
    payload = decode_token(token)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TOKEN", "message": "Invalid or expired token"},
        )

    if payload.get("type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TOKEN_TYPE", "message": "Not an access token"},
        )

    return payload


async def get_current_auth_session(
    payload: Annotated[dict[str, Any], Depends(get_current_access_token_payload)],
    db: Annotated[AsyncSession, Depends(get_db)],
) -> AuthSession:
    """Load and validate the current authenticated session from the database."""
    user_id = payload.get("sub")
    session_id = payload.get("sid")

    if not user_id or not session_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TOKEN", "message": "Token missing session context"},
        )

    try:
        session_uuid = UUID(str(session_id))
        user_uuid = UUID(str(user_id))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TOKEN", "message": "Token contains malformed identifiers"},
        ) from exc

    result = await db.execute(
        select(AuthSession).options(selectinload(AuthSession.user)).where(AuthSession.session_id == session_uuid)
    )

    auth_session = result.scalar_one_or_none()

    if auth_session is None or auth_session.user_id != user_uuid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_SESSION", "message": "Session is invalid"},
        )

    if auth_session.revoked_at is not None or auth_session.expires_at <= datetime.now(UTC):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "SESSION_REVOKED", "message": "Session is expired or revoked"},
        )

    if auth_session.user.disabled_at is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"code": "ACCOUNT_DISABLED", "message": "User account is disabled"},
        )

    return auth_session


async def get_current_user_id(
    auth_session: Annotated[AuthSession, Depends(get_current_auth_session)],
) -> UUID:
    """Extract the current user ID from the validated session."""
    return auth_session.user_id


async def get_current_session_id(
    auth_session: Annotated[AuthSession, Depends(get_current_auth_session)],
) -> UUID:
    """Extract the current session ID from the validated session."""
    return auth_session.session_id


CurrentUserId = Annotated[UUID, Depends(get_current_user_id)]
CurrentAccessTokenPayload = Annotated[dict[str, Any], Depends(get_current_access_token_payload)]
CurrentSessionId = Annotated[UUID, Depends(get_current_session_id)]


class PaginationParams:
    """Common pagination parameters."""

    def __init__(
        self,
        page: Annotated[int, Query(ge=1, description="Page number")] = 1,
        limit: Annotated[int, Query(ge=1, le=100, description="Items per page")] = 20,
        sort: Annotated[
            str | None,
            Query(description="Sort field with optional - prefix for descending"),
        ] = None,
    ):
        self.page = page
        self.limit = limit
        self.sort = sort
        self.offset = (page - 1) * limit


Pagination = Annotated[PaginationParams, Depends()]
