"""Bounded OPDS relay with public-address validation and pinned HTTP connections."""

import base64
import http.client
import ipaddress
import socket
import ssl
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field
from urllib.parse import SplitResult, urljoin, urlsplit, urlunsplit

from papyrus.config import get_settings
from papyrus.core.exceptions import AppError
from papyrus.schemas.opds import OpdsRelayRequest

_slots = threading.BoundedSemaphore(8)
_dns_slots = threading.BoundedSemaphore(8)
_dns_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="opds-dns")
_TIMEOUT = 30
_TOTAL_TIMEOUT = 300
_CHUNK_BYTES = 64 * 1024
_REDIRECTS = {301, 302, 303, 307, 308}


class OpdsRelayError(AppError):
    def __init__(self, message: str, status_code: int = 400, *, retryable: bool = False) -> None:
        super().__init__(message, code="OPDS_RELAY_ERROR", status_code=status_code, details={"retryable": retryable})


def _parse_url(value: str) -> SplitResult:
    try:
        if not value.isascii() or any(ord(char) <= 32 or ord(char) == 127 for char in value) or "\\" in value:
            raise ValueError

        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or "%" in parts.hostname
            or parts.port == 0
        ):
            raise ValueError

        return parts._replace(fragment="")
    except ValueError as exc:
        raise OpdsRelayError("Enter an HTTP or HTTPS URL without embedded credentials.") from exc


def _origin(parts: SplitResult) -> tuple[str, str | None, int]:
    return parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)


def _resolve_public(parts: SplitResult, deadline: float) -> str:
    host = parts.hostname or ""
    allowed = get_settings().opds_relay_allowed_hosts
    if allowed and host.lower() not in {item.lower() for item in allowed}:
        raise OpdsRelayError("This catalog host is not enabled on the Papyrus server.", 403)

    if not _dns_slots.acquire(blocking=False):
        raise OpdsRelayError("The catalog relay is busy. Please retry shortly.", 503, retryable=True)

    future = _dns_pool.submit(socket.getaddrinfo, host, _origin(parts)[2], type=socket.SOCK_STREAM)
    future.add_done_callback(lambda _: _dns_slots.release())
    try:
        addresses = future.result(timeout=max(0, min(_TIMEOUT, deadline - time.monotonic())))
    except TimeoutError:
        future.cancel()
        raise

    if not addresses:
        raise OpdsRelayError("The catalog host could not be resolved.", 502)

    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if not ip.is_global or ip.is_multicast or (isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped):
            raise OpdsRelayError("The relay can only access public catalog addresses.")

    return str(addresses[0][4][0])


class _PinnedConnection(http.client.HTTPConnection):
    """Connect to the validated IP while retaining the original Host and TLS identity."""

    def __init__(self, parts: SplitResult, address: str) -> None:
        super().__init__(parts.hostname or "", port=_origin(parts)[2], timeout=_TIMEOUT)
        self._address = address
        self._tls = parts.scheme == "https"
        self._network_socket: socket.socket | None = None
        self._aborted = False

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address, self.port), timeout=_TIMEOUT)
        self._network_socket = self.sock
        if self._aborted:
            self.abort()
            raise TimeoutError

        if self._tls:
            try:
                self.sock = ssl.create_default_context().wrap_socket(
                    self.sock, server_hostname=self.host, do_handshake_on_connect=False
                )
                self._network_socket = self.sock
                if self._aborted:
                    self.abort()
                    raise TimeoutError

                self.sock.do_handshake()
            except BaseException:
                self.close()
                raise

    def abort(self) -> None:
        """Interrupt blocking headers, TLS, or chunk framing when the watchdog fires."""
        self._aborted = True
        if self._network_socket is not None:
            with suppress(OSError):
                self._network_socket.shutdown(socket.SHUT_RDWR)


@dataclass
class RelayResource:
    url: str
    content_type: str
    length: int | None
    response: http.client.HTTPResponse
    connection: _PinnedConnection
    max_bytes: int
    deadline: float
    _closed: bool = False
    timer: threading.Timer | None = None
    _close_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _read_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return

            self._closed = True

        if self.timer is not None:
            self.timer.cancel()

        self.connection.abort()
        try:
            with self._read_lock:
                self.response.close()
        finally:
            self.connection.close()
            _slots.release()

    def chunks(self) -> Iterator[bytes]:
        received = 0
        try:
            while True:
                if time.monotonic() > self.deadline:
                    raise OpdsRelayError("The catalog download timed out.", 504)

                with self._read_lock:
                    chunk = self.response.read1(min(_CHUNK_BYTES, self.max_bytes - received + 1))

                if time.monotonic() >= self.deadline:
                    raise OpdsRelayError("The catalog download timed out.", 504)

                if not chunk:
                    if self.length is not None and received != self.length:
                        raise OpdsRelayError("The catalog download was incomplete.", 502)

                    break

                received += len(chunk)
                if received > self.max_bytes:
                    raise OpdsRelayError("This resource is too large to load.", 413)

                yield chunk
        finally:
            self.close()


