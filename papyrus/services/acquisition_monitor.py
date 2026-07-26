"""Monitor user-submitted qBittorrent acquisition jobs."""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Protocol
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from papyrus.config import get_settings
from papyrus.core.exceptions import AppError, ConflictError, ForbiddenError, NotFoundError, ValidationError
from papyrus.models import AcquisitionEndpoint, AcquisitionJob, MediaAsset
from papyrus.services import media as media_service
from papyrus.services.acquisition import QbittorrentClient, QbittorrentFile, QbittorrentTorrent

ACTIVE_JOB_STATUSES = ("queued", "submitted", "downloading", "importing")
MAX_MISSING_TORRENT_RETRIES = 3

logger = logging.getLogger(__name__)


class QbittorrentMonitorClient(Protocol):
    async def find_torrent(
        self,
        *,
        tag: str,
        torrent_hash: str | None = None,
    ) -> QbittorrentTorrent: ...

    async def files(self, torrent_hash: str) -> list[QbittorrentFile]: ...

    async def pause(self, torrent_hash: str) -> None: ...


QbittorrentClientFactory = Callable[
    [AcquisitionEndpoint],
    Awaitable[QbittorrentMonitorClient],
]
Sleep = Callable[[float], Awaitable[None]]


async def claim_due_jobs(
    session: AsyncSession,
    worker_id: str,
    *,
    now: datetime | None = None,
    limit: int = 50,
) -> list[UUID]:
    claimed_at = now or datetime.now(UTC)
    lease_until = claimed_at + timedelta(seconds=get_settings().acquisition_monitor_lease_seconds)
    result = await session.execute(
        select(AcquisitionJob)
        .join(
            AcquisitionEndpoint,
            AcquisitionEndpoint.endpoint_id == AcquisitionJob.endpoint_id,
        )
        .where(
            AcquisitionEndpoint.kind == "qbittorrent",
            AcquisitionJob.status.in_(ACTIVE_JOB_STATUSES),
            AcquisitionJob.rule_id.is_(None),
            AcquisitionJob.book_id.is_not(None),
            AcquisitionJob.download_url.is_(None),
            or_(
                AcquisitionJob.next_poll_at.is_(None),
                AcquisitionJob.next_poll_at <= claimed_at,
            ),
            or_(
                AcquisitionJob.lease_until.is_(None),
                AcquisitionJob.lease_until <= claimed_at,
            ),
        )
        .order_by(
            AcquisitionJob.next_poll_at.asc().nulls_first(),
            AcquisitionJob.created_at,
        )
        .with_for_update(skip_locked=True)
        .limit(limit)
    )
    jobs = result.scalars().all()

    for job in jobs:
        job.lease_owner = worker_id
        job.lease_until = lease_until

    await session.commit()

    return [job.job_id for job in jobs]


async def run_monitor(
    session_maker: async_sessionmaker[AsyncSession],
    *,
    import_root: Path,
    worker_id: str | None = None,
    sleep: Sleep | None = None,
) -> None:
    monitor_worker_id = worker_id or f"acquisition-monitor:{uuid4()}"
    pause = sleep or asyncio.sleep

    while True:
        job_ids: list[UUID] = []

        try:
            async with session_maker() as session:
                job_ids = await claim_due_jobs(session, monitor_worker_id)

                if job_ids:
                    await process_claimed_jobs(
                        session,
                        job_ids,
                        import_root=import_root,
                        worker_id=monitor_worker_id,
                    )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Acquisition monitor cycle failed")

        settings = get_settings()
        delay = (
            settings.acquisition_monitor_active_interval_seconds
            if job_ids
            else settings.acquisition_monitor_idle_interval_seconds
        )

        await pause(delay)


