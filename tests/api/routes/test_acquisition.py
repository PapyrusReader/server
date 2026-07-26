"""Tests for private BitTorrent acquisition configuration."""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from papyrus.api.routes import acquisition as acquisition_routes
from papyrus.core.security import decrypt_secret_payload
from papyrus.main import settings as app_settings
from papyrus.models.acquisition import AcquisitionEndpoint, AcquisitionJob, AcquisitionRule
from papyrus.models.sync import SyncBook
from papyrus.models.user import User
from papyrus.services import acquisition as acquisition_service


@pytest.fixture(autouse=True)
def enable_acquisition(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_settings, "acquisition_enabled", True, raising=False)


async def test_disabled_capabilities_hide_acquisition_scope(client: AsyncClient) -> None:
    app_settings.acquisition_enabled = False

    response = await client.get("/v1/acquisition/capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "enabled": False,
        "managed_downloads_ready": False,
        "endpoint_kinds": [],
        "indexer_kinds": [],
        "download_client_kinds": [],
        "arr_kinds": [],
        "arr_commands": {},
    }


async def test_disabled_acquisition_routes_are_not_found(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    app_settings.acquisition_enabled = False

    response = await client.get("/v1/acquisition/endpoints", headers=auth_headers)

    assert response.status_code == 404


async def test_capabilities_advertise_torrent_only_scope(
    client: AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_settings, "acquisition_import_root", "/imports", raising=False)
    response = await client.get("/v1/acquisition/capabilities", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["indexer_kinds"] == ["prowlarr", "torznab"]
    assert body["managed_downloads_ready"] is True
    assert body["download_client_kinds"] == ["qbittorrent", "transmission", "deluge"]
    assert "newznab" not in body["endpoint_kinds"]
    assert body["arr_commands"]["readarr"] == ["AuthorSearch", "BookSearch"]


async def test_capabilities_report_managed_downloads_not_ready_without_import_root(
    client: AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(app_settings, "acquisition_import_root", None, raising=False)

    response = await client.get(
        "/v1/acquisition/capabilities",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["enabled"] is True
    assert response.json()["managed_downloads_ready"] is False


async def test_create_and_list_endpoint_hides_and_encrypts_credentials(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    """Credentials can be configured but must never be returned to a client."""
    response = await client.post(
        "/v1/acquisition/endpoints",
        headers=auth_headers,
        json={
            "name": "Home qBittorrent",
            "kind": "qbittorrent",
            "base_url": "http://127.0.0.1:8080",
            "username": "admin",
            "password": "secret",
        },
    )

    assert response.status_code == 201
    assert "password" not in response.text
    assert "username" not in response.text
    endpoint = response.json()

    response = await client.get("/v1/acquisition/endpoints", headers=auth_headers)
    assert response.status_code == 200
    assert response.json() == [endpoint]

    stored = (await db_session.execute(select(AcquisitionEndpoint))).scalar_one()
    assert stored.credentials is not None
    assert "password" not in stored.credentials
    assert decrypt_secret_payload(stored.credentials["encrypted"]) == {
        "username": "admin",
        "password": "secret",
    }


async def test_create_rule_requires_owned_download_client(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    """Rules cannot target a client belonging to another user or a missing client."""
    response = await client.post(
        "/v1/acquisition/rules",
        headers=auth_headers,
        json={
            "name": "Monthly reading",
            "query": "example book",
            "download_client_id": "00000000-0000-0000-0000-000000000001",
        },
    )

    assert response.status_code == 404


async def test_readarr_endpoint_is_a_supported_integration(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    """Readarr uses its own acquisition workflow and is accepted as an endpoint."""
    response = await client.post(
        "/v1/acquisition/endpoints",
        headers=auth_headers,
        json={
            "name": "Reading automation",
            "kind": "readarr",
            "base_url": "http://readarr.local:8787",
            "api_key": "private-key",
        },
    )

    assert response.status_code == 201
    assert response.json()["kind"] == "readarr"


async def test_newznab_endpoint_is_rejected(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    response = await client.post(
        "/v1/acquisition/endpoints",
        headers=auth_headers,
        json={
            "name": "Usenet",
            "kind": "newznab",
            "base_url": "http://newznab.local",
            "api_key": "private-key",
        },
    )

    assert response.status_code == 422


async def test_endpoint_url_rejects_embedded_credentials(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    response = await client.post(
        "/v1/acquisition/endpoints",
        headers=auth_headers,
        json={
            "name": "Bad URL",
            "kind": "qbittorrent",
            "base_url": "http://user:secret@127.0.0.1:8080",
        },
    )

    assert response.status_code == 422


async def test_search_returns_owner_bound_release_tokens_without_download_urls(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint_response = await client.post(
        "/v1/acquisition/endpoints",
        headers=auth_headers,
        json={
            "name": "Prowlarr",
            "kind": "prowlarr",
            "base_url": "http://prowlarr.local:9696",
            "api_key": "private-key",
        },
    )

    async def search_endpoint(
        endpoint: AcquisitionEndpoint,
        query: str,
    ) -> list[acquisition_service.ReleaseCandidate]:
        assert endpoint.endpoint_id == UUID(endpoint_response.json()["endpoint_id"])
        assert query == "test book"

        return [
            acquisition_service.ReleaseCandidate(
                title="A Test Book",
                download_url="https://prowlarr.local/download?apikey=private-key",
                protocol="torrent",
                indexer="Test Indexer",
                size_bytes=1024,
                seeders=12,
                publish_date=None,
                format_hints=["epub"],
            )
        ]

    monkeypatch.setattr(acquisition_routes, "search_endpoint", search_endpoint)

    response = await client.post(
        "/v1/acquisition/search",
        headers=auth_headers,
        json={"query": "test book"},
    )

    assert response.status_code == 200
    release = response.json()[0]
    assert "download_url" not in release
    assert release["format_hints"] == ["epub"]

    payload = acquisition_service.decode_release_token(
        release["release_token"],
        UUID(auth_user["user_id"]),
    )
    assert payload.download_url == "https://prowlarr.local/download?apikey=private-key"


async def test_batch_submission_creates_a_linked_placeholder_without_persisting_the_release_url(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
    test_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(app_settings, "acquisition_import_root", str(tmp_path), raising=False)
    endpoint_response = await client.post(
        "/v1/acquisition/endpoints",
        headers=auth_headers,
        json={
            "name": "Home qBittorrent",
            "kind": "qbittorrent",
            "base_url": "http://qbittorrent.local:8080",
            "username": "user",
            "password": "secret",
            "download_root": "/downloads",
        },
    )
    endpoint_id = UUID(endpoint_response.json()["endpoint_id"])
    owner_user_id = UUID(auth_user["user_id"])
    indexer = AcquisitionEndpoint(
        owner_user_id=owner_user_id,
        name="Prowlarr",
        kind="prowlarr",
        base_url="http://prowlarr.local:9696",
    )
    db_session.add(indexer)
    await db_session.commit()

    release_url = "https://prowlarr.local/download?apikey=private-key"
    token = acquisition_service.create_release_token(
        acquisition_service.ReleaseCandidate(
            title="A Test Book",
            download_url=release_url,
            protocol="torrent",
            indexer="Test Indexer",
            size_bytes=1024,
            seeders=12,
            publish_date=None,
            format_hints=["epub"],
        ),
        indexer,
    )
    submissions: list[tuple[str, str | None, str | None, list[str] | None]] = []

    async def submit_to_client(
        endpoint: AcquisitionEndpoint,
        download_url: str,
        category: str | None,
        save_path: str | None,
        *,
        tags: list[str] | None = None,
    ) -> str | None:
        assert endpoint.endpoint_id == endpoint_id

        async with test_session_maker() as observer:
            persisted_job = (await observer.execute(select(AcquisitionJob))).scalar_one()
            persisted_book = (await observer.execute(select(SyncBook))).scalar_one()

        assert persisted_job.status == "queued"
        assert persisted_job.book_id == persisted_book.book_id
        submissions.append((download_url, category, save_path, tags))
        return None

    monkeypatch.setattr(acquisition_service, "submit_to_client", submit_to_client)

    response = await client.post(
        "/v1/acquisition/submissions/batch",
        headers=auth_headers,
        json={
            "endpoint_id": str(endpoint_id),
            "release_tokens": [token],
        },
    )

    assert response.status_code == 201
    item = response.json()["items"][0]
    assert item["index"] == 0
    assert item["error"] is None
    assert "download_url" not in item["job"]
    assert item["job"]["status"] == "submitted"
    assert item["job"]["book_id"] is not None

    job = (await db_session.execute(select(AcquisitionJob))).scalar_one()
    book = (await db_session.execute(select(SyncBook))).scalar_one()
    assert job.book_id == book.book_id == UUID(item["job"]["book_id"])
    assert job.download_url is None
    assert book.title == "A Test Book"
    assert submissions == [
        (
            release_url,
            "papyrus",
            f"/downloads/{owner_user_id}/{job.job_id}",
            [f"papyrus:{job.job_id}"],
        )
    ]


async def test_single_submission_wraps_the_token_only_batch_service(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint_id = uuid4()
    job = AcquisitionJob(
        owner_user_id=UUID(auth_user["user_id"]),
        endpoint_id=None,
        title="A Test Book",
        status="submitted",
    )
    db_session.add(job)
    await db_session.flush()

    async def submit_release_batch(
        session: AsyncSession,
        owner_user_id: UUID,
        submitted_endpoint_id: UUID,
        release_tokens: list[str],
    ) -> list[acquisition_service.BatchSubmissionResult]:
        assert session is not None
        assert owner_user_id == UUID(auth_user["user_id"])
        assert submitted_endpoint_id == endpoint_id
        assert release_tokens == ["opaque-release-token"]

        return [
            acquisition_service.BatchSubmissionResult(
                index=0,
                job=job,
                error=None,
            )
        ]

    monkeypatch.setattr(acquisition_routes, "submit_release_batch", submit_release_batch)

    response = await client.post(
        "/v1/acquisition/submissions",
        headers=auth_headers,
        json={
            "endpoint_id": str(endpoint_id),
            "release_token": "opaque-release-token",
        },
    )

    assert response.status_code == 201
    assert response.json()["job_id"] == str(job.job_id)
    assert "download_url" not in response.json()


async def test_batch_submission_isolates_invalid_release_tokens(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(app_settings, "acquisition_import_root", str(tmp_path), raising=False)
    owner_user_id = UUID(auth_user["user_id"])
    download_client = AcquisitionEndpoint(
        owner_user_id=owner_user_id,
        name="qBittorrent",
        kind="qbittorrent",
        base_url="http://qbittorrent.local:8080",
        download_root="/downloads",
    )
    indexer = AcquisitionEndpoint(
        owner_user_id=owner_user_id,
        name="Prowlarr",
        kind="prowlarr",
        base_url="http://prowlarr.local:9696",
    )
    db_session.add_all([download_client, indexer])
    await db_session.commit()
    valid_token = acquisition_service.create_release_token(
        acquisition_service.ReleaseCandidate(
            title="Valid Book",
            download_url="magnet:?xt=urn:btih:valid",
            protocol="torrent",
            indexer="Test Indexer",
            size_bytes=None,
            seeders=None,
            publish_date=None,
            format_hints=[],
        ),
        indexer,
    )

    async def submit_to_client(*args: object, **kwargs: object) -> str | None:
        return None

    monkeypatch.setattr(acquisition_service, "submit_to_client", submit_to_client)

    response = await client.post(
        "/v1/acquisition/submissions/batch",
        headers=auth_headers,
        json={
            "endpoint_id": str(download_client.endpoint_id),
            "release_tokens": [valid_token, "invalid-token"],
        },
    )

    assert response.status_code == 201
    items = response.json()["items"]
    assert items[0]["job"]["title"] == "Valid Book"
    assert items[0]["error"] is None
    assert items[1] == {
        "index": 1,
        "job": None,
        "error": "Release token is invalid or expired",
    }
    assert await db_session.scalar(select(func.count()).select_from(AcquisitionJob)) == 1
    assert await db_session.scalar(select(func.count()).select_from(SyncBook)) == 1


async def test_job_list_is_paginated_and_job_detail_is_owner_scoped(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
) -> None:
    owner_user_id = UUID(auth_user["user_id"])
    other_user = User(display_name="Other User")
    db_session.add(other_user)
    await db_session.flush()
    first_job = AcquisitionJob(
        owner_user_id=owner_user_id,
        title="First",
        status="submitted",
    )
    second_job = AcquisitionJob(
        owner_user_id=owner_user_id,
        title="Second",
        status="failed",
    )
    other_job = AcquisitionJob(
        owner_user_id=other_user.user_id,
        title="Private",
        status="submitted",
    )
    db_session.add_all([first_job, second_job, other_job])
    await db_session.commit()

    response = await client.get(
        "/v1/acquisition/jobs",
        headers=auth_headers,
        params={"limit": 1, "offset": 0},
    )

    assert response.status_code == 200
    page = response.json()
    assert page["total"] == 2
    assert page["limit"] == 1
    assert page["offset"] == 0
    assert len(page["items"]) == 1
    assert "download_url" not in page["items"][0]

    response = await client.get(
        f"/v1/acquisition/jobs/{first_job.job_id}",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["job_id"] == str(first_job.job_id)

    response = await client.get(
        f"/v1/acquisition/jobs/{other_job.job_id}",
        headers=auth_headers,
    )

    assert response.status_code == 404


async def test_job_files_marks_supported_book_candidates(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
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
    job = AcquisitionJob(
        owner_user_id=owner_user_id,
        endpoint_id=endpoint.endpoint_id,
        title="Multi-file release",
        status="needs_file_selection",
        client_hash="abc123",
    )
    db_session.add(job)
    await db_session.commit()

    class FakeQbittorrentClient:
        async def files(self, torrent_hash: str) -> list[acquisition_service.QbittorrentFile]:
            assert torrent_hash == "abc123"
            return [
                acquisition_service.QbittorrentFile(0, "cover.jpg", 100, 10_000, 1),
                acquisition_service.QbittorrentFile(1, "Book.EPUB", 1024, 10_000, 1),
                acquisition_service.QbittorrentFile(2, "extras/book.pdf", 2048, 5000, 1),
            ]

    async def connect(endpoint: AcquisitionEndpoint) -> FakeQbittorrentClient:
        return FakeQbittorrentClient()

    monkeypatch.setattr(
        acquisition_service.QbittorrentClient,
        "connect",
        staticmethod(connect),
    )

    response = await client.get(
        f"/v1/acquisition/jobs/{job.job_id}/files",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json() == [
        {
            "index": 0,
            "name": "cover.jpg",
            "size_bytes": 100,
            "progress_basis_points": 10_000,
            "priority": 1,
            "supported": False,
        },
        {
            "index": 1,
            "name": "Book.EPUB",
            "size_bytes": 1024,
            "progress_basis_points": 10_000,
            "priority": 1,
            "supported": True,
        },
        {
            "index": 2,
            "name": "extras/book.pdf",
            "size_bytes": 2048,
            "progress_basis_points": 5000,
            "priority": 1,
            "supported": True,
        },
    ]


async def test_file_selection_prioritizes_candidate_and_resumes_job(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
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
    job = AcquisitionJob(
        owner_user_id=owner_user_id,
        endpoint_id=endpoint.endpoint_id,
        title="Multi-file release",
        status="needs_file_selection",
        client_hash="abc123",
    )
    db_session.add(job)
    await db_session.commit()
    selections: list[tuple[str, int, list[int]]] = []

    class FakeQbittorrentClient:
        async def files(self, torrent_hash: str) -> list[acquisition_service.QbittorrentFile]:
            return [
                acquisition_service.QbittorrentFile(0, "cover.jpg", 100, 10_000, 1),
                acquisition_service.QbittorrentFile(1, "book.epub", 1024, 10_000, 1),
                acquisition_service.QbittorrentFile(2, "extras/book.pdf", 2048, 5000, 1),
            ]

        async def select_file(
            self,
            torrent_hash: str,
            *,
            selected_index: int,
            file_indices: list[int],
        ) -> None:
            selections.append((torrent_hash, selected_index, file_indices))

    async def connect(endpoint: AcquisitionEndpoint) -> FakeQbittorrentClient:
        return FakeQbittorrentClient()

    monkeypatch.setattr(
        acquisition_service.QbittorrentClient,
        "connect",
        staticmethod(connect),
    )

    response = await client.post(
        f"/v1/acquisition/jobs/{job.job_id}/file-selection",
        headers=auth_headers,
        json={"file_index": 2},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "downloading"
    assert response.json()["selected_file_path"] == "extras/book.pdf"
    assert selections == [("abc123", 2, [0, 1, 2])]

    await db_session.refresh(job)
    assert job.status == "downloading"
    assert job.selected_file_path == "extras/book.pdf"
    assert job.error is None
    assert job.next_poll_at is not None


async def test_cancel_job_deletes_partial_data_and_retains_cancelled_placeholder(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_user_id = UUID(auth_user["user_id"])
    endpoint = AcquisitionEndpoint(
        owner_user_id=owner_user_id,
        name="qBittorrent",
        kind="qbittorrent",
        base_url="http://qbittorrent.local:8080",
        download_root="/downloads",
    )
    book = SyncBook(owner_user_id=owner_user_id, title="Downloading Book")
    db_session.add_all([endpoint, book])
    await db_session.flush()
    job = AcquisitionJob(
        owner_user_id=owner_user_id,
        endpoint_id=endpoint.endpoint_id,
        book_id=book.book_id,
        title=book.title,
        status="downloading",
        client_hash="abc123",
    )
    db_session.add(job)
    await db_session.commit()
    deleted_hashes: list[str] = []

    class FakeQbittorrentClient:
        async def delete_torrent(self, torrent_hash: str) -> None:
            deleted_hashes.append(torrent_hash)

    async def connect(endpoint: AcquisitionEndpoint) -> FakeQbittorrentClient:
        return FakeQbittorrentClient()

    monkeypatch.setattr(
        acquisition_service.QbittorrentClient,
        "connect",
        staticmethod(connect),
    )

    response = await client.post(
        f"/v1/acquisition/jobs/{job.job_id}/cancel",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert response.json()["cancelled_at"] is not None
    assert deleted_hashes == ["abc123"]

    response = await client.post(
        f"/v1/acquisition/jobs/{job.job_id}/cancel",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert deleted_hashes == ["abc123"]
    assert await db_session.get(SyncBook, book.book_id) is not None


async def test_cancel_job_waits_for_monitor_transition_before_deleting_torrent(
    auth_user: dict[str, str],
    test_session_maker: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_user_id = UUID(auth_user["user_id"])

    async with test_session_maker() as setup_session:
        endpoint = AcquisitionEndpoint(
            owner_user_id=owner_user_id,
            name="qBittorrent",
            kind="qbittorrent",
            base_url="http://qbittorrent.local:8080",
            download_root="/downloads",
        )
        setup_session.add(endpoint)
        await setup_session.flush()

        job = AcquisitionJob(
            owner_user_id=owner_user_id,
            endpoint_id=endpoint.endpoint_id,
            title="Completing Book",
            status="downloading",
            client_hash="abc123",
        )
        setup_session.add(job)
        await setup_session.commit()
        job_id = job.job_id

    async def connect(endpoint: AcquisitionEndpoint) -> None:
        raise AssertionError("Completed jobs must not delete qBittorrent data")

    monkeypatch.setattr(
        acquisition_service.QbittorrentClient,
        "connect",
        staticmethod(connect),
    )

    async with (
        test_session_maker() as monitor_session,
        test_session_maker() as cancel_session,
    ):
        locked_job = (
            await monitor_session.execute(
                select(AcquisitionJob).where(AcquisitionJob.job_id == job_id).with_for_update()
            )
        ).scalar_one()
        locked_job.status = "completed"

        cancel_task = asyncio.create_task(
            acquisition_service.cancel_job(
                cancel_session,
                owner_user_id,
                job_id,
            )
        )
        await asyncio.sleep(0.05)

        assert not cancel_task.done()

        await monitor_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await asyncio.wait_for(cancel_task, timeout=1)

    assert exc_info.value.status_code == 409


async def test_delete_failed_job_removes_its_unimported_placeholder(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
) -> None:
    owner_user_id = UUID(auth_user["user_id"])
    book = SyncBook(owner_user_id=owner_user_id, title="Failed Book")
    db_session.add(book)
    await db_session.flush()
    job = AcquisitionJob(
        owner_user_id=owner_user_id,
        book_id=book.book_id,
        title=book.title,
        status="failed",
        error="Import failed",
    )
    db_session.add(job)
    await db_session.commit()
    job_id = job.job_id
    book_id = book.book_id

    response = await client.delete(
        f"/v1/acquisition/jobs/{job_id}",
        headers=auth_headers,
    )

    assert response.status_code == 204
    db_session.expire_all()
    assert await db_session.get(AcquisitionJob, job_id) is None
    assert await db_session.get(SyncBook, book_id) is None


async def test_retry_import_requeues_failed_job_without_resubmitting_download(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
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
    job = AcquisitionJob(
        owner_user_id=owner_user_id,
        endpoint_id=endpoint.endpoint_id,
        title="Downloaded Book",
        status="failed",
        selected_file_path="book.epub",
        submitted_at=datetime.now(UTC),
        error="Import quota exceeded",
    )
    db_session.add(job)
    await db_session.commit()

    response = await client.post(
        f"/v1/acquisition/jobs/{job.job_id}/retry-import",
        headers=auth_headers,
    )

    assert response.status_code == 200
    assert response.json()["status"] == "downloading"
    assert response.json()["retry_count"] == 1
    assert response.json()["error"] is None
    assert response.json()["next_poll_at"] is not None


async def test_retry_import_rejects_job_that_never_reached_qbittorrent(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
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

    job = AcquisitionJob(
        owner_user_id=owner_user_id,
        endpoint_id=endpoint.endpoint_id,
        title="Rejected Book",
        status="failed",
        error="qBittorrent rejected the release",
    )
    db_session.add(job)
    await db_session.commit()

    response = await client.post(
        f"/v1/acquisition/jobs/{job.job_id}/retry-import",
        headers=auth_headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"] == "Acquisition job was not submitted to qBittorrent"


async def test_legacy_raw_submission_is_rejected(
    client: AsyncClient,
    auth_headers: dict[str, str],
) -> None:
    response = await client.post(
        "/v1/acquisition/submissions",
        headers=auth_headers,
        json={
            "endpoint_id": str(uuid4()),
            "title": "Rejected release",
            "download_url": "magnet:?xt=urn:btih:test",
        },
    )

    assert response.status_code == 422


async def test_delete_endpoint_is_blocked_while_jobs_are_active(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
) -> None:
    endpoint_response = await client.post(
        "/v1/acquisition/endpoints",
        headers=auth_headers,
        json={
            "name": "Disposable client",
            "kind": "qbittorrent",
            "base_url": "http://qbittorrent.local:8080",
        },
    )
    endpoint_id = UUID(endpoint_response.json()["endpoint_id"])
    owner_user_id = UUID(auth_user["user_id"])
    rule = AcquisitionRule(
        owner_user_id=owner_user_id,
        name="Affected rule",
        query="book",
        endpoint_ids=[str(endpoint_id)],
        download_client_id=endpoint_id,
        enabled=True,
    )
    job = AcquisitionJob(
        owner_user_id=owner_user_id,
        endpoint_id=endpoint_id,
        title="Audited release",
        download_url="magnet:?xt=urn:btih:test",
        status="submitted",
    )

    db_session.add_all([rule, job])
    await db_session.commit()

    response = await client.delete(f"/v1/acquisition/endpoints/{endpoint_id}", headers=auth_headers)

    assert response.status_code == 409
    await db_session.refresh(job)
    await db_session.refresh(rule)
    assert job.endpoint_id == endpoint_id
    assert rule.download_client_id == endpoint_id
    assert rule.endpoint_ids == [str(endpoint_id)]
    assert rule.enabled is True


async def test_connection_checks_unsaved_endpoint_without_persisting(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[AcquisitionEndpoint] = []

    async def test_connection(endpoint: AcquisitionEndpoint) -> None:
        captured.append(endpoint)

    monkeypatch.setattr(acquisition_routes, "test_endpoint_connection", test_connection, raising=False)
    before_count = await db_session.scalar(select(func.count()).select_from(AcquisitionEndpoint))

    response = await client.post(
        "/v1/acquisition/endpoints/test",
        headers=auth_headers,
        json={
            "kind": "prowlarr",
            "base_url": "http://prowlarr.local:9696",
            "api_key": "unsaved-key",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert captured[0].kind == "prowlarr"
    assert decrypt_secret_payload(captured[0].credentials["encrypted"])["api_key"] == "unsaved-key"
    after_count = await db_session.scalar(select(func.count()).select_from(AcquisitionEndpoint))
    assert after_count == before_count


async def test_connection_merges_owned_endpoint_overrides_without_persisting(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint_response = await client.post(
        "/v1/acquisition/endpoints",
        headers=auth_headers,
        json={
            "name": "Saved Prowlarr",
            "kind": "prowlarr",
            "base_url": "http://prowlarr.local:9696",
            "api_key": "saved-key",
        },
    )
    captured: list[AcquisitionEndpoint] = []

    async def test_connection(endpoint: AcquisitionEndpoint) -> None:
        captured.append(endpoint)

    monkeypatch.setattr(acquisition_routes, "test_endpoint_connection", test_connection, raising=False)
    before_count = await db_session.scalar(select(func.count()).select_from(AcquisitionEndpoint))

    response = await client.post(
        "/v1/acquisition/endpoints/test",
        headers=auth_headers,
        json={
            "endpoint_id": endpoint_response.json()["endpoint_id"],
            "base_url": "http://prowlarr-edited.local:9696",
            "api_key": "override",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert captured[0].base_url == "http://prowlarr-edited.local:9696/"
    assert decrypt_secret_payload(captured[0].credentials["encrypted"])["api_key"] == "override"
    after_count = await db_session.scalar(select(func.count()).select_from(AcquisitionEndpoint))
    assert after_count == before_count


async def test_connection_rejects_another_users_endpoint(
    client: AsyncClient,
    auth_headers: dict[str, str],
    db_session: AsyncSession,
) -> None:
    other_user = User(display_name="Other User")
    db_session.add(other_user)
    await db_session.flush()

    endpoint = AcquisitionEndpoint(
        owner_user_id=other_user.user_id,
        name="Other Prowlarr",
        kind="prowlarr",
        base_url="http://other-prowlarr.local:9696",
    )
    db_session.add(endpoint)
    await db_session.commit()

    response = await client.post(
        "/v1/acquisition/endpoints/test",
        headers=auth_headers,
        json={"endpoint_id": str(endpoint.endpoint_id)},
    )

    assert response.status_code == 404
