from uuid import UUID

from sqlalchemy import case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.config import get_settings
from papyrus.core.exceptions import NotFoundError
from papyrus.models import AuthSession, MediaAsset, SyncBook, User
from papyrus.schemas.admin import DirectoryUser, UserDirectory
from papyrus.schemas.common import Pagination
from papyrus.schemas.user import AccountRole


async def set_role(session: AsyncSession, user_id: UUID, role: AccountRole) -> None:
    changed = await session.scalar(
        update(User).where(User.user_id == user_id).values(role=role).returning(User.user_id)
    )

    if changed is None:
        raise NotFoundError("User not found")

    await session.commit()


async def list_users(session: AsyncSession, page: int, limit: int) -> UserDirectory:
    total = (await session.scalar(select(func.count()).select_from(User))) or 0
    pages = (total + limit - 1) // limit

    accounts = (
        select(
            User.user_id,
            User.display_name,
            User.primary_email.label("email"),
            User.role,
            User.created_at,
            case(
                (User.deletion_due_at.is_not(None), "pending_deletion"),
                (User.disabled_at.is_not(None), "disabled"),
                (User.email_verified_at.is_(None), "unverified"),
                else_="active",
            ).label("status"),
        )
        .order_by(User.created_at.desc(), User.user_id)
        .offset((page - 1) * limit)
        .limit(limit)
        .subquery()
    )

    books = select(func.count()).select_from(SyncBook).where(SyncBook.owner_user_id == accounts.c.user_id)

    usage = select(func.coalesce(func.sum(MediaAsset.size_bytes), 0)).where(
        MediaAsset.owner_user_id == accounts.c.user_id
    )

    login = select(func.max(AuthSession.created_at)).where(AuthSession.user_id == accounts.c.user_id)

    rows = await session.execute(
        select(
            accounts,
            books.scalar_subquery().label("book_count"),
            usage.scalar_subquery().label("used_bytes"),
            login.scalar_subquery().label("last_sign_in_at"),
        ).order_by(accounts.c.created_at.desc(), accounts.c.user_id)
    )

    return UserDirectory(
        users=[DirectoryUser.model_validate(row) for row in rows.mappings()],
        quota_bytes=get_settings().file_storage_quota_bytes,
        pagination=Pagination(
            page=page,
            limit=limit,
            total=total,
            total_pages=pages,
            has_prev=page > 1,
            has_next=page < pages,
        ),
    )
