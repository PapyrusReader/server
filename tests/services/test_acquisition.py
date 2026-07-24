"""Tests for acquisition integration adapters."""

import json
from typing import cast
from uuid import uuid4

import pytest
from fastapi import HTTPException

from papyrus.models.acquisition import AcquisitionEndpoint
from papyrus.services import acquisition


def _endpoint(kind: str) -> AcquisitionEndpoint:
    return AcquisitionEndpoint(
        owner_user_id=uuid4(),
        name=f"Test {kind}",
        kind=kind,
        base_url="http://integration.test",
    )


async def test_transmission_rejects_rpc_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    async def request(*args: object, **kwargs: object) -> tuple[int, dict[str, str], bytes]:
        return 200, {}, b'{"result":"invalid or corrupt torrent file","arguments":{}}'

    monkeypatch.setattr(acquisition, "_request", request)

    with pytest.raises(HTTPException) as exc_info:
        await acquisition.submit_to_client(
            _endpoint("transmission"),
            "magnet:?xt=urn:btih:test",
            None,
            None,
        )

    assert exc_info.value.status_code == 502


async def test_deluge_uses_url_method_for_http_torrent(monkeypatch: pytest.MonkeyPatch) -> None:
    bodies: list[dict[str, object]] = []

    async def request(*args: object, **kwargs: object) -> tuple[int, dict[str, str], bytes]:
        bodies.append(json.loads(cast(bytes, kwargs["body"])))

        if len(bodies) == 1:
            return 200, {"Set-Cookie": "_session_id=test"}, b'{"result":true,"error":null,"id":1}'

        return 200, {}, b'{"result":"torrent-id","error":null,"id":2}'

    monkeypatch.setattr(acquisition, "_request", request)

    await acquisition.submit_to_client(
        _endpoint("deluge"),
        "https://indexer.test/release.torrent",
        None,
        None,
    )

    assert bodies[1]["method"] == "core.add_torrent_url"


async def test_deluge_rejects_json_rpc_error(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter(
        [
            (200, {"Set-Cookie": "_session_id=test"}, b'{"result":true,"error":null,"id":1}'),
            (200, {}, b'{"result":null,"error":{"message":"invalid torrent"},"id":2}'),
        ]
    )

    async def request(*args: object, **kwargs: object) -> tuple[int, dict[str, str], bytes]:
        return next(responses)

    monkeypatch.setattr(acquisition, "_request", request)

    with pytest.raises(HTTPException) as exc_info:
        await acquisition.submit_to_client(
            _endpoint("deluge"),
            "magnet:?xt=urn:btih:test",
            None,
            None,
        )

    assert exc_info.value.status_code == 502


async def test_prowlarr_rejects_invalid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    async def request(*args: object, **kwargs: object) -> tuple[int, dict[str, str], bytes]:
        return 200, {}, b"not-json"

    monkeypatch.setattr(acquisition, "_request", request)

    with pytest.raises(HTTPException) as exc_info:
        await acquisition.search_endpoint(_endpoint("prowlarr"), "book")

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "Prowlarr returned invalid JSON"


async def test_qbittorrent_connection_test_accepts_empty_204_login(monkeypatch: pytest.MonkeyPatch) -> None:
    async def request(*args: object, **kwargs: object) -> tuple[int, dict[str, str], bytes]:
        return 204, {"Set-Cookie": "QBT_SID_8082=test; path=/"}, b""

    monkeypatch.setattr(acquisition, "_request", request)

    await acquisition.test_endpoint_connection(_endpoint("qbittorrent"))


async def test_qbittorrent_submission_accepts_empty_204_login(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = iter(
        [
            (204, {"Set-Cookie": "QBT_SID_8082=test; path=/"}, b""),
            (200, {}, b""),
        ]
    )

    async def request(*args: object, **kwargs: object) -> tuple[int, dict[str, str], bytes]:
        return next(responses)

    monkeypatch.setattr(acquisition, "_request", request)

    await acquisition.submit_to_client(
        _endpoint("qbittorrent"),
        "magnet:?xt=urn:btih:test",
        None,
        None,
    )


async def test_qbittorrent_rejects_failed_login_body(monkeypatch: pytest.MonkeyPatch) -> None:
    async def request(*args: object, **kwargs: object) -> tuple[int, dict[str, str], bytes]:
        return 200, {}, b"Fails."

    monkeypatch.setattr(acquisition, "_request", request)

    with pytest.raises(HTTPException) as exc_info:
        await acquisition.submit_to_client(
            _endpoint("qbittorrent"),
            "magnet:?xt=urn:btih:test",
            None,
            None,
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.detail == "qBittorrent authentication failed"


async def test_arr_commands_use_v3_api(monkeypatch: pytest.MonkeyPatch) -> None:
    urls: list[str] = []

    async def request(url: str, **kwargs: object) -> tuple[int, dict[str, str], bytes]:
        urls.append(url)
        return 201, {}, b'{"id":1}'

    monkeypatch.setattr(acquisition, "_request", request)

    await acquisition.dispatch_arr_command(_endpoint("readarr"), "BookSearch", [1])

    assert urls == ["http://integration.test/api/v3/command"]
