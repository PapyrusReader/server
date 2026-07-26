"""Adapters for indexer protocols and self-hosted download clients."""

from __future__ import annotations

import asyncio
import base64
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import PurePosixPath
from typing import Any, Self
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen
from uuid import UUID, uuid4
from xml.etree import ElementTree

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.config import get_settings
from papyrus.core.security import decrypt_secret_payload, encrypt_secret_payload
from papyrus.models.acquisition import AcquisitionEndpoint, AcquisitionJob, AcquisitionRule
from papyrus.models.sync import SyncBook
from papyrus.schemas.acquisition import AcquisitionEndpointTest
from papyrus.services.media import BOOK_EXTENSIONS

RELEASE_TOKEN_LIFETIME = timedelta(minutes=5)
SUBMISSION_LEASE_LIFETIME = timedelta(minutes=5)


@dataclass(frozen=True, slots=True)
class ReleaseCandidate:
    title: str
    download_url: str
    protocol: str
    indexer: str
    size_bytes: int | None
    seeders: int | None
    publish_date: datetime | None
    format_hints: list[str]


@dataclass(frozen=True, slots=True)
class ReleaseTokenPayload:
    endpoint_id: UUID
    owner_user_id: UUID
    title: str
    download_url: str
    protocol: str
    indexer: str
    size_bytes: int | None
    seeders: int | None
    publish_date: datetime | None
    format_hints: list[str]


@dataclass(frozen=True, slots=True)
class BatchSubmissionResult:
    index: int
    job: AcquisitionJob | None
    error: str | None


@dataclass(frozen=True, slots=True)
class QbittorrentTorrent:
    hash: str
    state: str
    progress_basis_points: int
    downloaded_bytes: int
    total_bytes: int
    download_speed_bytes_per_second: int
    eta_seconds: int | None


@dataclass(frozen=True, slots=True)
class QbittorrentFile:
    index: int
    name: str
    size_bytes: int
    progress_basis_points: int
    priority: int


@dataclass(frozen=True, slots=True)
class JobFileCandidate:
    index: int
    name: str
    size_bytes: int
    progress_basis_points: int
    priority: int
    supported: bool


@dataclass(slots=True)
class QbittorrentClient:
    endpoint: AcquisitionEndpoint
    cookie: str

    @classmethod
    async def connect(cls, endpoint: AcquisitionEndpoint) -> Self:
        if endpoint.kind != "qbittorrent":
            raise HTTPException(status_code=422, detail="Endpoint is not qBittorrent")

        credentials = _credentials(endpoint)
        login = urlencode(
            {
                "username": credentials.get("username", ""),
                "password": credentials.get("password", ""),
            }
        ).encode()
        response_status, headers, response_payload = await _request(
            _url(endpoint, "api/v2/auth/login"),
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=login,
        )

        if not _qbittorrent_login_succeeded(response_status, response_payload):
            raise HTTPException(status_code=502, detail="qBittorrent authentication failed")

        return cls(
            endpoint=endpoint,
            cookie=_header_value(headers, "Set-Cookie").split(";", 1)[0],
        )

    async def find_torrent(
        self,
        *,
        tag: str,
        torrent_hash: str | None = None,
    ) -> QbittorrentTorrent:
        payload: list[dict[str, object]] = []

        if torrent_hash is not None:
            payload = await self._get_json_array(
                "api/v2/torrents/info",
                {"hashes": torrent_hash},
            )

        if not payload:
            payload = await self._get_json_array("api/v2/torrents/info", {"tag": tag})

        if not payload:
            raise HTTPException(status_code=404, detail="qBittorrent torrent not found")

        if len(payload) != 1:
            raise HTTPException(status_code=409, detail="qBittorrent tag matched multiple torrents")

        item = payload[0]
        return QbittorrentTorrent(
            hash=_required_string(item, "hash", "qBittorrent torrent"),
            state=_required_string(item, "state", "qBittorrent torrent"),
            progress_basis_points=_progress_basis_points(item.get("progress")),
            downloaded_bytes=_required_int(item, "downloaded", "qBittorrent torrent"),
            total_bytes=_required_int(item, "total_size", "qBittorrent torrent"),
            download_speed_bytes_per_second=_required_int(item, "dlspeed", "qBittorrent torrent"),
            eta_seconds=_optional_int(item.get("eta")),
        )

    async def files(self, torrent_hash: str) -> list[QbittorrentFile]:
        payload = await self._get_json_array("api/v2/torrents/files", {"hash": torrent_hash})
        return [
            QbittorrentFile(
                index=_required_int(item, "index", "qBittorrent file"),
                name=_required_string(item, "name", "qBittorrent file"),
                size_bytes=_required_int(item, "size", "qBittorrent file"),
                progress_basis_points=_progress_basis_points(item.get("progress")),
                priority=_required_int(item, "priority", "qBittorrent file"),
            )
            for item in payload
        ]

    async def select_file(self, torrent_hash: str, *, selected_index: int, file_indices: list[int]) -> None:
        if selected_index not in file_indices:
            raise HTTPException(status_code=422, detail="Selected qBittorrent file was not found")

        other_indices = [index for index in file_indices if index != selected_index]

        await self._post_form("api/v2/torrents/pause", {"hashes": torrent_hash})

        if other_indices:
            await self._post_form(
                "api/v2/torrents/filePrio",
                {
                    "hash": torrent_hash,
                    "id": "|".join(str(index) for index in other_indices),
                    "priority": "0",
                },
            )

        await self._post_form(
            "api/v2/torrents/filePrio",
            {
                "hash": torrent_hash,
                "id": str(selected_index),
                "priority": "1",
            },
        )
        await self._post_form("api/v2/torrents/resume", {"hashes": torrent_hash})

    async def pause(self, torrent_hash: str) -> None:
        await self._post_form("api/v2/torrents/pause", {"hashes": torrent_hash})

    async def delete_torrent(self, torrent_hash: str) -> None:
        await self._post_form(
            "api/v2/torrents/delete",
            {
                "hashes": torrent_hash,
                "deleteFiles": "true",
            },
        )

    async def _get_json_array(self, path: str, params: dict[str, str]) -> list[dict[str, object]]:
        response_status, _, payload = await _request(
            _url(self.endpoint, f"{path}?{urlencode(params)}"),
            headers={"Cookie": self.cookie},
        )

        if response_status >= 400:
            raise HTTPException(status_code=502, detail="qBittorrent request failed")

        return _json_array(payload, "qBittorrent")

    async def _post_form(self, path: str, values: dict[str, str]) -> None:
        response_status, _, _ = await _request(
            _url(self.endpoint, path),
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": self.cookie,
            },
            body=urlencode(values).encode(),
        )

        if response_status >= 400:
            raise HTTPException(status_code=502, detail="qBittorrent request failed")


