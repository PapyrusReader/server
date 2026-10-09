"""Owner-scoped acquisition job lifecycle and rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.config import get_settings
from papyrus.core.security import encrypt_secret_payload
from papyrus.models.acquisition import AcquisitionEndpoint, AcquisitionJob, AcquisitionRule
from papyrus.models.sync import SyncBook
from papyrus.schemas.acquisition import AcquisitionEndpointTest
from papyrus.services.media import BOOK_EXTENSIONS

from .providers import QbittorrentClient, dispatch_arr_command, search_endpoint, submit_to_client
from .release_tokens import decode_release_token
from .transport import _credentials
from .types import BatchSubmissionResult, JobFileCandidate

SUBMISSION_LEASE_LIFETIME = timedelta(minutes=5)


async def owned_endpoint(session: AsyncSession, owner_user_id: Any, endpoint_id: Any) -> AcquisitionEndpoint:
    result = await session.execute(
        select(AcquisitionEndpoint).where(
            AcquisitionEndpoint.endpoint_id == endpoint_id, AcquisitionEndpoint.owner_user_id == owner_user_id
        )
    )

    endpoint = result.scalar_one_or_none()

    if endpoint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Acquisition endpoint not found")

    return endpoint


async def owned_job(
    session: AsyncSession,
    owner_user_id: UUID,
    job_id: UUID,
    *,
    for_update: bool = False,
) -> AcquisitionJob:
    statement = select(AcquisitionJob).where(
        AcquisitionJob.job_id == job_id,
        AcquisitionJob.owner_user_id == owner_user_id,
    )

    if for_update:
        statement = statement.with_for_update()

    result = await session.execute(statement.execution_options(populate_existing=for_update))
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(status_code=404, detail="Acquisition job not found")

    return job


async def paginated_jobs(
    session: AsyncSession,
    owner_user_id: UUID,
    *,
    limit: int,
    offset: int,
) -> tuple[list[AcquisitionJob], int]:
    total = await session.scalar(
        select(func.count()).select_from(AcquisitionJob).where(AcquisitionJob.owner_user_id == owner_user_id)
    )

    result = await session.execute(
        select(AcquisitionJob)
        .where(AcquisitionJob.owner_user_id == owner_user_id)
        .order_by(AcquisitionJob.created_at.desc(), AcquisitionJob.job_id.desc())
        .limit(limit)
        .offset(offset)
    )

    return list(result.scalars()), total or 0


async def job_file_candidates(
    session: AsyncSession,
    owner_user_id: UUID,
    job_id: UUID,
) -> list[JobFileCandidate]:
    job = await owned_job(session, owner_user_id, job_id)

    if job.status != "needs_file_selection":
        raise HTTPException(status_code=409, detail="Acquisition job does not need file selection")

    if job.endpoint_id is None or job.client_hash is None:
        raise HTTPException(status_code=409, detail="Acquisition job is missing its qBittorrent reference")

    endpoint = await owned_endpoint(session, owner_user_id, job.endpoint_id)
    client = await QbittorrentClient.connect(endpoint)
    files = await client.files(job.client_hash)

    return [
        JobFileCandidate(
            index=file.index,
            name=file.name,
            size_bytes=file.size_bytes,
            progress_basis_points=file.progress_basis_points,
            priority=file.priority,
            supported=_supported_book_file(file.name),
        )
        for file in files
    ]


def _supported_book_file(filename: str) -> bool:
    normalized = filename.replace("\\", "/")
    extension = PurePosixPath(normalized).suffix.lower().lstrip(".")
    return extension in BOOK_EXTENSIONS


async def select_job_file(
    session: AsyncSession,
    owner_user_id: UUID,
    job_id: UUID,
    file_index: int,
) -> AcquisitionJob:
    job = await owned_job(
        session,
        owner_user_id,
        job_id,
        for_update=True,
    )

    if job.status != "needs_file_selection":
        raise HTTPException(status_code=409, detail="Acquisition job does not need file selection")

    if job.endpoint_id is None or job.client_hash is None:
        raise HTTPException(status_code=409, detail="Acquisition job is missing its qBittorrent reference")

    endpoint = await owned_endpoint(session, owner_user_id, job.endpoint_id)
    client = await QbittorrentClient.connect(endpoint)
    files = await client.files(job.client_hash)
    selected = next((file for file in files if file.index == file_index), None)

    if selected is None:
        raise HTTPException(status_code=404, detail="qBittorrent file not found")

    if not _supported_book_file(selected.name):
        raise HTTPException(status_code=422, detail="Selected file is not a supported book")

    await client.select_file(
        job.client_hash,
        selected_index=selected.index,
        file_indices=[file.index for file in files],
    )

    job.selected_file_path = selected.name
    job.status = "downloading"
    job.error = None
    job.next_poll_at = datetime.now(UTC)
    job.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(job)
    return job


async def cancel_job(
    session: AsyncSession,
    owner_user_id: UUID,
    job_id: UUID,
) -> AcquisitionJob:
    job = await owned_job(
        session,
        owner_user_id,
        job_id,
        for_update=True,
    )

    if job.status == "cancelled":
        return job

    if job.status not in {"queued", "submitted", "downloading", "needs_file_selection"}:
        raise HTTPException(status_code=409, detail="Acquisition job cannot be cancelled")

    if job.endpoint_id is None:
        raise HTTPException(status_code=409, detail="Acquisition job is missing its qBittorrent endpoint")

    endpoint = await owned_endpoint(session, owner_user_id, job.endpoint_id)
    client = await QbittorrentClient.connect(endpoint)
    torrent_hash = job.client_hash

    if torrent_hash is None:
        try:
            torrent_hash = (await client.find_torrent(tag=f"papyrus:{job.job_id}")).hash
        except HTTPException as exc:
            if exc.status_code != 404:
                raise

    if torrent_hash is not None:
        await client.delete_torrent(torrent_hash)

    now = datetime.now(UTC)
    job.status = "cancelled"
    job.cancelled_at = now
    job.updated_at = now
    job.next_poll_at = None
    job.error = None
    await session.commit()
    await session.refresh(job)
    return job


async def delete_terminal_job(
    session: AsyncSession,
    owner_user_id: UUID,
    job_id: UUID,
) -> None:
    job = await owned_job(session, owner_user_id, job_id)

    if job.status not in {"failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="Only failed or cancelled jobs can be removed")

    book = None

    if job.book_id is not None:
        result = await session.execute(
            select(SyncBook)
            .where(
                SyncBook.book_id == job.book_id,
                SyncBook.owner_user_id == owner_user_id,
            )
            .with_for_update()
        )

        book = result.scalar_one_or_none()

        if book is not None and book.file_media_id is not None:
            raise HTTPException(status_code=409, detail="Imported books must be removed from the library")

    await session.delete(job)
    await session.flush()

    if book is not None:
        await session.delete(book)

    await session.commit()


async def retry_job_import(
    session: AsyncSession,
    owner_user_id: UUID,
    job_id: UUID,
) -> AcquisitionJob:
    job = await owned_job(
        session,
        owner_user_id,
        job_id,
        for_update=True,
    )

    if job.status != "failed":
        raise HTTPException(status_code=409, detail="Only failed jobs can retry import")

    if job.endpoint_id is None:
        raise HTTPException(status_code=409, detail="Acquisition job cannot resume without qBittorrent")

    if job.submitted_at is None:
        raise HTTPException(status_code=409, detail="Acquisition job was not submitted to qBittorrent")

    now = datetime.now(UTC)
    job.status = "downloading"
    job.retry_count += 1
    job.error = None
    job.next_poll_at = now
    job.updated_at = now
    await session.commit()
    await session.refresh(job)
    return job


async def submit_release_batch(
    session: AsyncSession,
    owner_user_id: UUID,
    endpoint_id: UUID,
    release_tokens: list[str],
) -> list[BatchSubmissionResult]:
    endpoint = await owned_endpoint(session, owner_user_id, endpoint_id)

    if endpoint.kind != "qbittorrent":
        raise HTTPException(status_code=422, detail="Managed downloads require qBittorrent")

    if not endpoint.enabled:
        raise HTTPException(status_code=409, detail="Download client is disabled")

    if endpoint.download_root is None:
        raise HTTPException(status_code=409, detail="qBittorrent download root is not configured")

    if get_settings().acquisition_import_root is None:
        raise HTTPException(status_code=409, detail="Acquisition import root is not configured")

    results: list[BatchSubmissionResult] = []

    for index, token in enumerate(release_tokens):
        try:
            release = decode_release_token(token, owner_user_id)
        except HTTPException as exc:
            results.append(
                BatchSubmissionResult(
                    index=index,
                    job=None,
                    error=str(exc.detail),
                )
            )

            continue

        if release.protocol != "torrent" or not release.download_url.startswith(("magnet:", "http://", "https://")):
            results.append(
                BatchSubmissionResult(
                    index=index,
                    job=None,
                    error="Release token is invalid or expired",
                )
            )

            continue

        book_id = uuid4()
        job_id = uuid4()

        book = SyncBook(
            book_id=book_id,
            owner_user_id=owner_user_id,
            title=release.title,
            custom_metadata={
                "acquisition": {
                    "job_id": str(job_id),
                    "provisional": True,
                }
            },
        )

        job = AcquisitionJob(
            job_id=job_id,
            owner_user_id=owner_user_id,
            endpoint_id=endpoint.endpoint_id,
            book_id=book_id,
            title=release.title,
            download_url=None,
            status="queued",
            next_poll_at=datetime.now(UTC),
            lease_owner=f"submission:{job_id}",
            lease_until=datetime.now(UTC) + SUBMISSION_LEASE_LIFETIME,
        )

        session.add(book)
        await session.flush()
        session.add(job)
        await session.flush()
        await session.commit()
        await session.refresh(job)

        try:
            job.client_reference = await submit_to_client(
                endpoint,
                release.download_url,
                "papyrus",
                _managed_download_path(endpoint.download_root, owner_user_id, job_id),
                tags=[f"papyrus:{job_id}"],
            )

            job.status = "submitted"
            job.submitted_at = datetime.now(UTC)
            job.next_poll_at = datetime.now(UTC)
        except HTTPException as exc:
            job.status = "failed"
            job.error = str(exc.detail)
            job.next_poll_at = None

        job.lease_owner = None
        job.lease_until = None
        await session.commit()
        await session.refresh(job)

        results.append(
            BatchSubmissionResult(
                index=index,
                job=job,
                error=None,
            )
        )

    return results


def _managed_download_path(download_root: str, owner_user_id: UUID, job_id: UUID) -> str:
    normalized_root = download_root.rstrip("/\\")
    separator = "\\" if "\\" in normalized_root and "/" not in normalized_root else "/"
    return separator.join((normalized_root, str(owner_user_id), str(job_id)))


async def delete_acquisition_endpoint(session: AsyncSession, owner_user_id: Any, endpoint_id: Any) -> None:
    endpoint = await owned_endpoint(session, owner_user_id, endpoint_id)

    active_job_id = await session.scalar(
        select(AcquisitionJob.job_id)
        .where(
            AcquisitionJob.owner_user_id == owner_user_id,
            AcquisitionJob.endpoint_id == endpoint_id,
            AcquisitionJob.status.not_in({"completed", "failed", "cancelled"}),
        )
        .limit(1)
    )

    if active_job_id is not None:
        raise HTTPException(status_code=409, detail="Endpoint has active acquisition jobs")

    result = await session.execute(
        select(AcquisitionRule).where(AcquisitionRule.owner_user_id == owner_user_id).with_for_update()
    )

    for rule in result.scalars():
        endpoint_ids = rule.endpoint_ids or []
        remaining_endpoint_ids = [value for value in endpoint_ids if value != str(endpoint_id)]
        indexer_deleted = remaining_endpoint_ids != endpoint_ids
        download_client_deleted = rule.download_client_id == endpoint_id

        if indexer_deleted:
            rule.endpoint_ids = remaining_endpoint_ids

        if download_client_deleted:
            rule.download_client_id = None

        if download_client_deleted or (indexer_deleted and not remaining_endpoint_ids):
            rule.enabled = False

    await session.flush()
    await session.delete(endpoint)
    await session.commit()


async def build_test_endpoint(
    session: AsyncSession,
    owner_user_id: Any,
    request: AcquisitionEndpointTest,
) -> AcquisitionEndpoint:
    stored_endpoint = None
    stored_credentials: dict[str, str] = {}

    if request.endpoint_id is not None:
        stored_endpoint = await owned_endpoint(session, owner_user_id, request.endpoint_id)
        stored_credentials = _credentials(stored_endpoint)

    credentials = dict(stored_credentials)

    for field in ("api_key", "username", "password"):
        value = getattr(request, field)

        if value is not None:
            credentials[field] = value.get_secret_value()

    if request.kind is not None:
        kind = request.kind.value
    elif stored_endpoint is not None:
        kind = stored_endpoint.kind
    else:
        raise HTTPException(status_code=422, detail="Endpoint kind is required")

    if request.base_url is not None:
        base_url = str(request.base_url)
    elif stored_endpoint is not None:
        base_url = stored_endpoint.base_url
    else:
        raise HTTPException(status_code=422, detail="Endpoint URL is required")

    encrypted_credentials = {"encrypted": encrypt_secret_payload(credentials)} if credentials else None

    return AcquisitionEndpoint(
        owner_user_id=owner_user_id,
        name=stored_endpoint.name if stored_endpoint is not None else "Connection test",
        kind=kind,
        base_url=base_url,
        credentials=encrypted_credentials,
        settings=stored_endpoint.settings if stored_endpoint is not None else None,
    )


async def run_rule(session: AsyncSession, rule: AcquisitionRule) -> list[AcquisitionJob]:
    """Run one rule once; callers may schedule this from their worker/cron service."""
    client = await owned_endpoint(session, rule.owner_user_id, rule.download_client_id)

    if client.kind in {"readarr", "sonarr", "radarr", "lidarr", "whisparr"}:
        filters = rule.filters or {}
        command = filters.get("arr_command")
        ids = filters.get("arr_ids", [])

        if (
            not isinstance(command, str)
            or not isinstance(ids, list)
            or not all(isinstance(value, int) for value in ids)
        ):
            raise HTTPException(
                status_code=422,
                detail="Arr rules require filters.arr_command and filters.arr_ids",
            )

        job = AcquisitionJob(
            owner_user_id=rule.owner_user_id,
            endpoint_id=client.endpoint_id,
            rule_id=rule.rule_id,
            title=command,
            download_url=f"arr-command:{command}",
        )

        session.add(job)

        try:
            job.client_reference = await dispatch_arr_command(client, command, ids)
            job.status = "submitted"
        except HTTPException as exc:
            job.status = "failed"
            job.error = str(exc.detail)

        rule.last_run_at = datetime.now(UTC)
        await session.commit()
        return [job]

    endpoint_ids = rule.endpoint_ids or []

    result = await session.execute(
        select(AcquisitionEndpoint).where(
            AcquisitionEndpoint.owner_user_id == rule.owner_user_id,
            AcquisitionEndpoint.endpoint_id.in_(endpoint_ids),
            AcquisitionEndpoint.enabled.is_(True),
        )
    )

    releases = [release for endpoint in result.scalars() for release in await search_endpoint(endpoint, rule.query)]

    if not releases:
        rule.last_run_at = datetime.now(UTC)
        await session.commit()
        return []

    selected = sorted(releases, key=lambda release: (release.seeders or 0, release.size_bytes or 0), reverse=True)[0]

    job = AcquisitionJob(
        owner_user_id=rule.owner_user_id,
        endpoint_id=client.endpoint_id,
        rule_id=rule.rule_id,
        title=selected.title,
        download_url=selected.download_url,
    )

    session.add(job)

    try:
        job.client_reference = await submit_to_client(
            client,
            selected.download_url,
            None,
            None,
        )

        job.status = "submitted"
    except HTTPException as exc:
        job.status = "failed"
        job.error = str(exc.detail)

    rule.last_run_at = datetime.now(UTC)
    await session.commit()
    return [job]


async def run_enabled_rules(session: AsyncSession) -> None:
    """Run enabled rules, isolating a failed remote integration from the others."""
    result = await session.execute(select(AcquisitionRule).where(AcquisitionRule.enabled.is_(True)))

    for rule in result.scalars():
        try:
            await run_rule(session, rule)
        except HTTPException:
            await session.rollback()
