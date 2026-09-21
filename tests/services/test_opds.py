"""OPDS relay transport, resource limits, and destination isolation."""

import http.client
import io
import socket
import threading
import time
from contextlib import suppress
from unittest.mock import Mock
from urllib.parse import SplitResult, urlsplit

import pytest

from papyrus.config import get_settings
from papyrus.schemas.opds import OpdsCredentials, OpdsRelayRequest
from papyrus.services import opds


def upstream(body: bytes = b"book", status: int = 200, **headers: str) -> http.client.HTTPResponse:
    raw = f"HTTP/1.1 {status} Response\r\n".encode()
    raw += b"".join(f"{key.replace('_', '-')}: {value}\r\n".encode() for key, value in headers.items())
    stream = Mock()
    stream.makefile.return_value = io.BytesIO(raw + b"\r\n" + body)
    response = http.client.HTTPResponse(stream)
    response.begin()
    return response


@pytest.fixture
def transport(monkeypatch: pytest.MonkeyPatch) -> tuple[list[http.client.HTTPResponse | Exception], list[Mock]]:
    responses: list[http.client.HTTPResponse | Exception] = []
    connections: list[Mock] = []

    def connection(parts: SplitResult, address: str) -> Mock:
        item = Mock()
        item.parts = parts
        item.address = address
        item._aborted = False
        result = responses.pop(0)
        if isinstance(result, Exception):
            item.getresponse.side_effect = result
        else:
            item.getresponse.return_value = result

        connections.append(item)
        return item

    monkeypatch.setattr(opds, "_PinnedConnection", connection)
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))])
    monkeypatch.setattr(get_settings(), "opds_relay_enabled", True)
    monkeypatch.setattr(get_settings(), "opds_relay_allowed_hosts", [])
    monkeypatch.setattr(opds, "_slots", opds.threading.BoundedSemaphore(8))
    return responses, connections


def request(**kwargs: object) -> OpdsRelayRequest:
    return OpdsRelayRequest.model_validate(
        {
            "url": "https://books.example/feed",
            "catalog_url": "https://books.example/feed",
            **kwargs,
        }
    )


def test_streams_redirected_bytes_with_final_url_and_origin_scoped_credentials(transport) -> None:
    responses, connections = transport
    responses.extend(
        [
            upstream(status=302, Location="/edition"),
            upstream(status=307, Location="https://cdn.example/book.epub"),
            upstream(b"epub bytes", Content_Type="application/epub+zip", Content_Length="10", Set_Cookie="secret=1"),
        ]
    )
    resource = opds.open_resource(request(credentials={"username": "reader", "password": "secret"}))
    assert resource.url == "https://cdn.example/book.epub"
    assert resource.length == 10
    assert resource.content_type == "application/epub+zip"
    assert b"".join(resource.chunks()) == b"epub bytes"
    assert connections[0].request.call_args.kwargs["headers"]["Authorization"] == "Basic cmVhZGVyOnNlY3JldA=="
    assert "Authorization" in connections[1].request.call_args.kwargs["headers"]
    assert "Authorization" not in connections[2].request.call_args.kwargs["headers"]
    assert all(connection.close.called for connection in connections)


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "::1",
        "fe80::1",
        "::ffff:127.0.0.1",
        "224.0.0.1",
        "2002:7f00:1::",
    ],
)
def test_rejects_nonpublic_dns_answers_before_connecting(transport, monkeypatch, address: str) -> None:
    _, connections = transport
    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: [(2, 1, 6, "", (address, 80))])
    with pytest.raises(opds.OpdsRelayError, match="public"):
        opds.open_resource(request())

    assert connections == []


def test_rechecks_redirect_hosts_and_releases_connection(transport, monkeypatch) -> None:
    responses, connections = transport
    responses.append(upstream(status=302, Location="http://internal.example/private"))
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda host, *args, **kwargs: [
            (
                2,
                1,
                6,
                "",
                (
                    "127.0.0.1" if host == "internal.example" else "93.184.216.34",
                    80,
                ),
            )
        ],
    )
    with pytest.raises(opds.OpdsRelayError, match="public"):
        opds.open_resource(request(url="http://books.example/feed"))

    assert len(connections) == 1
    assert connections[0].close.called