def _required_string(item: dict[str, object], key: str, subject: str) -> str:
    value = item.get(key)

    if not isinstance(value, str) or not value:
        raise HTTPException(status_code=502, detail=f"{subject} returned invalid data")

    return value


def _required_int(item: dict[str, object], key: str, subject: str) -> int:
    value = item.get(key)

    if not isinstance(value, int) or isinstance(value, bool):
        raise HTTPException(status_code=502, detail=f"{subject} returned invalid data")

    return value


def _progress_basis_points(value: object) -> int:
    if not isinstance(value, int | float) or isinstance(value, bool):
        raise HTTPException(status_code=502, detail="qBittorrent returned invalid progress")

    return min(10_000, max(0, round(float(value) * 10_000)))


def create_release_token(
    release: ReleaseCandidate,
    endpoint: AcquisitionEndpoint,
    *,
    now: datetime | None = None,
) -> str:
    issued_at = now or datetime.now(UTC)

    if endpoint.endpoint_id is None:
        raise ValueError("Release tokens require a persisted endpoint")

    return encrypt_secret_payload(
        {
            "endpoint_id": str(endpoint.endpoint_id),
            "owner_user_id": str(endpoint.owner_user_id),
            "title": release.title,
            "download_url": release.download_url,
            "protocol": release.protocol,
            "indexer": release.indexer,
            "size_bytes": "" if release.size_bytes is None else str(release.size_bytes),
            "seeders": "" if release.seeders is None else str(release.seeders),
            "publish_date": release.publish_date.isoformat() if release.publish_date is not None else "",
            "format_hints": json.dumps(release.format_hints),
            "expires_at": str(int((issued_at + RELEASE_TOKEN_LIFETIME).timestamp())),
        }
    )


def decode_release_token(
    token: str,
    owner_user_id: UUID,
    *,
    now: datetime | None = None,
) -> ReleaseTokenPayload:
    try:
        payload = decrypt_secret_payload(token)
        expires_at = int(payload["expires_at"])
        token_owner_user_id = UUID(payload["owner_user_id"])
        endpoint_id = UUID(payload["endpoint_id"])
        format_hints = json.loads(payload["format_hints"])
        size_bytes = int(payload["size_bytes"]) if payload["size_bytes"] else None
        seeders = int(payload["seeders"]) if payload["seeders"] else None
        publish_date = datetime.fromisoformat(payload["publish_date"]) if payload["publish_date"] else None

        if (
            token_owner_user_id != owner_user_id
            or expires_at <= int((now or datetime.now(UTC)).timestamp())
            or not isinstance(format_hints, list)
            or not all(isinstance(value, str) for value in format_hints)
        ):
            raise ValueError

        return ReleaseTokenPayload(
            endpoint_id=endpoint_id,
            owner_user_id=token_owner_user_id,
            title=payload["title"],
            download_url=payload["download_url"],
            protocol=payload["protocol"],
            indexer=payload["indexer"],
            size_bytes=size_bytes,
            seeders=seeders,
            publish_date=publish_date,
            format_hints=format_hints,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Release token is invalid or expired") from exc


def _url(endpoint: AcquisitionEndpoint, path: str) -> str:
    return urljoin(endpoint.base_url.rstrip("/") + "/", path.lstrip("/"))


async def _request(
    url: str, *, method: str = "GET", headers: dict[str, str] | None = None, body: bytes | None = None
) -> tuple[int, dict[str, str], bytes]:
    """Perform a bounded blocking HTTP request off the event loop."""

    def send() -> tuple[int, dict[str, str], bytes]:
        request = Request(url, data=body, headers=headers or {}, method=method)
        try:
            with urlopen(request, timeout=15) as response:  # noqa: S310 - user-owned self-hosted integrations
                return response.status, dict(response.headers.items()), response.read(5_000_000)
        except HTTPError as exc:
            return exc.code, dict(exc.headers.items()), exc.read(1_000_000)
        except URLError as exc:
            raise HTTPException(status_code=502, detail=f"Integration request failed: {exc.reason}") from exc

    return await asyncio.to_thread(send)


def _credentials(endpoint: AcquisitionEndpoint) -> dict[str, str]:
    credentials = endpoint.credentials or {}
    encrypted = credentials.get("encrypted")
    if encrypted is None:
        return credentials
    try:
        return decrypt_secret_payload(encrypted)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="Stored integration credentials are invalid") from exc


