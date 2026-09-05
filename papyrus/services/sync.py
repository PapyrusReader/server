"""Atomic PowerSync uploads for the user's owned library."""

from pathlib import Path
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.models import User
from papyrus.schemas.sync import PowerSyncCrudMutation
from papyrus.services import media as media_service
from papyrus.services.library_sync import apply_library_mutation


async def apply_powersync_upload_batch(
    session: AsyncSession,
    user_id: UUID,
    batch: list[PowerSyncCrudMutation],
) -> int:
    """Serialize each owner's queue transactions and commit mixed batches atomically."""
    applied_count = 0
    media_paths_to_delete: list[Path] = []

    try:
        await session.execute(select(User.user_id).where(User.user_id == user_id).with_for_update())

        for mutation in batch:
            applied, deleted_media_paths = await apply_library_mutation(session, user_id, mutation)
            applied_count += applied
            media_paths_to_delete.extend(deleted_media_paths)

        await session.commit()
    except Exception:
        await session.rollback()
        raise

    media_service.delete_physical_paths(media_paths_to_delete)
    return applied_count
