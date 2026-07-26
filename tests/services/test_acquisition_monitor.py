"""Tests for user-submitted acquisition job monitoring."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from papyrus.core.exceptions import ValidationError
from papyrus.main import settings as app_settings
from papyrus.models import AcquisitionEndpoint, AcquisitionJob, AcquisitionRule, MediaAsset, SyncBook
from papyrus.services import acquisition as acquisition_service
from papyrus.services import acquisition_monitor


async def test_process_job_persists_active_qbittorrent_progress(
    auth_user: dict[str, str],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(app_settings, "acquisition_monitor_active_interval_seconds", 2, raising=False)
    owner_user_id = UUID(auth_user["user_id"])
    endpoint = AcquisitionEndpoint(
        owner_user_id=owner_user_id,
        name="qBittorrent",
        kind="qbittorrent",
        base_url="http://qbittorrent.local:8080",
        download_root="/downloads",
    )
    db_session.add(endpoint)
    await db_session.flush()
    job = AcquisitionJob(
        owner_user_id=owner_user_id,
        endpoint_id=endpoint.endpoint_id,
        title="Downloading Book",
        status="submitted",
        client_hash="saved-hash",
    )
    db_session.add(job)
    await db_session.commit()
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)

    class FakeQbittorrentClient:
        async def find_torrent(
            self,
            *,
            tag: str,
            torrent_hash: str | None = None,
        ) -> acquisition_service.QbittorrentTorrent:
            assert tag == f"papyrus:{job.job_id}"
            assert torrent_hash == "saved-hash"

            return acquisition_service.QbittorrentTorrent(
                hash="abc123",
                state="downloading",
                progress_basis_points=5000,
                downloaded_bytes=512,
                total_bytes=1024,
                download_speed_bytes_per_second=128,
                eta_seconds=4,
            )

    await acquisition_monitor.process_job(
        db_session,
        job.job_id,
        FakeQbittorrentClient(),
        import_root=tmp_path,
        now=now,
    )

    await db_session.refresh(job)
    assert job.status == "downloading"
    assert job.client_hash == "abc123"
    assert job.client_state == "downloading"
    assert job.progress_basis_points == 5000
    assert job.downloaded_bytes == 512
    assert job.total_bytes == 1024
    assert job.download_speed_bytes_per_second == 128
    assert job.eta_seconds == 4
    assert job.submitted_at == now
    assert job.started_at == now
    assert job.next_poll_at == now + timedelta(seconds=2)


async def test_process_job_imports_one_completed_book_file_and_keeps_source(
    auth_user: dict[str, str],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    media_root = tmp_path / "media"
    import_root = tmp_path / "downloads"
    monkeypatch.setattr(app_settings, "media_storage_root", str(media_root), raising=False)
    monkeypatch.setattr(app_settings, "file_storage_quota_bytes", 1_073_741_824, raising=False)
    owner_user_id = UUID(auth_user["user_id"])
    endpoint = AcquisitionEndpoint(
        owner_user_id=owner_user_id,
        name="qBittorrent",
        kind="qbittorrent",
        base_url="http://qbittorrent.local:8080",
        download_root="/downloads",
    )
    book = SyncBook(owner_user_id=owner_user_id, title="Completed Book")
    db_session.add_all([endpoint, book])
    await db_session.flush()
    job = AcquisitionJob(
        owner_user_id=owner_user_id,
        endpoint_id=endpoint.endpoint_id,
        book_id=book.book_id,
        title=book.title,
        status="downloading",
    )
    db_session.add(job)
    await db_session.commit()
    source_path = import_root / str(owner_user_id) / str(job.job_id) / "book.epub"
    source_path.parent.mkdir(parents=True)
    source_path.write_bytes(b"completed epub")
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)

    class FakeQbittorrentClient:
        async def find_torrent(
            self,
            *,
            tag: str,
            torrent_hash: str | None = None,
        ) -> acquisition_service.QbittorrentTorrent:
            return acquisition_service.QbittorrentTorrent(
                hash="abc123",
                state="uploading",
                progress_basis_points=10_000,
                downloaded_bytes=14,
                total_bytes=14,
                download_speed_bytes_per_second=0,
                eta_seconds=0,
            )

        async def files(self, torrent_hash: str) -> list[acquisition_service.QbittorrentFile]:
            assert torrent_hash == "abc123"
            return [
                acquisition_service.QbittorrentFile(
                    index=0,
                    name="book.epub",
                    size_bytes=14,
                    progress_basis_points=10_000,
                    priority=1,
                )
            ]

    await acquisition_monitor.process_job(
        db_session,
        job.job_id,
        FakeQbittorrentClient(),
        import_root=import_root,
        now=now,
    )

    await db_session.refresh(job)
    await db_session.refresh(book)
    asset = await db_session.get(MediaAsset, book.file_media_id)
    assert job.status == "completed"
    assert job.completed_at == now
    assert job.next_poll_at is None
    assert asset is not None
    assert (media_root / asset.storage_path).read_bytes() == b"completed epub"
    assert source_path.read_bytes() == b"completed epub"


async def test_process_job_pauses_completed_torrent_with_multiple_book_files(
    auth_user: dict[str, str],
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    owner_user_id = UUID(auth_user["user_id"])
    endpoint = AcquisitionEndpoint(
        owner_user_id=owner_user_id,
        name="qBittorrent",
        kind="qbittorrent",
        base_url="http://qbittorrent.local:8080",
        download_root="/downloads",
    )
    book = SyncBook(owner_user_id=owner_user_id, title="Multiple Books")
    db_session.add_all([endpoint, book])
    await db_session.flush()
    job = AcquisitionJob(
        owner_user_id=owner_user_id,
        endpoint_id=endpoint.endpoint_id,
        book_id=book.book_id,
        title=book.title,
        status="downloading",
    )
    db_session.add(job)
    await db_session.commit()
    paused_hashes: list[str] = []

    class FakeQbittorrentClient:
        async def find_torrent(
            self,
            *,
            tag: str,
            torrent_hash: str | None = None,
        ) -> acquisition_service.QbittorrentTorrent:
            return acquisition_service.QbittorrentTorrent(
                hash="abc123",
                state="uploading",
                progress_basis_points=10_000,
                downloaded_bytes=3072,
                total_bytes=3072,
                download_speed_bytes_per_second=0,
                eta_seconds=0,
            )

        async def files(self, torrent_hash: str) -> list[acquisition_service.QbittorrentFile]:
            return [
                acquisition_service.QbittorrentFile(0, "first.epub", 1024, 10_000, 1),
                acquisition_service.QbittorrentFile(1, "second.pdf", 2048, 10_000, 1),
            ]

        async def pause(self, torrent_hash: str) -> None:
            paused_hashes.append(torrent_hash)

    await acquisition_monitor.process_job(
        db_session,
        job.job_id,
        FakeQbittorrentClient(),
        import_root=tmp_path,
    )

    await db_session.refresh(job)
    assert job.status == "needs_file_selection"
    assert job.next_poll_at is None
    assert paused_hashes == ["abc123"]
    assert (
        await db_session.scalar(select(func.count()).select_from(MediaAsset).where(MediaAsset.book_id == book.book_id))
        == 0
    )


def test_resolve_import_path_rejects_symbolic_links(
    tmp_path: Path,
) -> None:
    owner_user_id = UUID("00000000-0000-0000-0000-000000000001")
    job_id = UUID("00000000-0000-0000-0000-000000000002")
    job_root = tmp_path / str(owner_user_id) / str(job_id)
    job_root.mkdir(parents=True)
    (job_root / "real.epub").write_bytes(b"book")
    (job_root / "linked.epub").symlink_to(job_root / "real.epub")

    with pytest.raises(ValidationError, match="symbolic link"):
        acquisition_monitor._resolve_import_path(
            tmp_path,
            owner_user_id,
            job_id,
            "linked.epub",
        )


@pytest.mark.parametrize("link_level", ["owner", "job"])
def test_resolve_import_path_rejects_symbolic_parent_directories(
    tmp_path: Path,
    link_level: str,
) -> None:
    owner_user_id = UUID("00000000-0000-0000-0000-000000000001")
    job_id = UUID("00000000-0000-0000-0000-000000000002")
    import_root = tmp_path / "imports"
    outside = tmp_path / "outside"
    import_root.mkdir()
    outside.mkdir()

    if link_level == "owner":
        outside_job = outside / str(job_id)
        outside_job.mkdir()
        (outside_job / "book.epub").write_bytes(b"book")
        (import_root / str(owner_user_id)).symlink_to(outside, target_is_directory=True)
    else:
        owner_root = import_root / str(owner_user_id)
        owner_root.mkdir()
        (outside / "book.epub").write_bytes(b"book")
        (owner_root / str(job_id)).symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValidationError, match="symbolic link"):
        acquisition_monitor._resolve_import_path(
            import_root,
            owner_user_id,
            job_id,
            "book.epub",
        )


async def test_claim_due_jobs_assigns_distinct_database_leases(
    auth_user: dict[str, str],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_settings, "acquisition_monitor_lease_seconds", 30, raising=False)
    owner_user_id = UUID(auth_user["user_id"])
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)
    endpoint = AcquisitionEndpoint(
        owner_user_id=owner_user_id,
        name="qBittorrent",
        kind="qbittorrent",
        base_url="http://qbittorrent.local:8080",
        download_root="/downloads",
    )
    db_session.add(endpoint)
    await db_session.flush()
    books = [SyncBook(owner_user_id=owner_user_id, title=f"Book {index}") for index in range(4)]
    db_session.add_all(books)
    await db_session.flush()
    due_jobs = [
        AcquisitionJob(
            owner_user_id=owner_user_id,
            endpoint_id=endpoint.endpoint_id,
            book_id=books[index - 1].book_id,
            title=f"Due {index}",
            status="downloading",
            next_poll_at=now - timedelta(seconds=index),
        )
        for index in (1, 2)
    ]
    future_job = AcquisitionJob(
        owner_user_id=owner_user_id,
        endpoint_id=endpoint.endpoint_id,
        book_id=books[2].book_id,
        title="Future",
        status="downloading",
        next_poll_at=now + timedelta(minutes=1),
    )
    leased_job = AcquisitionJob(
        owner_user_id=owner_user_id,
        endpoint_id=endpoint.endpoint_id,
        book_id=books[3].book_id,
        title="Leased",
        status="downloading",
        next_poll_at=now - timedelta(seconds=1),
        lease_owner="other-worker",
        lease_until=now + timedelta(minutes=1),
    )
    db_session.add_all([*due_jobs, future_job, leased_job])
    await db_session.commit()

    first = await acquisition_monitor.claim_due_jobs(
        db_session,
        "worker-a",
        now=now,
        limit=1,
    )
    second = await acquisition_monitor.claim_due_jobs(
        db_session,
        "worker-b",
        now=now,
        limit=1,
    )

    assert len(first) == len(second) == 1
    assert first[0] != second[0]
    assert set(first + second) == {job.job_id for job in due_jobs}

    for job in due_jobs:
        await db_session.refresh(job)
        assert job.lease_owner in {"worker-a", "worker-b"}
        assert job.lease_until == now + timedelta(seconds=30)

    await db_session.refresh(future_job)
    await db_session.refresh(leased_job)
    assert future_job.lease_owner is None
    assert leased_job.lease_owner == "other-worker"


async def test_claim_due_jobs_ignores_automatic_rule_jobs(
    auth_user: dict[str, str],
    db_session: AsyncSession,
) -> None:
    owner_user_id = UUID(auth_user["user_id"])
    download_client = AcquisitionEndpoint(
        owner_user_id=owner_user_id,
        name="qBittorrent",
        kind="qbittorrent",
        base_url="http://qbittorrent.local:8080",
        download_root="/downloads",
    )
    arr_endpoint = AcquisitionEndpoint(
        owner_user_id=owner_user_id,
        name="Readarr",
        kind="readarr",
        base_url="http://readarr.local:8787",
    )
    rule = AcquisitionRule(
        owner_user_id=owner_user_id,
        name="Automatic",
        query="automatic book",
    )
    db_session.add_all([download_client, arr_endpoint, rule])
    await db_session.flush()
    manual_book = SyncBook(owner_user_id=owner_user_id, title="Manual")
    rule_book = SyncBook(owner_user_id=owner_user_id, title="Automatic")
    db_session.add_all([manual_book, rule_book])
    await db_session.flush()
    manual_job = AcquisitionJob(
        owner_user_id=owner_user_id,
        endpoint_id=download_client.endpoint_id,
        book_id=manual_book.book_id,
        title="Manual",
        status="submitted",
    )
    rule_job = AcquisitionJob(
        owner_user_id=owner_user_id,
        endpoint_id=download_client.endpoint_id,
        rule_id=rule.rule_id,
        book_id=rule_book.book_id,
        title="Automatic",
        status="submitted",
    )
    arr_job = AcquisitionJob(
        owner_user_id=owner_user_id,
        endpoint_id=arr_endpoint.endpoint_id,
        title="Readarr search",
        status="submitted",
    )
    legacy_job = AcquisitionJob(
        owner_user_id=owner_user_id,
        endpoint_id=download_client.endpoint_id,
        title="Legacy qBittorrent submission",
        download_url="magnet:?xt=urn:btih:legacy",
        status="submitted",
    )
    db_session.add_all([manual_job, rule_job, arr_job, legacy_job])
    await db_session.commit()

    claimed = await acquisition_monitor.claim_due_jobs(
        db_session,
        "worker",
        now=datetime(2026, 7, 25, 12, tzinfo=UTC),
    )

    assert claimed == [manual_job.job_id]


async def test_transient_endpoint_failure_reschedules_managed_job(
    auth_user: dict[str, str],
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    owner_user_id = UUID(auth_user["user_id"])
    endpoint = AcquisitionEndpoint(
        owner_user_id=owner_user_id,
        name="qBittorrent",
        kind="qbittorrent",
        base_url="http://qbittorrent.local:8080",
        download_root="/downloads",
    )
    book = SyncBook(owner_user_id=owner_user_id, title="Transient")
    db_session.add_all([endpoint, book])
    await db_session.flush()
    job = AcquisitionJob(
        owner_user_id=owner_user_id,
        endpoint_id=endpoint.endpoint_id,
        book_id=book.book_id,
        title="Transient",
        status="submitted",
        lease_owner="worker",
    )
    db_session.add(job)
    await db_session.commit()
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)

    async def connect(endpoint: AcquisitionEndpoint) -> acquisition_monitor.QbittorrentMonitorClient:
        raise HTTPException(status_code=502, detail="qBittorrent is temporarily unavailable")

    await acquisition_monitor.process_claimed_jobs(
        db_session,
        [job.job_id],
        import_root=tmp_path,
        client_factory=connect,
        worker_id="worker",
        now=now,
    )

    await db_session.refresh(job)
    assert job.status == "submitted"
    assert job.retry_count == 1
    assert job.error == "qBittorrent is temporarily unavailable"
    assert job.next_poll_at is not None and job.next_poll_at > now
    assert job.lease_owner is None


async def test_missing_qbittorrent_torrent_becomes_terminal_after_bounded_retries(
    auth_user: dict[str, str],
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    owner_user_id = UUID(auth_user["user_id"])
    endpoint = AcquisitionEndpoint(
        owner_user_id=owner_user_id,
        name="qBittorrent",
        kind="qbittorrent",
        base_url="http://qbittorrent.local:8080",
        download_root="/downloads",
    )
    book = SyncBook(owner_user_id=owner_user_id, title="Missing")
    db_session.add_all([endpoint, book])
    await db_session.flush()
    job = AcquisitionJob(
        owner_user_id=owner_user_id,
        endpoint_id=endpoint.endpoint_id,
        book_id=book.book_id,
        title="Missing",
        status="queued",
        retry_count=acquisition_monitor.MAX_MISSING_TORRENT_RETRIES - 1,
        lease_owner="worker",
    )
    db_session.add(job)
    await db_session.commit()

    class FakeQbittorrentClient:
        async def find_torrent(
            self,
            *,
            tag: str,
            torrent_hash: str | None = None,
        ) -> acquisition_service.QbittorrentTorrent:
            raise HTTPException(status_code=404, detail="qBittorrent torrent not found")

    async def connect(endpoint: AcquisitionEndpoint) -> FakeQbittorrentClient:
        return FakeQbittorrentClient()

    await acquisition_monitor.process_claimed_jobs(
        db_session,
        [job.job_id],
        import_root=tmp_path,
        client_factory=connect,
        worker_id="worker",
    )

    await db_session.refresh(job)
    assert job.status == "failed"
    assert job.retry_count == acquisition_monitor.MAX_MISSING_TORRENT_RETRIES
    assert job.error == "qBittorrent torrent not found"
    assert job.next_poll_at is None
    assert job.lease_owner is None


async def test_failed_recovered_torrent_remains_eligible_for_import_retry(
    auth_user: dict[str, str],
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    owner_user_id = UUID(auth_user["user_id"])
    endpoint = AcquisitionEndpoint(
        owner_user_id=owner_user_id,
        name="qBittorrent",
        kind="qbittorrent",
        base_url="http://qbittorrent.local:8080",
        download_root="/downloads",
    )
    book = SyncBook(owner_user_id=owner_user_id, title="Recovered")
    db_session.add_all([endpoint, book])
    await db_session.flush()
    job = AcquisitionJob(
        owner_user_id=owner_user_id,
        endpoint_id=endpoint.endpoint_id,
        book_id=book.book_id,
        title="Recovered",
        status="queued",
        lease_owner="worker",
    )
    db_session.add(job)
    await db_session.commit()
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)

    class FakeQbittorrentClient:
        async def find_torrent(
            self,
            *,
            tag: str,
            torrent_hash: str | None = None,
        ) -> acquisition_service.QbittorrentTorrent:
            return acquisition_service.QbittorrentTorrent(
                hash="recovered-hash",
                state="uploading",
                progress_basis_points=10_000,
                downloaded_bytes=1024,
                total_bytes=1024,
                download_speed_bytes_per_second=0,
                eta_seconds=0,
            )

        async def files(self, torrent_hash: str) -> list[acquisition_service.QbittorrentFile]:
            return [
                acquisition_service.QbittorrentFile(
                    index=0,
                    name="not-a-book.exe",
                    size_bytes=1024,
                    progress_basis_points=10_000,
                    priority=1,
                )
            ]

    async def connect(endpoint: AcquisitionEndpoint) -> FakeQbittorrentClient:
        return FakeQbittorrentClient()

    await acquisition_monitor.process_claimed_jobs(
        db_session,
        [job.job_id],
        import_root=tmp_path,
        client_factory=connect,
        worker_id="worker",
        now=now,
    )

    await db_session.refresh(job)
    assert job.status == "failed"
    assert job.submitted_at == now
    assert job.client_hash == "recovered-hash"
    assert job.error == "Downloaded release does not contain a supported book file"


async def test_failure_finalization_does_not_overwrite_cancelled_job(
    auth_user: dict[str, str],
    db_session: AsyncSession,
) -> None:
    job = AcquisitionJob(
        owner_user_id=UUID(auth_user["user_id"]),
        title="Cancelled",
        status="cancelled",
        lease_owner="worker",
    )
    db_session.add(job)
    await db_session.commit()

    await acquisition_monitor._mark_failed_job(
        db_session,
        job.job_id,
        "Late monitor failure",
        datetime(2026, 7, 25, 12, tzinfo=UTC),
        worker_id="worker",
    )

    await db_session.refresh(job)
    assert job.status == "cancelled"
    assert job.error is None


async def test_process_claimed_jobs_connects_once_per_endpoint(
    auth_user: dict[str, str],
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    owner_user_id = UUID(auth_user["user_id"])
    endpoint = AcquisitionEndpoint(
        owner_user_id=owner_user_id,
        name="qBittorrent",
        kind="qbittorrent",
        base_url="http://qbittorrent.local:8080",
        download_root="/downloads",
    )
    db_session.add(endpoint)
    await db_session.flush()
    jobs = [
        AcquisitionJob(
            owner_user_id=owner_user_id,
            endpoint_id=endpoint.endpoint_id,
            title=f"Book {index}",
            status="submitted",
            lease_owner="worker",
        )
        for index in (1, 2)
    ]
    db_session.add_all(jobs)
    await db_session.commit()
    connected_endpoints: list[UUID] = []
    observed_tags: list[str] = []

    class FakeQbittorrentClient:
        async def find_torrent(
            self,
            *,
            tag: str,
            torrent_hash: str | None = None,
        ) -> acquisition_service.QbittorrentTorrent:
            observed_tags.append(tag)

            return acquisition_service.QbittorrentTorrent(
                hash=tag,
                state="downloading",
                progress_basis_points=5000,
                downloaded_bytes=512,
                total_bytes=1024,
                download_speed_bytes_per_second=128,
                eta_seconds=4,
            )

    async def connect(
        connected_endpoint: AcquisitionEndpoint,
    ) -> FakeQbittorrentClient:
        connected_endpoints.append(connected_endpoint.endpoint_id)

        return FakeQbittorrentClient()

    await acquisition_monitor.process_claimed_jobs(
        db_session,
        [job.job_id for job in jobs],
        import_root=tmp_path,
        client_factory=connect,
    )

    assert connected_endpoints == [endpoint.endpoint_id]
    assert set(observed_tags) == {f"papyrus:{job.job_id}" for job in jobs}


async def test_process_claimed_jobs_isolates_job_failures(
    auth_user: dict[str, str],
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    owner_user_id = UUID(auth_user["user_id"])
    endpoint = AcquisitionEndpoint(
        owner_user_id=owner_user_id,
        name="qBittorrent",
        kind="qbittorrent",
        base_url="http://qbittorrent.local:8080",
        download_root="/downloads",
    )
    db_session.add(endpoint)
    await db_session.flush()
    failed_job = AcquisitionJob(
        owner_user_id=owner_user_id,
        endpoint_id=endpoint.endpoint_id,
        title="Broken",
        status="submitted",
        lease_owner="worker",
    )
    healthy_job = AcquisitionJob(
        owner_user_id=owner_user_id,
        endpoint_id=endpoint.endpoint_id,
        title="Healthy",
        status="submitted",
        lease_owner="worker",
    )
    db_session.add_all([failed_job, healthy_job])
    await db_session.commit()

    class FakeQbittorrentClient:
        async def find_torrent(
            self,
            *,
            tag: str,
            torrent_hash: str | None = None,
        ) -> acquisition_service.QbittorrentTorrent:
            if tag == f"papyrus:{failed_job.job_id}":
                raise ValidationError("Downloaded release is invalid")

            return acquisition_service.QbittorrentTorrent(
                hash="healthy",
                state="downloading",
                progress_basis_points=5000,
                downloaded_bytes=512,
                total_bytes=1024,
                download_speed_bytes_per_second=128,
                eta_seconds=4,
            )

    async def connect(
        connected_endpoint: AcquisitionEndpoint,
    ) -> FakeQbittorrentClient:
        return FakeQbittorrentClient()

    await acquisition_monitor.process_claimed_jobs(
        db_session,
        [failed_job.job_id, healthy_job.job_id],
        import_root=tmp_path,
        client_factory=connect,
    )

    await db_session.refresh(failed_job)
    await db_session.refresh(healthy_job)
    assert failed_job.status == "failed"
    assert failed_job.error == "Downloaded release is invalid"
    assert failed_job.lease_owner is None
    assert healthy_job.status == "downloading"
    assert healthy_job.error is None


@pytest.mark.parametrize(
    ("claimed_job_ids", "expected_delay"),
    [
        ([], 10),
        ([uuid4()], 2),
    ],
)
async def test_run_monitor_uses_idle_or_active_poll_interval(
    claimed_job_ids: list[UUID],
    expected_delay: int,
    test_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(app_settings, "acquisition_monitor_active_interval_seconds", 2, raising=False)
    monkeypatch.setattr(app_settings, "acquisition_monitor_idle_interval_seconds", 10, raising=False)
    processed_job_ids: list[UUID] = []
    delays: list[float] = []

    async def claim(
        session: AsyncSession,
        worker_id: str,
        *,
        now: datetime | None = None,
        limit: int = 50,
    ) -> list[UUID]:
        return claimed_job_ids

    async def process(
        session: AsyncSession,
        job_ids: list[UUID],
        *,
        import_root: Path,
        client_factory: acquisition_monitor.QbittorrentClientFactory | None = None,
        worker_id: str | None = None,
        now: datetime | None = None,
    ) -> None:
        assert worker_id == "worker"
        processed_job_ids.extend(job_ids)

    class StopMonitorError(Exception):
        pass

    async def sleep(delay: float) -> None:
        delays.append(delay)
        raise StopMonitorError

    monkeypatch.setattr(acquisition_monitor, "claim_due_jobs", claim)
    monkeypatch.setattr(acquisition_monitor, "process_claimed_jobs", process)

    with pytest.raises(StopMonitorError):
        await acquisition_monitor.run_monitor(
            test_session_maker,
            import_root=tmp_path,
            worker_id="worker",
            sleep=sleep,
        )

    assert processed_job_ids == claimed_job_ids
    assert delays == [expected_delay]


async def test_run_monitor_continues_after_cycle_failure(
    test_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(app_settings, "acquisition_monitor_idle_interval_seconds", 10, raising=False)
    claim_attempts = 0
    delays: list[float] = []

    async def claim(
        session: AsyncSession,
        worker_id: str,
        *,
        now: datetime | None = None,
        limit: int = 50,
    ) -> list[UUID]:
        nonlocal claim_attempts
        claim_attempts += 1

        if claim_attempts == 1:
            raise RuntimeError("database temporarily unavailable")

        return []

    class StopMonitorError(Exception):
        pass

    async def sleep(delay: float) -> None:
        delays.append(delay)

        if len(delays) == 2:
            raise StopMonitorError

        await asyncio.sleep(0)

    monkeypatch.setattr(acquisition_monitor, "claim_due_jobs", claim)

    with pytest.raises(StopMonitorError):
        await acquisition_monitor.run_monitor(
            test_session_maker,
            import_root=tmp_path,
            worker_id="worker",
            sleep=sleep,
        )

    assert claim_attempts == 2
    assert delays == [10, 10]