def _json_value(payload: bytes, integration: str) -> object:
    try:
        return json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail=f"{integration} returned invalid JSON") from exc


def _json_object(payload: bytes, integration: str) -> dict[str, object]:
    value = _json_value(payload, integration)
    if not isinstance(value, dict):
        raise HTTPException(status_code=502, detail=f"{integration} returned an invalid response")
    return value


def _json_array(payload: bytes, integration: str) -> list[dict[str, object]]:
    value = _json_value(payload, integration)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise HTTPException(status_code=502, detail=f"{integration} returned an invalid response")
    return value


def _require_deluge_result(payload: bytes) -> object:
    response = _json_object(payload, "Deluge")
    result = response.get("result")
    if response.get("error") is not None or result is None or result is False:
        raise HTTPException(status_code=502, detail="Deluge rejected the request")
    return result


async def search_endpoint(endpoint: AcquisitionEndpoint, query: str) -> list[ReleaseCandidate]:
    """Search Prowlarr or a Torznab-compatible torrent indexer."""
    credentials = _credentials(endpoint)
    if endpoint.kind == "prowlarr":
        request_url = _url(endpoint, f"api/v1/search?{urlencode({'query': query})}")
        response_status, _, payload = await _request(request_url, headers={"X-Api-Key": credentials.get("api_key", "")})
        if response_status >= 400:
            raise HTTPException(status_code=502, detail="Prowlarr search failed")
        data = _json_array(payload, "Prowlarr")
        releases: list[ReleaseCandidate] = []

        for item in data:
            download_url = item.get("downloadUrl") or item.get("magnetUrl") or item.get("guid")
            protocol = item.get("protocol", "torrent")

            if not isinstance(download_url, str) or not download_url or protocol != "torrent":
                continue

            title_value = item.get("title")
            indexer_value = item.get("indexer")
            title = title_value if isinstance(title_value, str) and title_value else "Untitled"
            indexer = indexer_value if isinstance(indexer_value, str) and indexer_value else "Prowlarr"

            releases.append(
                ReleaseCandidate(
                    title=title,
                    download_url=download_url,
                    protocol="torrent",
                    indexer=indexer,
                    size_bytes=_optional_int(item.get("size")),
                    seeders=_optional_int(item.get("seeders")),
                    publish_date=None,
                    format_hints=_format_hints(title, download_url),
                )
            )

        return releases

    params = urlencode({"t": "search", "q": query, "apikey": credentials.get("api_key", "")})
    response_status, _, payload = await _request(_url(endpoint, f"api?{params}"))
    if response_status >= 400:
        raise HTTPException(status_code=502, detail=f"{endpoint.kind.title()} search failed")
    return _parse_torznab(payload, endpoint.name)


def _parse_torznab(payload: bytes, indexer: str) -> list[ReleaseCandidate]:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise HTTPException(status_code=502, detail="Indexer returned invalid XML") from exc
    releases: list[ReleaseCandidate] = []
    for item in root.findall(".//item"):
        enclosure = item.find("enclosure")
        link = (enclosure.get("url") if enclosure is not None else None) or item.findtext("link")
        if not link:
            continue
        attrs = {child.attrib.get("name"): child.attrib.get("value") for child in item if child.tag.endswith("attr")}
        size = attrs.get("size")
        seeders = attrs.get("seeders")
        releases.append(
            ReleaseCandidate(
                title=item.findtext("title") or "Untitled",
                download_url=link,
                protocol="torrent",
                indexer=indexer,
                size_bytes=int(size) if size is not None and size.isdigit() else None,
                seeders=int(seeders) if seeders is not None and seeders.isdigit() else None,
                publish_date=None,
                format_hints=_format_hints(item.findtext("title") or "", link),
            )
        )
    return releases


def _format_hints(title: str, download_url: str) -> list[str]:
    searchable = f"{title} {download_url}".lower()
    return [
        extension
        for extension in ("epub", "pdf", "mobi", "azw3", "txt", "cbr", "cbz")
        if re.search(rf"(?<![a-z0-9]){extension}(?![a-z0-9])", searchable)
    ]


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


async def submit_to_client(
    endpoint: AcquisitionEndpoint,
    download_url: str,
    category: str | None,
    save_path: str | None,
    *,
    tags: list[str] | None = None,
) -> str | None:
    """Submit a magnet or torrent URL to a supported BitTorrent client."""
    if endpoint.kind == "qbittorrent":
        return await _submit_qbittorrent(endpoint, download_url, category, save_path, tags=tags)
    if endpoint.kind == "transmission":
        return await _submit_transmission(endpoint, download_url, save_path)
    if endpoint.kind == "deluge":
        return await _submit_deluge(endpoint, download_url, save_path)
    raise HTTPException(status_code=422, detail="Endpoint is not a download client")