def test_rejects_mixed_public_and_private_dns_answers(transport, monkeypatch) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 80)),
            (2, 1, 6, "", ("127.0.0.1", 80)),
        ],
    )
    with pytest.raises(opds.OpdsRelayError, match="public"):
        opds.open_resource(request())


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@books.example/",
        "https://books.example:0/",
        "https://books.example:bad/",
        "ftp://books.example/",
        "https://books.example/\r\nX:1",
        "https://[fe80::1%eth0]/",
    ],
)
def test_rejects_unsafe_url_syntax(transport, url: str) -> None:
    with pytest.raises(opds.OpdsRelayError, match="HTTP or HTTPS"):
        opds.open_resource(request(url=url))


@pytest.mark.parametrize(("status", "expected"), [(401, 401), (403, 403), (404, 404), (500, 500), (206, 502)])
def test_maps_upstream_errors_without_exposing_response_content(transport, status: int, expected: int) -> None:
    responses, connections = transport
    responses.append(upstream(b"upstream private error details", status=status))
    with pytest.raises(opds.OpdsRelayError) as error:
        opds.open_resource(request())

    assert error.value.status_code == expected
    assert "private error details" not in str(error.value)
    assert connections[0].close.called


@pytest.mark.parametrize("headers", [{"Content_Length": "5"}, {}])
def test_limits_known_and_unknown_length_streams_and_closes(transport, headers: dict[str, str]) -> None:
    responses, connections = transport
    responses.append(upstream(b"12345", **headers))
    with pytest.raises(opds.OpdsRelayError, match="too large"):
        resource = opds.open_resource(request(max_bytes=4))
        list(resource.chunks())

    assert connections[0].close.called


def test_truncated_stream_fails_and_closes(transport) -> None:
    responses, connections = transport
    responses.append(upstream(b"123", Content_Length="5"))
    resource = opds.open_resource(request())
    with pytest.raises(opds.OpdsRelayError, match="incomplete"):
        list(resource.chunks())

    assert connections[0].close.called


def test_enforces_concurrency_and_releases_unstarted_streams(transport) -> None:
    responses, _ = transport
    resources = []
    for _ in range(8):
        responses.append(upstream())
        resources.append(opds.open_resource(request()))

    with pytest.raises(opds.OpdsRelayError, match="busy"):
        opds.open_resource(request())

    for resource in resources:
        resource.close()
        resource.close()

    responses.append(upstream())
    assert b"".join(opds.open_resource(request()).chunks()) == b"book"


def test_failed_requests_release_slots_and_sanitize_errors(transport) -> None:
    responses, _ = transport
    for _ in range(10):
        responses.append(OSError("secret network details"))
        with pytest.raises(opds.OpdsRelayError, match="could not connect") as error:
            opds.open_resource(request())

        assert "secret" not in str(error.value)


def test_redirect_limit_and_https_downgrade(transport) -> None:
    responses, connections = transport
    responses.extend(upstream(status=302, Location="/loop") for _ in range(6))
    with pytest.raises(opds.OpdsRelayError, match="too many"):
        opds.open_resource(request())

    assert all(connection.close.called for connection in connections)
    responses.append(upstream(status=302, Location="http://books.example/file"))
    with pytest.raises(opds.OpdsRelayError, match="insecure"):
        opds.open_resource(request())


def test_configured_host_allowlist_is_enforced(transport, monkeypatch) -> None:
    monkeypatch.setattr(get_settings(), "opds_relay_allowed_hosts", ["other.example"])
    with pytest.raises(opds.OpdsRelayError, match="not enabled"):
        opds.open_resource(request())