async def process_claimed_jobs(
    session: AsyncSession,
    job_ids: Sequence[UUID],
    *,
    import_root: Path,
    client_factory: QbittorrentClientFactory | None = None,
    worker_id: str | None = None,
    now: datetime | None = None,
) -> None:
    if not job_ids:
        return

    result = await session.execute(
        select(AcquisitionJob.job_id, AcquisitionJob.endpoint_id).where(AcquisitionJob.job_id.in_(job_ids))
    )
    jobs_by_endpoint: dict[UUID, list[UUID]] = defaultdict(list)
    jobs_without_endpoint: list[UUID] = []

    for job_id, endpoint_id in result:
        if endpoint_id is None:
            jobs_without_endpoint.append(job_id)
        else:
            jobs_by_endpoint[endpoint_id].append(job_id)

    observed_at = now or datetime.now(UTC)

    for job_id in jobs_without_endpoint:
        await _mark_failed_job(
            session,
            job_id,
            "Acquisition job is missing its download client",
            observed_at,
            worker_id=worker_id,
        )

    connect = client_factory or QbittorrentClient.connect

    for endpoint_id, endpoint_job_ids in jobs_by_endpoint.items():
        endpoint = await session.get(AcquisitionEndpoint, endpoint_id)

        if endpoint is None:
            for job_id in endpoint_job_ids:
                await _mark_failed_job(
                    session,
                    job_id,
                    "Acquisition download client was not found",
                    observed_at,
                    worker_id=worker_id,
                )

            continue

        try:
            client = await connect(endpoint)
        except Exception as exc:
            await session.rollback()
            for job_id in endpoint_job_ids:
                await _reschedule_job(
                    session,
                    job_id,
                    _exception_message(exc),
                    observed_at,
                    worker_id=worker_id,
                )

            continue

        for job_id in endpoint_job_ids:
            try:
                await process_job(
                    session,
                    job_id,
                    client,
                    import_root=import_root,
                    worker_id=worker_id,
                    now=observed_at,
                )
            except Exception as exc:
                await session.rollback()

                if _is_terminal_error(exc):
                    await _mark_failed_job(
                        session,
                        job_id,
                        _exception_message(exc),
                        observed_at,
                        worker_id=worker_id,
                    )
                elif _is_missing_torrent_error(exc):
                    await _reschedule_missing_torrent(
                        session,
                        job_id,
                        _exception_message(exc),
                        observed_at,
                        worker_id=worker_id,
                    )
                else:
                    await _reschedule_job(
                        session,
                        job_id,
                        _exception_message(exc),
                        observed_at,
                        worker_id=worker_id,
                    )


async def process_job(
    session: AsyncSession,
    job_id: UUID,
    client: QbittorrentMonitorClient,
    *,
    import_root: Path,
    worker_id: str | None = None,
    now: datetime | None = None,
) -> None:
    job = await _locked_claimed_job(
        session,
        job_id,
        worker_id=worker_id,
    )

    if job is None:
        return

    observed_at = now or datetime.now(UTC)
    torrent = await client.find_torrent(
        tag=f"papyrus:{job.job_id}",
        torrent_hash=job.client_hash,
    )
    job.client_hash = torrent.hash
    job.client_state = torrent.state
    job.progress_basis_points = torrent.progress_basis_points
    job.downloaded_bytes = torrent.downloaded_bytes
    job.total_bytes = torrent.total_bytes
    job.download_speed_bytes_per_second = torrent.download_speed_bytes_per_second
    job.eta_seconds = torrent.eta_seconds
    job.error = None

    if job.submitted_at is None:
        job.submitted_at = observed_at

    if job.started_at is None and torrent.downloaded_bytes > 0:
        job.started_at = observed_at

    job.updated_at = observed_at

    if job.status == "queued":
        job.status = "submitted"

    await session.commit()

    job = await _locked_claimed_job(
        session,
        job_id,
        worker_id=worker_id,
    )

    if job is None:
        return

    if torrent.progress_basis_points < 10_000:
        job.status = "downloading"
        job.next_poll_at = _next_active_poll(observed_at)
        _release_lease(job)

        await session.commit()
        return

    files = await client.files(torrent.hash)
    candidates = [file for file in files if _supported_book_file(file.name)]

    if job.selected_file_path is not None:
        candidates = [file for file in candidates if file.name == job.selected_file_path]

    if not candidates:
        raise ValidationError("Downloaded release does not contain a supported book file")

    if len(candidates) > 1:
        await client.pause(torrent.hash)

        job.status = "needs_file_selection"
        job.next_poll_at = None
        _release_lease(job)

        await session.commit()
        return

    selected = candidates[0]

    if selected.progress_basis_points < 10_000:
        job.status = "downloading"
        job.selected_file_path = selected.name
        job.next_poll_at = _next_active_poll(observed_at)
        _release_lease(job)

        await session.commit()
        return

    source_path = _resolve_import_path(
        import_root,
        job.owner_user_id,
        job.job_id,
        selected.name,
    )
    job.status = "importing"
    job.selected_file_path = selected.name
    job.next_poll_at = None

    if job.book_id is None:
        raise ValidationError("Acquisition job is missing its placeholder book")

    def complete_import(_: MediaAsset) -> None:
        job.status = "completed"
        job.progress_basis_points = 10_000
        job.completed_at = observed_at
        job.updated_at = observed_at
        job.error = None
        _release_lease(job)

    await media_service.import_media_path(
        session,
        job.owner_user_id,
        book_id=job.book_id,
        kind="book_file",
        source_path=source_path,
        before_commit=complete_import,
    )


async def _mark_failed_job(
    session: AsyncSession,
    job_id: UUID,
    message: str,
    observed_at: datetime,
    *,
    worker_id: str | None = None,
) -> None:
    job = await _locked_claimed_job(
        session,
        job_id,
        worker_id=worker_id,
    )

    if job is None:
        return

    job.status = "failed"
    job.error = message
    job.next_poll_at = None
    job.updated_at = observed_at
    _release_lease(job)

    await session.commit()


