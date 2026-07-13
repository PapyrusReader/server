"""Tests for private BitTorrent acquisition configuration."""

from httpx import AsyncClient


async def test_create_and_list_endpoint_hides_credentials(client: AsyncClient, auth_headers: dict[str, str]) -> None:
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
