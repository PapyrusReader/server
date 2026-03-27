"""User routes."""

from typing import Annotated

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.api.deps import CurrentUserId
from papyrus.core.database import get_db
from papyrus.schemas.auth import ChangePasswordRequest
from papyrus.schemas.common import MessageResponse
from papyrus.schemas.user import DeleteAccountRequest, UpdateUserRequest, User, UserPreferences
from papyrus.services import users as user_service

router = APIRouter()
DBSession = Annotated[AsyncSession, Depends(get_db)]


@router.get(
    "/me",
    response_model=User,
    summary="Get current user profile",
)
async def get_current_user(user_id: CurrentUserId, db: DBSession) -> User:
    """Return the authenticated user's profile information."""
    user = await user_service.get_user_profile(db, user_id)
    return User.model_validate(user)


@router.patch(
    "/me",
    response_model=User,
    summary="Update current user profile",
)
async def update_current_user(
    user_id: CurrentUserId,
    request: UpdateUserRequest,
    db: DBSession,
) -> User:
    """Update the authenticated user's profile information."""
    user = await user_service.update_user_profile(db, user_id, request)
    return User.model_validate(user)


@router.delete(
    "/me",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete current user account",
)
async def delete_current_user(
    user_id: CurrentUserId,
    request: DeleteAccountRequest,
    db: DBSession,
) -> Response:
    """Disable the user account and revoke active sessions."""
    await user_service.delete_user_account(db, user_id, request.password)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get(
    "/me/preferences",
    response_model=UserPreferences,
    summary="Get user preferences",
)
async def get_user_preferences(user_id: CurrentUserId) -> UserPreferences:
    """Return the user's application preferences."""
    return UserPreferences(
        theme="dark",
        notifications_enabled=True,
        default_shelf="Currently Reading",
        sync_wifi_only=False,
    )


@router.put(
    "/me/preferences",
    response_model=UserPreferences,
    summary="Update user preferences",
)
async def update_user_preferences(
    user_id: CurrentUserId,
    request: UserPreferences,
) -> UserPreferences:
    """Update the user's application preferences."""
    return request


@router.post(
    "/me/change-password",
    response_model=MessageResponse,
    summary="Change password",
)
async def change_password(
    user_id: CurrentUserId,
    request: ChangePasswordRequest,
    db: DBSession,
) -> MessageResponse:
    """Change the user's password and revoke existing sessions."""
    await user_service.change_user_password(db, user_id, request.current_password, request.new_password)
    return MessageResponse(message="Password changed successfully")