def test_connects_to_checked_ip_with_original_tls_hostname(monkeypatch) -> None:
    create_connection = Mock(return_value=Mock())
    context = Mock()
    monkeypatch.setattr(socket, "create_connection", create_connection)
    monkeypatch.setattr(opds.ssl, "create_default_context", lambda: context)
    connection = opds._PinnedConnection(urlsplit("https://books.example/feed"), "93.184.216.34")
    connection.connect()
    create_connection.assert_called_once_with(("93.184.216.34", 443), timeout=30)
    context.wrap_socket.assert_called_once_with(
        create_connection.return_value, server_hostname="books.example", do_handshake_on_connect=False
    )
    context.wrap_socket.return_value.do_handshake.assert_called_once()


def test_credentials_are_redacted_from_model_repr() -> None:
    assert "secret" not in repr(OpdsCredentials(username="reader", password="secret"))


@pytest.mark.parametrize("status_first", [False, True])
def test_slow_headers_are_interrupted_by_open_timeout(monkeypatch, status_first: bool) -> None:
    client, upstream_socket = socket.socketpair()
    monkeypatch.setattr(opds, "_TIMEOUT", 0.1)
    monkeypatch.setattr(opds, "_resolve_public", lambda parts, deadline: "93.184.216.34")
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: client)
    stop = threading.Event()

    def trickle() -> None:
        with upstream_socket, suppress(OSError):
            status = b"HTTP/1.1 200 OK\r\n"
            if status_first:
                upstream_socket.sendall(status)

            data = (b"" if status_first else status) + b"Content-Type: application/epub+zip\r\n\r\nbook"
            for char in data:
                upstream_socket.send(bytes([char]))
                if stop.wait(0.02):
                    break

    worker = threading.Thread(target=trickle, daemon=True)
    worker.start()
    try:
        started = time.monotonic()
        with pytest.raises(opds.OpdsRelayError) as error:
            opds.open_resource(request(url="http://books.example/feed"))

        assert error.value.status_code == 504
        assert time.monotonic() - started < 1
    finally:
        stop.set()
        worker.join(timeout=1)
        client.close()


def test_slow_dns_is_bounded(monkeypatch) -> None:
    stop = threading.Event()

    def resolve(*args, **kwargs):
        stop.wait(2)
        return [(2, 1, 6, "", ("93.184.216.34", 80))]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(opds, "_TIMEOUT", 0.05)
    try:
        started = time.monotonic()
        with pytest.raises(opds.OpdsRelayError) as error:
            opds.open_resource(request())

        assert error.value.status_code == 504
        assert time.monotonic() - started < 1
    finally:
        stop.set()


def test_total_deadline_closes_unconsumed_resources_and_releases_capacity(transport, monkeypatch) -> None:
    responses, connections = transport
    monkeypatch.setattr(opds, "_TOTAL_TIMEOUT", 0.05)
    responses.append(upstream())
    resource = opds.open_resource(request())
    resource.timer.join(timeout=1)
    assert resource._closed
    assert connections[0].abort.called
    assert opds._slots.acquire(blocking=False)
    opds._slots.release()


def test_chunk_framing_trickle_is_interrupted_by_total_deadline(monkeypatch) -> None:
    client, upstream_socket = socket.socketpair()
    monkeypatch.setattr(opds, "_TIMEOUT", 0.3)
    monkeypatch.setattr(opds, "_TOTAL_TIMEOUT", 0.2)
    monkeypatch.setattr(opds, "_resolve_public", lambda parts, deadline: "93.184.216.34")
    monkeypatch.setattr(socket, "create_connection", lambda *args, **kwargs: client)
    stop = threading.Event()

    def trickle() -> None:
        with upstream_socket, suppress(OSError):
            upstream_socket.sendall(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n")
            for char in b"000000000000000000000004\r\nbook\r\n0\r\n\r\n":
                upstream_socket.send(bytes([char]))
                if stop.wait(0.02):
                    break

    worker = threading.Thread(target=trickle, daemon=True)
    worker.start()
    try:
        started = time.monotonic()
        resource = opds.open_resource(request(url="http://books.example/feed"))
        with pytest.raises((http.client.HTTPException, OSError, opds.OpdsRelayError)):
            list(resource.chunks())

        resource.timer.join(timeout=1)
        assert resource._closed
        assert time.monotonic() - started < 1
    finally:
        stop.set()
        worker.join(timeout=1)
        client.close()