async def _reschedule_job(
    session: AsyncSession,
    job_id: UUID,
    message: str,
    observed_at: datetime,
    *,
    worker_id: str | None = None,
) -> None:
    job = await _locked_claimed_job(
        session,
        job_id,
        worker_id=worker_id,
    )

    if job is None:
        return

    job.retry_count += 1
    job.error = message
    job.next_poll_at = observed_at + _retry_delay(job.retry_count)
    job.updated_at = observed_at
    _release_lease(job)

    await session.commit()


async def _reschedule_missing_torrent(
    session: AsyncSession,
    job_id: UUID,
    message: str,
    observed_at: datetime,
    *,
    worker_id: str | None = None,
) -> None:
    job = await _locked_claimed_job(
        session,
        job_id,
        worker_id=worker_id,
    )

    if job is None:
        return

    job.retry_count += 1
    job.error = message
    job.updated_at = observed_at

    if job.retry_count >= MAX_MISSING_TORRENT_RETRIES:
        job.status = "failed"
        job.next_poll_at = None
    else:
        job.next_poll_at = observed_at + _retry_delay(job.retry_count)

    _release_lease(job)

    await session.commit()


async def _locked_claimed_job(
    session: AsyncSession,
    job_id: UUID,
    *,
    worker_id: str | None,
) -> AcquisitionJob | None:
    result = await session.execute(
        select(AcquisitionJob)
        .where(AcquisitionJob.job_id == job_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    job = result.scalar_one_or_none()

    if job is None or job.status not in ACTIVE_JOB_STATUSES:
        return None

    if worker_id is not None and job.lease_owner != worker_id:
        return None

    return job


def _is_terminal_error(exc: Exception) -> bool:
    if isinstance(exc, ValidationError | ConflictError | ForbiddenError):
        return True

    return isinstance(exc, HTTPException) and 400 <= exc.status_code < 500 and exc.status_code != 404


def _is_missing_torrent_error(exc: Exception) -> bool:
    return isinstance(exc, HTTPException) and exc.status_code == 404


def _exception_message(exc: Exception) -> str:
    if isinstance(exc, AppError):
        return exc.message

    if isinstance(exc, HTTPException):
        return str(exc.detail)

    return "Acquisition monitoring failed"


def _next_active_poll(now: datetime) -> datetime:
    return now + timedelta(seconds=get_settings().acquisition_monitor_active_interval_seconds)


def _retry_delay(attempt: int) -> timedelta:
    base_seconds = max(
        get_settings().acquisition_monitor_active_interval_seconds,
        1,
    )
    seconds = min(300, base_seconds * (2 ** min(max(attempt - 1, 0), 8)))
    return timedelta(seconds=seconds)


def _release_lease(job: AcquisitionJob) -> None:
    job.lease_owner = None
    job.lease_until = None


def _supported_book_file(filename: str) -> bool:
    normalized = filename.replace("\\", "/")
    extension = PurePosixPath(normalized).suffix.lower().lstrip(".")
    return extension in media_service.BOOK_EXTENSIONS


def _resolve_import_path(
    import_root: Path,
    owner_user_id: UUID,
    job_id: UUID,
    filename: str,
) -> Path:
    relative = PurePosixPath(filename.replace("\\", "/"))

    if relative.is_absolute() or ".." in relative.parts:
        raise ValidationError("Downloaded file path is outside its acquisition directory")

    try:
        resolved_import_root = import_root.resolve(strict=True)
    except FileNotFoundError as exc:
        raise NotFoundError("Acquisition import root was not found") from exc

    owner_root = import_root / str(owner_user_id)
    job_root = owner_root / str(job_id)

    if owner_root.is_symlink() or job_root.is_symlink():
        raise ValidationError("Downloaded file path contains a symbolic link")

    try:
        resolved_root = job_root.resolve(strict=True)
    except FileNotFoundError as exc:
        raise NotFoundError("Acquisition download directory was not found") from exc

    if not resolved_root.is_relative_to(resolved_import_root):
        raise ValidationError("Downloaded file path is outside its acquisition directory")

    candidate = resolved_root

    for part in relative.parts:
        candidate /= part

        if candidate.is_symlink():
            raise ValidationError("Downloaded file path contains a symbolic link")

    try:
        resolved_candidate = candidate.resolve(strict=True)
    except FileNotFoundError as exc:
        raise NotFoundError("Downloaded book file was not found") from exc

    if not resolved_candidate.is_relative_to(resolved_root) or not resolved_candidate.is_file():
        raise ValidationError("Downloaded file path is outside its acquisition directory")

    return resolved_candidate