async def dispatch_arr_command(endpoint: AcquisitionEndpoint, command: str, ids: list[int]) -> str | None:
    """Start an acquisition/search command in a Servarr application.

    The Arr apps own their library and release-grab decisions, so they must be
    driven through their command API rather than handed an arbitrary magnet.
    """
    if endpoint.kind not in {"readarr", "sonarr", "radarr", "lidarr", "whisparr"}:
        raise HTTPException(status_code=422, detail="Endpoint is not a Servarr application")

    allowed_commands = {
        "readarr": {"AuthorSearch", "BookSearch"},
        "sonarr": {"SeriesSearch", "EpisodeSearch", "MissingEpisodeSearch"},
        "radarr": {"MoviesSearch", "MissingMoviesSearch"},
        "lidarr": {"ArtistSearch", "AlbumSearch", "MissingAlbumSearch"},
        "whisparr": {"SeriesSearch", "EpisodeSearch", "MissingEpisodeSearch"},
    }
    if command not in allowed_commands[endpoint.kind]:
        raise HTTPException(status_code=422, detail="Command is not supported by this Servarr application")

    id_field = {
        "AuthorSearch": "authorIds",
        "BookSearch": "bookIds",
        "SeriesSearch": "seriesId",
        "EpisodeSearch": "episodeIds",
        "MissingEpisodeSearch": "seriesId",
        "MoviesSearch": "movieIds",
        "MissingMoviesSearch": "movieIds",
        "ArtistSearch": "artistIds",
        "AlbumSearch": "albumIds",
        "MissingAlbumSearch": "artistIds",
    }[command]
    payload: dict[str, object] = {"name": command}
    if ids:
        payload[id_field] = ids[0] if id_field in {"seriesId"} else ids

    response_status, _, response_payload = await _request(
        _url(endpoint, "api/v3/command"),
        method="POST",
        headers={"Content-Type": "application/json", "X-Api-Key": _credentials(endpoint).get("api_key", "")},
        body=json.dumps(payload).encode(),
    )
    if response_status >= 400:
        raise HTTPException(status_code=502, detail=f"{endpoint.kind.title()} command failed")
    response = _json_object(response_payload, endpoint.kind.title())
    return str(response.get("id")) if response.get("id") is not None else None


async def _submit_qbittorrent(
    endpoint: AcquisitionEndpoint,
    download_url: str,
    category: str | None,
    save_path: str | None,
    *,
    tags: list[str] | None = None,
) -> str | None:
    credentials = _credentials(endpoint)
    login = urlencode(
        {"username": credentials.get("username", ""), "password": credentials.get("password", "")}
    ).encode()
    response_status, headers, response_payload = await _request(
        _url(endpoint, "api/v2/auth/login"),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=login,
    )
    if not _qbittorrent_login_succeeded(response_status, response_payload):
        raise HTTPException(status_code=502, detail="qBittorrent authentication failed")
    payload = {"urls": download_url}
    if category:
        payload["category"] = category
    if save_path:
        payload["savepath"] = save_path
    if tags:
        payload["tags"] = ",".join(tags)
    response_status, _, _ = await _request(
        _url(endpoint, "api/v2/torrents/add"),
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": _header_value(headers, "Set-Cookie").split(";", 1)[0],
        },
        body=urlencode(payload).encode(),
    )
    if response_status >= 400:
        raise HTTPException(status_code=502, detail="qBittorrent rejected the release")
    return None


def _header_value(headers: dict[str, str], name: str) -> str:
    normalized_name = name.casefold()
    return next((value for key, value in headers.items() if key.casefold() == normalized_name), "")


def _qbittorrent_login_succeeded(response_status: int, response_payload: bytes) -> bool:
    return response_status < 400 and (response_status == 204 or response_payload.strip() == b"Ok.")


async def _submit_transmission(endpoint: AcquisitionEndpoint, download_url: str, save_path: str | None) -> str | None:
    credentials = _credentials(endpoint)
    arguments: dict[str, str] = {"filename": download_url}
    if save_path:
        arguments["download-dir"] = save_path
    body = json.dumps({"method": "torrent-add", "arguments": arguments}).encode()
    headers = {"Content-Type": "application/json"}
    if credentials.get("username"):
        token = base64.b64encode(f"{credentials['username']}:{credentials.get('password', '')}".encode()).decode()
        headers["Authorization"] = f"Basic {token}"
    response_status, response_headers, payload = await _request(
        _url(endpoint, "transmission/rpc"), method="POST", headers=headers, body=body
    )
    if response_status == 409:
        headers["X-Transmission-Session-Id"] = response_headers.get("X-Transmission-Session-Id", "")
        response_status, _, payload = await _request(
            _url(endpoint, "transmission/rpc"), method="POST", headers=headers, body=body
        )
    if response_status >= 400:
        raise HTTPException(status_code=502, detail="Transmission rejected the release")

    response = _json_object(payload, "Transmission")
    if response.get("result") != "success":
        raise HTTPException(status_code=502, detail="Transmission rejected the release")

    response_arguments = response.get("arguments")
    if not isinstance(response_arguments, dict):
        raise HTTPException(status_code=502, detail="Transmission returned an invalid response")

    torrent = response_arguments.get("torrent-added") or response_arguments.get("torrent-duplicate")
    if not isinstance(torrent, dict):
        return None

    reference = torrent.get("hashString")
    return str(reference) if reference is not None else None


