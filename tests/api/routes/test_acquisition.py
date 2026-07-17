"""Tests for private BitTorrent acquisition configuration."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.core.security import decrypt_secret_payload
from papyrus.main import settings as app_settings
from papyrus.models.acquisition import AcquisitionEndpoint


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
