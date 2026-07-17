"""Tests for private BitTorrent acquisition configuration."""

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.api.routes import acquisition as acquisition_routes
from papyrus.core.security import decrypt_secret_payload
from papyrus.main import settings as app_settings
from papyrus.models.acquisition import AcquisitionEndpoint, AcquisitionJob, AcquisitionRule
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


async def test_capabilities_advertise_torrent_only_scope(client: AsyncClient, auth_headers: dict[str, str]) -> None:
    response = await client.get("/v1/acquisition/capabilities", headers=auth_headers)

    assert response.status_code == 200
    body = response.json()
    assert body["indexer_kinds"] == ["prowlarr", "torznab"]
    assert body["download_client_kinds"] == ["qbittorrent", "transmission", "deluge"]
    assert "newznab" not in body["endpoint_kinds"]
    assert body["arr_commands"]["readarr"] == ["AuthorSearch", "BookSearch"]


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


async def test_rejected_submission_persists_failed_job(
    client: AsyncClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def request(*args: object, **kwargs: object) -> tuple[int, dict[str, str], bytes]:
        return 200, {}, b'{"result":"invalid or corrupt torrent file","arguments":{}}'

    monkeypatch.setattr(acquisition_service, "_request", request)
    endpoint_response = await client.post(
        "/v1/acquisition/endpoints",
        headers=auth_headers,
        json={
            "name": "Transmission",
            "kind": "transmission",
            "base_url": "http://transmission.local:9091",
        },
    )
    endpoint_id = endpoint_response.json()["endpoint_id"]

    response = await client.post(
        "/v1/acquisition/submissions",
        headers=auth_headers,
        json={
            "endpoint_id": endpoint_id,
            "title": "Rejected release",
            "download_url": "magnet:?xt=urn:btih:test",
        },
    )

    assert response.status_code == 201
    assert response.json()["status"] == "failed"
    assert response.json()["error"] == "Transmission rejected the release"


async def test_delete_endpoint_preserves_jobs_and_disables_rules(
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

    assert response.status_code == 204
    await db_session.refresh(job)
    await db_session.refresh(rule)
    assert job.endpoint_id is None
    assert rule.download_client_id is None
    assert rule.endpoint_ids == []
    assert rule.enabled is False


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