async def _submit_deluge(endpoint: AcquisitionEndpoint, download_url: str, save_path: str | None) -> str | None:
    credentials = _credentials(endpoint)
    headers = {"Content-Type": "application/json"}
    login = json.dumps({"method": "auth.login", "params": [credentials.get("password", "")], "id": 1}).encode()
    response_status, response_headers, login_payload = await _request(
        _url(endpoint, "json"), method="POST", headers=headers, body=login
    )
    if response_status >= 400:
        raise HTTPException(status_code=502, detail="Deluge authentication failed")

    try:
        _require_deluge_result(login_payload)
    except HTTPException as exc:
        raise HTTPException(status_code=502, detail="Deluge authentication failed") from exc

    options = {"download_location": save_path} if save_path else {}
    method = "core.add_torrent_magnet" if download_url.startswith("magnet:") else "core.add_torrent_url"
    body = json.dumps({"method": method, "params": [download_url, options], "id": 2}).encode()
    headers["Cookie"] = response_headers.get("Set-Cookie", "").split(";", 1)[0]
    response_status, _, payload = await _request(_url(endpoint, "json"), method="POST", headers=headers, body=body)
    if response_status >= 400:
        raise HTTPException(status_code=502, detail="Deluge rejected the release")

    try:
        result = _require_deluge_result(payload)
    except HTTPException as exc:
        raise HTTPException(status_code=502, detail="Deluge rejected the release") from exc

    return str(result)


async def owned_endpoint(session: AsyncSession, owner_user_id: Any, endpoint_id: Any) -> AcquisitionEndpoint:
    result = await session.execute(
        select(AcquisitionEndpoint).where(
            AcquisitionEndpoint.endpoint_id == endpoint_id, AcquisitionEndpoint.owner_user_id == owner_user_id
        )
    )
    endpoint = result.scalar_one_or_none()
    if endpoint is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Acquisition endpoint not found")
    return endpoint


async def owned_job(
    session: AsyncSession,
    owner_user_id: UUID,
    job_id: UUID,
    *,
    for_update: bool = False,
) -> AcquisitionJob:
    statement = select(AcquisitionJob).where(
        AcquisitionJob.job_id == job_id,
        AcquisitionJob.owner_user_id == owner_user_id,
    )

    if for_update:
        statement = statement.with_for_update()

    result = await session.execute(statement.execution_options(populate_existing=for_update))
    job = result.scalar_one_or_none()

    if job is None:
        raise HTTPException(status_code=404, detail="Acquisition job not found")

    return job


async def paginated_jobs(
    session: AsyncSession,
    owner_user_id: UUID,
    *,
    limit: int,
    offset: int,
) -> tuple[list[AcquisitionJob], int]:
    total = await session.scalar(
        select(func.count()).select_from(AcquisitionJob).where(AcquisitionJob.owner_user_id == owner_user_id)
    )
    result = await session.execute(
        select(AcquisitionJob)
        .where(AcquisitionJob.owner_user_id == owner_user_id)
        .order_by(AcquisitionJob.created_at.desc(), AcquisitionJob.job_id.desc())
        .limit(limit)
        .offset(offset)
    )

    return list(result.scalars()), total or 0


async def job_file_candidates(
    session: AsyncSession,
    owner_user_id: UUID,
    job_id: UUID,
) -> list[JobFileCandidate]:
    job = await owned_job(session, owner_user_id, job_id)

    if job.status != "needs_file_selection":
        raise HTTPException(status_code=409, detail="Acquisition job does not need file selection")

    if job.endpoint_id is None or job.client_hash is None:
        raise HTTPException(status_code=409, detail="Acquisition job is missing its qBittorrent reference")

    endpoint = await owned_endpoint(session, owner_user_id, job.endpoint_id)
    client = await QbittorrentClient.connect(endpoint)
    files = await client.files(job.client_hash)

    return [
        JobFileCandidate(
            index=file.index,
            name=file.name,
            size_bytes=file.size_bytes,
            progress_basis_points=file.progress_basis_points,
            priority=file.priority,
            supported=_supported_book_file(file.name),
        )
        for file in files
    ]


def _supported_book_file(filename: str) -> bool:
    normalized = filename.replace("\\", "/")
    extension = PurePosixPath(normalized).suffix.lower().lstrip(".")
    return extension in BOOK_EXTENSIONS


async def select_job_file(
    session: AsyncSession,
    owner_user_id: UUID,
    job_id: UUID,
    file_index: int,
) -> AcquisitionJob:
    job = await owned_job(
        session,
        owner_user_id,
        job_id,
        for_update=True,
    )

    if job.status != "needs_file_selection":
        raise HTTPException(status_code=409, detail="Acquisition job does not need file selection")

    if job.endpoint_id is None or job.client_hash is None:
        raise HTTPException(status_code=409, detail="Acquisition job is missing its qBittorrent reference")

    endpoint = await owned_endpoint(session, owner_user_id, job.endpoint_id)
    client = await QbittorrentClient.connect(endpoint)
    files = await client.files(job.client_hash)
    selected = next((file for file in files if file.index == file_index), None)

    if selected is None:
        raise HTTPException(status_code=404, detail="qBittorrent file not found")

    if not _supported_book_file(selected.name):
        raise HTTPException(status_code=422, detail="Selected file is not a supported book")

    await client.select_file(
        job.client_hash,
        selected_index=selected.index,
        file_indices=[file.index for file in files],
    )

    job.selected_file_path = selected.name
    job.status = "downloading"
    job.error = None
    job.next_poll_at = datetime.now(UTC)
    job.updated_at = datetime.now(UTC)

    await session.commit()
    await session.refresh(job)
    return job


