"""Tests for acquisition integration adapters."""

import json
from datetime import UTC, datetime, timedelta
from typing import cast
from urllib.parse import parse_qs
from uuid import uuid4

import pytest
from fastapi import HTTPException

from papyrus.models.acquisition import AcquisitionEndpoint
from papyrus.services import acquisition


def _endpoint(kind: str) -> AcquisitionEndpoint:
    return AcquisitionEndpoint(
        endpoint_id=uuid4(),
        owner_user_id=uuid4(),
        name=f"Test {kind}",
        kind=kind,
        base_url="http://integration.test",
    )


def _release_candidate() -> object:
    return acquisition.ReleaseCandidate(
        title="A Test Book",
        download_url="https://indexer.test/download?id=1&apikey=private",
        protocol="torrent",
        indexer="Test Indexer",
        size_bytes=1024,
        seeders=12,
        publish_date=None,
        format_hints=["epub"],
    )


def test_release_token_round_trip_preserves_private_release_data() -> None:
    endpoint = _endpoint("prowlarr")
    now = datetime(2026, 7, 25, 12, tzinfo=UTC)

    token = acquisition.create_release_token(_release_candidate(), endpoint, now=now)
    payload = acquisition.decode_release_token(token, endpoint.owner_user_id, now=now)

    assert payload.endpoint_id == endpoint.endpoint_id
    assert payload.owner_user_id == endpoint.owner_user_id
    assert payload.title == "A Test Book"
    assert payload.download_url == "https://indexer.test/download?id=1&apikey=private"
    assert payload.format_hints == ["epub"]


def test_release_token_rejects_another_owner() -> None:
    endpoint = _endpoint("prowlarr")
    token = acquisition.create_release_token(_release_candidate(), endpoint)

    with pytest.raises(HTTPException) as exc_info:
        acquisition.decode_release_token(token, uuid4())

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Release token is invalid or expired"


def test_release_token_rejects_expired_or_tampered_values() -> None:
    endpoint = _endpoint("prowlarr")
    issued_at = datetime(2026, 7, 25, 12, tzinfo=UTC)
    token = acquisition.create_release_token(_release_candidate(), endpoint, now=issued_at)

    with pytest.raises(HTTPException) as expired:
        acquisition.decode_release_token(token, endpoint.owner_user_id, now=issued_at + timedelta(minutes=6))

    with pytest.raises(HTTPException) as tampered:
        acquisition.decode_release_token(f"{token[:-1]}x", endpoint.owner_user_id, now=issued_at)

    assert expired.value.status_code == 400
    assert expired.value.detail == "Release token is invalid or expired"
    assert tampered.value.status_code == 400
    assert tampered.value.detail == "Release token is invalid or expired"


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


async def test_qbittorrent_submission_sends_managed_tag_and_save_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            (204, {"Set-Cookie": "QBT_SID_8082=test; path=/"}, b""),
            (200, {}, b""),
        ]
    )
    bodies: list[bytes] = []

    async def request(*args: object, **kwargs: object) -> tuple[int, dict[str, str], bytes]:
        bodies.append(cast(bytes, kwargs["body"]))
        return next(responses)

    monkeypatch.setattr(acquisition, "_request", request)

    await acquisition.submit_to_client(
        _endpoint("qbittorrent"),
        "magnet:?xt=urn:btih:test",
        "papyrus",
        "/downloads/user/job",
        tags=["papyrus:job"],
    )

    submission = parse_qs(bodies[1].decode())
    assert submission["category"] == ["papyrus"]
    assert submission["savepath"] == ["/downloads/user/job"]
    assert submission["tags"] == ["papyrus:job"]


async def test_qbittorrent_client_reuses_login_for_torrent_and_file_queries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            (204, {"Set-Cookie": "QBT_SID_8082=test; path=/"}, b""),
            (
                200,
                {},
                b'[{"hash":"abc123","state":"downloading","progress":0.5,'
                b'"downloaded":512,"total_size":1024,"dlspeed":128,"eta":4}]',
            ),
            (
                200,
                {},
                b'[{"index":0,"name":"book.epub","size":1024,"progress":0.5,"priority":1}]',
            ),
        ]
    )
    requests: list[tuple[str, dict[str, str]]] = []

    async def request(url: str, **kwargs: object) -> tuple[int, dict[str, str], bytes]:
        requests.append((url, cast(dict[str, str], kwargs.get("headers", {}))))
        return next(responses)

    monkeypatch.setattr(acquisition, "_request", request)

    client = await acquisition.QbittorrentClient.connect(_endpoint("qbittorrent"))
    torrent = await client.find_torrent(tag="papyrus:job")
    files = await client.files("abc123")

    assert torrent.hash == "abc123"
    assert torrent.progress_basis_points == 5000
    assert torrent.downloaded_bytes == 512
    assert torrent.total_bytes == 1024
    assert torrent.download_speed_bytes_per_second == 128
    assert torrent.eta_seconds == 4
    assert files == [
        acquisition.QbittorrentFile(
            index=0,
            name="book.epub",
            size_bytes=1024,
            progress_basis_points=5000,
            priority=1,
        )
    ]
    assert requests[1][0].endswith("api/v2/torrents/info?tag=papyrus%3Ajob")
    assert requests[2][0].endswith("api/v2/torrents/files?hash=abc123")
    assert requests[1][1]["Cookie"] == "QBT_SID_8082=test"
    assert requests[2][1]["Cookie"] == "QBT_SID_8082=test"


