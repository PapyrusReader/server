"""API dependencies for dependency injection."""

from typing import Annotated, Any
from uuid import UUID

from fastapi import Depends, HTTPException, Query, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from papyrus.core.security import decode_token

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


async def get_current_user_id(
    payload: Annotated[dict[str, Any], Depends(get_current_access_token_payload)],
) -> UUID:
    """Extract the current user ID from the validated access token payload."""
    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"code": "INVALID_TOKEN", "message": "Token missing user ID"},
        )

    return UUID(user_id)


async def get_current_session_id(
    payload: Annotated[dict[str, Any], Depends(get_current_access_token_payload)],
) -> UUID | None:
    """Extract the current session ID from the validated access token payload."""
    session_id = payload.get("sid")
    if session_id is None:
        return None
    return UUID(session_id)


CurrentUserId = Annotated[UUID, Depends(get_current_user_id)]
CurrentAccessTokenPayload = Annotated[dict[str, Any], Depends(get_current_access_token_payload)]
CurrentSessionId = Annotated[UUID | None, Depends(get_current_session_id)]


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