async def cancel_job(
    session: AsyncSession,
    owner_user_id: UUID,
    job_id: UUID,
) -> AcquisitionJob:
    job = await owned_job(
        session,
        owner_user_id,
        job_id,
        for_update=True,
    )

    if job.status == "cancelled":
        return job

    if job.status not in {"queued", "submitted", "downloading", "needs_file_selection"}:
        raise HTTPException(status_code=409, detail="Acquisition job cannot be cancelled")

    if job.endpoint_id is None:
        raise HTTPException(status_code=409, detail="Acquisition job is missing its qBittorrent endpoint")

    endpoint = await owned_endpoint(session, owner_user_id, job.endpoint_id)
    client = await QbittorrentClient.connect(endpoint)
    torrent_hash = job.client_hash

    if torrent_hash is None:
        try:
            torrent_hash = (await client.find_torrent(tag=f"papyrus:{job.job_id}")).hash
        except HTTPException as exc:
            if exc.status_code != 404:
                raise

    if torrent_hash is not None:
        await client.delete_torrent(torrent_hash)

    now = datetime.now(UTC)
    job.status = "cancelled"
    job.cancelled_at = now
    job.updated_at = now
    job.next_poll_at = None
    job.error = None

    await session.commit()
    await session.refresh(job)
    return job


async def delete_terminal_job(
    session: AsyncSession,
    owner_user_id: UUID,
    job_id: UUID,
) -> None:
    job = await owned_job(session, owner_user_id, job_id)

    if job.status not in {"failed", "cancelled"}:
        raise HTTPException(status_code=409, detail="Only failed or cancelled jobs can be removed")

    book = None

    if job.book_id is not None:
        result = await session.execute(
            select(SyncBook)
            .where(
                SyncBook.book_id == job.book_id,
                SyncBook.owner_user_id == owner_user_id,
            )
            .with_for_update()
        )
        book = result.scalar_one_or_none()

        if book is not None and book.file_media_id is not None:
            raise HTTPException(status_code=409, detail="Imported books must be removed from the library")

    await session.delete(job)
    await session.flush()

    if book is not None:
        await session.delete(book)

    await session.commit()


async def retry_job_import(
    session: AsyncSession,
    owner_user_id: UUID,
    job_id: UUID,
) -> AcquisitionJob:
    job = await owned_job(
        session,
        owner_user_id,
        job_id,
        for_update=True,
    )

    if job.status != "failed":
        raise HTTPException(status_code=409, detail="Only failed jobs can retry import")

    if job.endpoint_id is None:
        raise HTTPException(status_code=409, detail="Acquisition job cannot resume without qBittorrent")

    if job.submitted_at is None:
        raise HTTPException(status_code=409, detail="Acquisition job was not submitted to qBittorrent")

    now = datetime.now(UTC)
    job.status = "downloading"
    job.retry_count += 1
    job.error = None
    job.next_poll_at = now
    job.updated_at = now

    await session.commit()
    await session.refresh(job)
    return job


async def submit_release_batch(
    session: AsyncSession,
    owner_user_id: UUID,
    endpoint_id: UUID,
    release_tokens: list[str],
) -> list[BatchSubmissionResult]:
    endpoint = await owned_endpoint(session, owner_user_id, endpoint_id)

    if endpoint.kind != "qbittorrent":
        raise HTTPException(status_code=422, detail="Managed downloads require qBittorrent")

    if not endpoint.enabled:
        raise HTTPException(status_code=409, detail="Download client is disabled")

    if endpoint.download_root is None:
        raise HTTPException(status_code=409, detail="qBittorrent download root is not configured")

    if get_settings().acquisition_import_root is None:
        raise HTTPException(status_code=409, detail="Acquisition import root is not configured")

    results: list[BatchSubmissionResult] = []

    for index, token in enumerate(release_tokens):
        try:
            release = decode_release_token(token, owner_user_id)
        except HTTPException as exc:
            results.append(BatchSubmissionResult(index=index, job=None, error=str(exc.detail)))
            continue

        if release.protocol != "torrent" or not release.download_url.startswith(("magnet:", "http://", "https://")):
            results.append(BatchSubmissionResult(index=index, job=None, error="Release token is invalid or expired"))
            continue

        book_id = uuid4()
        job_id = uuid4()
        book = SyncBook(
            book_id=book_id,
            owner_user_id=owner_user_id,
            title=release.title,
            custom_metadata={
                "acquisition": {
                    "job_id": str(job_id),
                    "provisional": True,
                }
            },
        )
        job = AcquisitionJob(
            job_id=job_id,
            owner_user_id=owner_user_id,
            endpoint_id=endpoint.endpoint_id,
            book_id=book_id,
            title=release.title,
            download_url=None,
            status="queued",
            next_poll_at=datetime.now(UTC),
            lease_owner=f"submission:{job_id}",
            lease_until=datetime.now(UTC) + SUBMISSION_LEASE_LIFETIME,
        )

        session.add(book)
        await session.flush()

        session.add(job)
        await session.flush()

        await session.commit()
        await session.refresh(job)

        try:
            job.client_reference = await submit_to_client(
                endpoint,
                release.download_url,
                "papyrus",
                _managed_download_path(endpoint.download_root, owner_user_id, job_id),
                tags=[f"papyrus:{job_id}"],
            )
            job.status = "submitted"
            job.submitted_at = datetime.now(UTC)
            job.next_poll_at = datetime.now(UTC)
        except HTTPException as exc:
            job.status = "failed"
            job.error = str(exc.detail)
            job.next_poll_at = None

        job.lease_owner = None
        job.lease_until = None

        await session.commit()
        await session.refresh(job)
        results.append(BatchSubmissionResult(index=index, job=job, error=None))

    return results


