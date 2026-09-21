"""Anonymous OPDS relay contract tests without database fixtures."""

from unittest.mock import Mock

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.requests import ClientDisconnect

from papyrus.api.routes import opds as route
from papyrus.config import get_settings
from papyrus.main import create_app
from papyrus.services.opds import RelayResource


@pytest.mark.parametrize(
    "url", ["http://127.0.0.1/feed", "http://169.254.169.254/latest/meta-data/", "file:///etc/passwd"]
)
async def test_anonymous_relay_rejects_unsafe_destinations(url: str) -> None:
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.post(
            f"{get_settings().api_prefix}/opds/relay",
            json={"url": url, "catalog_url": "https://books.example/feed"},
        )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "OPDS_RELAY_ERROR"


async def test_relay_rejects_excessive_resource_limit() -> None:
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.post(
            f"{get_settings().api_prefix}/opds/relay",
            json={"url": "https://books.example/feed", "catalog_url": "https://books.example/feed", "max_bytes": 2**30},
        )

    assert response.status_code == 422


async def test_anonymous_stream_preserves_final_url_and_exposes_cors_metadata(monkeypatch) -> None:
    resource = Mock(spec=RelayResource)
    resource.url = "https://cdn.example/path/book.epub"
    resource.content_type = "application/epub+zip"
    resource.length = 4
    resource.chunks.return_value = iter([b"bo", b"ok"])
    open_resource = Mock(return_value=resource)
    monkeypatch.setattr(route, "open_resource", open_resource)
    monkeypatch.setattr(get_settings(), "cors_origins", ["https://papyrus.example"])
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.post(
            f"{get_settings().api_prefix}/opds/relay",
            headers={"Origin": "https://papyrus.example", "Cookie": "session=do-not-forward"},
            json={"url": "https://books.example/edition", "catalog_url": "https://books.example/feed"},
        )

    assert response.status_code == 200
    assert response.content == b"book"
    assert response.headers["x-opds-url"] == resource.url
    assert response.headers["content-type"] == "application/epub+zip"
    assert response.headers["content-length"] == "4"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["access-control-allow-origin"] == "https://papyrus.example"
    assert "X-OPDS-URL" in response.headers["access-control-expose-headers"]
    assert "set-cookie" not in response.headers
    assert open_resource.call_args.args[0].credentials is None
    resource.close.assert_called_once()


async def test_relay_rate_limits_anonymous_requests(monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_general", 1)
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        payload = {"url": "http://127.0.0.1/feed", "catalog_url": "https://books.example/feed"}
        first = await client.post(f"{get_settings().api_prefix}/opds/relay", json=payload)
        second = await client.post(f"{get_settings().api_prefix}/opds/relay", json=payload)

    assert first.status_code == 400
    assert second.status_code == 429


async def test_disconnect_before_streaming_closes_upstream() -> None:
    resource = Mock(spec=RelayResource)
    resource.url = "https://books.example/feed"
    resource.content_type = "application/opds+json"
    resource.length = None
    resource.chunks.return_value = iter([b"book"])

    async def receive():
        return {"type": "http.disconnect"}

    async def send(message):
        return None

    await route.RelayResponse(resource)({"type": "http", "asgi": {"spec_version": "2.0"}}, receive, send)
    resource.close.assert_called_once()


async def test_stream_error_closes_upstream() -> None:
    resource = Mock(spec=RelayResource)
    resource.url = "https://books.example/feed"
    resource.content_type = "application/opds+json"
    resource.length = None

    def chunks():
        yield b"first"
        raise OSError("upstream connection lost")

    resource.chunks.return_value = chunks()

    async def receive():
        return {"type": "http.request"}

    async def send(message):
        return None

    with pytest.raises(ClientDisconnect):
        await route.RelayResponse(resource)({"type": "http", "asgi": {"spec_version": "2.4"}}, receive, send)

    resource.close.assert_called_once()