def open_resource(payload: OpdsRelayRequest) -> RelayResource:
    """Open one upstream response; transfer slot ownership to its closing stream."""
    if not get_settings().opds_relay_enabled:
        raise OpdsRelayError("OPDS access is disabled on this Papyrus server.", 503)

    if not _slots.acquire(blocking=False):
        raise OpdsRelayError("The catalog relay is busy. Please retry shortly.", 503, retryable=True)

    connection: _PinnedConnection | None = None
    response: http.client.HTTPResponse | None = None
    try:
        catalog_origin = _origin(_parse_url(payload.catalog_url))
        current = payload.url
        deadline = time.monotonic() + _TOTAL_TIMEOUT
        for _ in range(6):
            if time.monotonic() >= deadline:
                raise TimeoutError

            parts = _parse_url(current)
            address = _resolve_public(parts, deadline)
            connection = _PinnedConnection(parts, address)
            headers = {"Accept-Encoding": "identity", "User-Agent": "Papyrus-OPDS/1.0", "Connection": "close"}
            if payload.credentials is not None and _origin(parts) == catalog_origin:
                credentials = payload.credentials
                token = f"{credentials.username}:{credentials.password.get_secret_value()}".encode()
                headers["Authorization"] = "Basic " + base64.b64encode(token).decode("ascii")

            path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
            header_timer = threading.Timer(max(0, min(_TIMEOUT, deadline - time.monotonic())), connection.abort)
            header_timer.daemon = True
            header_timer.start()
            try:
                connection.request("GET", path, headers=headers)
                response = connection.getresponse()
            except (OSError, http.client.HTTPException) as exc:
                if connection._aborted:
                    raise TimeoutError from exc

                raise
            finally:
                header_timer.cancel()

            if connection._aborted or time.monotonic() >= deadline:
                raise TimeoutError

            if response.status in _REDIRECTS:
                location = response.getheader("Location")
                response.close()
                connection.close()
                if not location:
                    raise OpdsRelayError("The catalog returned an invalid redirect.", 502)

                next_url = urljoin(urlunsplit(parts), location)
                if parts.scheme == "https" and _parse_url(next_url).scheme != "https":
                    raise OpdsRelayError("The catalog redirected to an insecure connection.")

                current = next_url
                continue

            if response.status == 401:
                raise OpdsRelayError(
                    "Check the catalog username and password, then retry with updated credentials.", 401
                )

            if response.status == 403:
                raise OpdsRelayError("This catalog denied access. Check your catalog account permissions.", 403)

            if response.status != 200:
                status = response.status if 400 <= response.status < 600 else 502
                raise OpdsRelayError(f"The catalog returned HTTP {response.status}. Please retry later.", status)

            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise OpdsRelayError("The catalog returned an unsupported content encoding.", 502)

            raw_length = response.getheader("Content-Length")
            if raw_length is not None and not raw_length.isdecimal():
                raise OpdsRelayError("The catalog returned an invalid content length.", 502)

            length = int(raw_length) if raw_length is not None else None
            if length is not None and length > payload.max_bytes:
                raise OpdsRelayError("This resource is too large to load.", 413)

            content_type = response.getheader("Content-Type", "application/octet-stream")
            if "\r" in content_type or "\n" in content_type:
                raise OpdsRelayError("The catalog returned an invalid content type.", 502)

            resource = RelayResource(
                urlunsplit(parts), content_type, length, response, connection, payload.max_bytes, deadline
            )
            resource.timer = threading.Timer(max(0, deadline - time.monotonic()), resource.close)
            resource.timer.daemon = True
            resource.timer.start()
            return resource

        raise OpdsRelayError("The catalog redirected too many times. Check its URL.", 502)
    except BaseException as exc:
        if response is not None:
            response.close()

        if connection is not None:
            connection.close()

        _slots.release()
        if isinstance(exc, TimeoutError):
            raise OpdsRelayError("The catalog did not respond in time. Please retry.", 504) from exc

        if isinstance(exc, (OSError, http.client.HTTPException)):
            raise OpdsRelayError("The Papyrus server could not connect to this catalog. Please retry.", 502) from exc

        raise