async def test_qbittorrent_client_falls_back_to_job_tag_when_saved_hash_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            (204, {"Set-Cookie": "QBT_SID_8082=test; path=/"}, b""),
            (200, {}, b"[]"),
            (
                200,
                {},
                b'[{"hash":"replacement","state":"downloading","progress":0.5,'
                b'"downloaded":512,"total_size":1024,"dlspeed":128,"eta":4}]',
            ),
        ]
    )
    request_urls: list[str] = []

    async def request(url: str, **kwargs: object) -> tuple[int, dict[str, str], bytes]:
        request_urls.append(url)
        return next(responses)

    monkeypatch.setattr(acquisition, "_request", request)

    client = await acquisition.QbittorrentClient.connect(_endpoint("qbittorrent"))
    torrent = await client.find_torrent(
        tag="papyrus:job",
        torrent_hash="stale-hash",
    )

    assert torrent.hash == "replacement"
    assert request_urls[1].endswith("api/v2/torrents/info?hashes=stale-hash")
    assert request_urls[2].endswith("api/v2/torrents/info?tag=papyrus%3Ajob")


async def test_qbittorrent_client_pauses_and_prioritizes_one_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            (204, {"Set-Cookie": "QBT_SID_8082=test; path=/"}, b""),
            (200, {}, b""),
            (200, {}, b""),
            (200, {}, b""),
            (200, {}, b""),
        ]
    )
    requests: list[tuple[str, dict[str, list[str]]]] = []

    async def request(url: str, **kwargs: object) -> tuple[int, dict[str, str], bytes]:
        if body := kwargs.get("body"):
            requests.append((url, parse_qs(cast(bytes, body).decode())))
        return next(responses)

    monkeypatch.setattr(acquisition, "_request", request)

    client = await acquisition.QbittorrentClient.connect(_endpoint("qbittorrent"))
    await client.select_file("abc123", selected_index=1, file_indices=[0, 1, 2])

    assert [url.rsplit("/", 1)[-1] for url, _ in requests[1:]] == [
        "pause",
        "filePrio",
        "filePrio",
        "resume",
    ]
    assert requests[1][1] == {"hashes": ["abc123"]}
    assert requests[2][1] == {
        "hash": ["abc123"],
        "id": ["0|2"],
        "priority": ["0"],
    }
    assert requests[3][1] == {
        "hash": ["abc123"],
        "id": ["1"],
        "priority": ["1"],
    }
    assert requests[4][1] == {"hashes": ["abc123"]}


async def test_qbittorrent_client_deletes_torrent_and_downloaded_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            (204, {"Set-Cookie": "QBT_SID_8082=test; path=/"}, b""),
            (200, {}, b""),
        ]
    )
    requests: list[tuple[str, dict[str, list[str]]]] = []

    async def request(url: str, **kwargs: object) -> tuple[int, dict[str, str], bytes]:
        if body := kwargs.get("body"):
            requests.append((url, parse_qs(cast(bytes, body).decode())))
        return next(responses)

    monkeypatch.setattr(acquisition, "_request", request)

    client = await acquisition.QbittorrentClient.connect(_endpoint("qbittorrent"))
    await client.delete_torrent("abc123")

    assert requests[1][0].endswith("api/v2/torrents/delete")
    assert requests[1][1] == {
        "hashes": ["abc123"],
        "deleteFiles": ["true"],
    }


async def test_qbittorrent_submission_accepts_lowercase_session_cookie_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = iter(
        [
            (204, {"set-cookie": "QBT_SID_8082=test; path=/"}, b""),
            (200, {}, b""),
        ]
    )
    request_headers: list[dict[str, str]] = []

    async def request(*args: object, **kwargs: object) -> tuple[int, dict[str, str], bytes]:
        request_headers.append(kwargs.get("headers", {}))
        return next(responses)

    monkeypatch.setattr(acquisition, "_request", request)

    await acquisition.submit_to_client(
        _endpoint("qbittorrent"),
        "magnet:?xt=urn:btih:test",
        None,
        None,
    )

    assert request_headers[1]["Cookie"] == "QBT_SID_8082=test"


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