def _managed_download_path(download_root: str, owner_user_id: UUID, job_id: UUID) -> str:
    normalized_root = download_root.rstrip("/\\")
    separator = "\\" if "\\" in normalized_root and "/" not in normalized_root else "/"
    return separator.join((normalized_root, str(owner_user_id), str(job_id)))


async def delete_acquisition_endpoint(session: AsyncSession, owner_user_id: Any, endpoint_id: Any) -> None:
    endpoint = await owned_endpoint(session, owner_user_id, endpoint_id)
    active_job_id = await session.scalar(
        select(AcquisitionJob.job_id)
        .where(
            AcquisitionJob.owner_user_id == owner_user_id,
            AcquisitionJob.endpoint_id == endpoint_id,
            AcquisitionJob.status.not_in({"completed", "failed", "cancelled"}),
        )
        .limit(1)
    )

    if active_job_id is not None:
        raise HTTPException(status_code=409, detail="Endpoint has active acquisition jobs")

    result = await session.execute(
        select(AcquisitionRule).where(AcquisitionRule.owner_user_id == owner_user_id).with_for_update()
    )

    for rule in result.scalars():
        endpoint_ids = rule.endpoint_ids or []
        remaining_endpoint_ids = [value for value in endpoint_ids if value != str(endpoint_id)]
        indexer_deleted = remaining_endpoint_ids != endpoint_ids
        download_client_deleted = rule.download_client_id == endpoint_id

        if indexer_deleted:
            rule.endpoint_ids = remaining_endpoint_ids

        if download_client_deleted:
            rule.download_client_id = None

        if download_client_deleted or (indexer_deleted and not remaining_endpoint_ids):
            rule.enabled = False

    await session.flush()

    await session.delete(endpoint)
    await session.commit()


async def build_test_endpoint(
    session: AsyncSession,
    owner_user_id: Any,
    request: AcquisitionEndpointTest,
) -> AcquisitionEndpoint:
    stored_endpoint = None
    stored_credentials: dict[str, str] = {}

    if request.endpoint_id is not None:
        stored_endpoint = await owned_endpoint(session, owner_user_id, request.endpoint_id)
        stored_credentials = _credentials(stored_endpoint)

    credentials = dict(stored_credentials)
    for field in ("api_key", "username", "password"):
        value = getattr(request, field)
        if value is not None:
            credentials[field] = value.get_secret_value()

    if request.kind is not None:
        kind = request.kind.value
    elif stored_endpoint is not None:
        kind = stored_endpoint.kind
    else:
        raise HTTPException(status_code=422, detail="Endpoint kind is required")

    if request.base_url is not None:
        base_url = str(request.base_url)
    elif stored_endpoint is not None:
        base_url = stored_endpoint.base_url
    else:
        raise HTTPException(status_code=422, detail="Endpoint URL is required")

    encrypted_credentials = {"encrypted": encrypt_secret_payload(credentials)} if credentials else None

    return AcquisitionEndpoint(
        owner_user_id=owner_user_id,
        name=stored_endpoint.name if stored_endpoint is not None else "Connection test",
        kind=kind,
        base_url=base_url,
        credentials=encrypted_credentials,
        settings=stored_endpoint.settings if stored_endpoint is not None else None,
    )


