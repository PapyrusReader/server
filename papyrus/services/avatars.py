"""Private account photos stored under the existing owner media directory."""

from pathlib import Path
from uuid import UUID, uuid4

import structlog
from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.core.exceptions import ForbiddenError, NotFoundError, ValidationError
from papyrus.models import User
from papyrus.services.media import media_root

MAX_AVATAR_BYTES = 5 * 1024 * 1024
AVATAR_PREFIX = "/users/me/avatar/"
logger = structlog.get_logger(__name__)


async def lock_user(session: AsyncSession, user_id: UUID) -> User:
    user = await session.scalar(
        select(User).where(User.user_id == user_id).with_for_update().execution_options(populate_existing=True)
    )

    if user is None or user.disabled_at is not None:
        raise ForbiddenError("User account is unavailable")

    return user


def avatar_path(user_id: UUID, avatar_url: str | None) -> Path | None:
    if avatar_url is None or not avatar_url.startswith(AVATAR_PREFIX):
        return None

    try:
        avatar_id = UUID(avatar_url.removeprefix(AVATAR_PREFIX))
    except ValueError:
        return None

    return media_root() / str(user_id) / "avatars" / str(avatar_id)


def _content_type(contents: bytes) -> str:
    if contents.startswith(b"\xff\xd8\xff") and contents.endswith(b"\xff\xd9"):
        return "image/jpeg"

    if contents.startswith(b"\x89PNG\r\n\x1a\n") and contents.endswith(b"IEND\xaeB`\x82"):
        return "image/png"

    raise ValidationError("Choose a JPEG or PNG photo")


def delete_previous_avatar(user_id: UUID, avatar_url: str | None) -> None:
    path = avatar_path(user_id, avatar_url)

    if path is not None:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            logger.exception("Previous account photo cleanup failed", user_id=str(user_id))


async def upload_avatar(
    session: AsyncSession,
    user_id: UUID,
    *,
    file: UploadFile,
    display_name: str,
) -> User:
    """Commit the name and photo together, retaining the old photo on failure.

    Photos have a separate fixed 5 MiB allowance and are not library media assets.
    Owner-directory cleanup includes them when account deletion becomes permanent.
    """
    contents = await file.read(MAX_AVATAR_BYTES + 1)

    if len(contents) > MAX_AVATAR_BYTES:
        raise ValidationError("Photo must be 5 MB or smaller")

    _content_type(contents)
    user = await lock_user(session, user_id)
    previous_url = user.avatar_url
    avatar_id = uuid4()
    avatar_url = f"{AVATAR_PREFIX}{avatar_id}"
    path = media_root() / str(user_id) / "avatars" / str(avatar_id)
    path.parent.mkdir(parents=True, exist_ok=True)

    try:
        path.write_bytes(contents)
        user.avatar_url = avatar_url
        user.display_name = display_name
        await session.commit()
    except Exception:
        await session.rollback()
        path.unlink(missing_ok=True)
        raise

    delete_previous_avatar(user_id, previous_url)
    await session.refresh(user)
    return user


async def download_avatar(session: AsyncSession, user_id: UUID, avatar_id: UUID) -> tuple[bytes, str]:
    user = await session.get(User, user_id)
    avatar_url = f"{AVATAR_PREFIX}{avatar_id}"

    if user is None or user.avatar_url != avatar_url:
        raise NotFoundError("Photo was not found")

    path = avatar_path(user_id, avatar_url)

    if path is None or not path.is_file():
        raise NotFoundError("Photo was not found")

    contents = path.read_bytes()
    return contents, _content_type(contents)