async def test_endpoint_connection(endpoint: AcquisitionEndpoint) -> None:
    credentials = _credentials(endpoint)

    if endpoint.kind == "prowlarr":
        response_status, _, payload = await _request(
            _url(endpoint, "api/v1/system/status"),
            headers={"X-Api-Key": credentials.get("api_key", "")},
        )
        if response_status >= 400:
            raise HTTPException(status_code=502, detail="Prowlarr connection test failed")

        _json_object(payload, "Prowlarr")
        return

    if endpoint.kind == "torznab":
        params = urlencode({"t": "caps", "apikey": credentials.get("api_key", "")})
        response_status, _, payload = await _request(_url(endpoint, f"api?{params}"))
        if response_status >= 400:
            raise HTTPException(status_code=502, detail="Torznab connection test failed")

        try:
            ElementTree.fromstring(payload)
        except ElementTree.ParseError as exc:
            raise HTTPException(status_code=502, detail="Torznab returned invalid XML") from exc
        return

    if endpoint.kind in {"readarr", "sonarr", "radarr", "lidarr", "whisparr"}:
        response_status, _, payload = await _request(
            _url(endpoint, "api/v3/system/status"),
            headers={"X-Api-Key": credentials.get("api_key", "")},
        )
        if response_status >= 400:
            raise HTTPException(status_code=502, detail=f"{endpoint.kind.title()} connection test failed")

        _json_object(payload, endpoint.kind.title())
        return

    if endpoint.kind == "qbittorrent":
        login = urlencode(
            {"username": credentials.get("username", ""), "password": credentials.get("password", "")}
        ).encode()
        response_status, _, payload = await _request(
            _url(endpoint, "api/v2/auth/login"),
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            body=login,
        )
        if not _qbittorrent_login_succeeded(response_status, payload):
            raise HTTPException(status_code=502, detail="qBittorrent authentication failed")
        return

    if endpoint.kind == "transmission":
        body = json.dumps({"method": "session-get", "arguments": {}}).encode()
        headers = {"Content-Type": "application/json"}
        if credentials.get("username"):
            token = base64.b64encode(f"{credentials['username']}:{credentials.get('password', '')}".encode()).decode()
            headers["Authorization"] = f"Basic {token}"

        response_status, response_headers, payload = await _request(
            _url(endpoint, "transmission/rpc"),
            method="POST",
            headers=headers,
            body=body,
        )
        if response_status == 409:
            headers["X-Transmission-Session-Id"] = response_headers.get("X-Transmission-Session-Id", "")
            response_status, _, payload = await _request(
                _url(endpoint, "transmission/rpc"),
                method="POST",
                headers=headers,
                body=body,
            )
        if response_status >= 400 or _json_object(payload, "Transmission").get("result") != "success":
            raise HTTPException(status_code=502, detail="Transmission connection test failed")
        return

    if endpoint.kind == "deluge":
        login = json.dumps({"method": "auth.login", "params": [credentials.get("password", "")], "id": 1}).encode()
        response_status, _, payload = await _request(
            _url(endpoint, "json"),
            method="POST",
            headers={"Content-Type": "application/json"},
            body=login,
        )
        if response_status >= 400:
            raise HTTPException(status_code=502, detail="Deluge authentication failed")

        try:
            _require_deluge_result(payload)
        except HTTPException as exc:
            raise HTTPException(status_code=502, detail="Deluge authentication failed") from exc
        return

    raise HTTPException(status_code=422, detail="Endpoint kind is not supported")


async def run_rule(session: AsyncSession, rule: AcquisitionRule) -> list[AcquisitionJob]:
    """Run one rule once; callers may schedule this from their worker/cron service."""
    client = await owned_endpoint(session, rule.owner_user_id, rule.download_client_id)
    if client.kind in {"readarr", "sonarr", "radarr", "lidarr", "whisparr"}:
        filters = rule.filters or {}
        command = filters.get("arr_command")
        ids = filters.get("arr_ids", [])
        if (
            not isinstance(command, str)
            or not isinstance(ids, list)
            or not all(isinstance(value, int) for value in ids)
        ):
            raise HTTPException(
                status_code=422,
                detail="Arr rules require filters.arr_command and filters.arr_ids",
            )
        job = AcquisitionJob(
            owner_user_id=rule.owner_user_id,
            endpoint_id=client.endpoint_id,
            rule_id=rule.rule_id,
            title=command,
            download_url=f"arr-command:{command}",
        )
        session.add(job)
        try:
            job.client_reference = await dispatch_arr_command(client, command, ids)
            job.status = "submitted"
        except HTTPException as exc:
            job.status = "failed"
            job.error = str(exc.detail)
        rule.last_run_at = datetime.now(UTC)
        await session.commit()
        return [job]

    endpoint_ids = rule.endpoint_ids or []
    result = await session.execute(
        select(AcquisitionEndpoint).where(
            AcquisitionEndpoint.owner_user_id == rule.owner_user_id,
            AcquisitionEndpoint.endpoint_id.in_(endpoint_ids),
            AcquisitionEndpoint.enabled.is_(True),
        )
    )
    releases = [release for endpoint in result.scalars() for release in await search_endpoint(endpoint, rule.query)]
    if not releases:
        rule.last_run_at = datetime.now(UTC)
        await session.commit()
        return []
    selected = sorted(releases, key=lambda release: (release.seeders or 0, release.size_bytes or 0), reverse=True)[0]
    job = AcquisitionJob(
        owner_user_id=rule.owner_user_id,
        endpoint_id=client.endpoint_id,
        rule_id=rule.rule_id,
        title=selected.title,
        download_url=selected.download_url,
    )
    session.add(job)
    try:
        job.client_reference = await submit_to_client(client, selected.download_url, None, None)
        job.status = "submitted"
    except HTTPException as exc:
        job.status = "failed"
        job.error = str(exc.detail)
    rule.last_run_at = datetime.now(UTC)
    await session.commit()
    return [job]


async def run_enabled_rules(session: AsyncSession) -> None:
    """Run enabled rules, isolating a failed remote integration from the others."""
    result = await session.execute(select(AcquisitionRule).where(AcquisitionRule.enabled.is_(True)))
    for rule in result.scalars():
        try:
            await run_rule(session, rule)
        except HTTPException:
            await session.rollback()
