"""Indexer and download-client protocol adapters."""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Self
from urllib.parse import urlencode
from xml.etree import ElementTree

from fastapi import HTTPException

from papyrus.models.acquisition import AcquisitionEndpoint

from .transport import (
    _credentials,
    _header_value,
    _json_array,
    _json_object,
    _qbittorrent_login_succeeded,
    _request,
    _require_deluge_result,
    _url,
)
from .types import QbittorrentFile, QbittorrentTorrent, ReleaseCandidate


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
        progress_basis_points = _progress_basis_points(item.get("progress"))
        completed_bytes = _optional_int(item.get("completed"))

        return QbittorrentTorrent(
            hash=_required_string(item, "hash", "qBittorrent torrent"),
            state=_required_string(item, "state", "qBittorrent torrent"),
            progress_basis_points=progress_basis_points,
            downloaded_bytes=completed_bytes
            if completed_bytes is not None
            else _required_int(item, "downloaded", "qBittorrent torrent"),
            total_bytes=_required_int(item, "total_size", "qBittorrent torrent"),
            download_speed_bytes_per_second=_required_int(item, "dlspeed", "qBittorrent torrent"),
            eta_seconds=0 if progress_basis_points == 10_000 else _optional_int(item.get("eta")),
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

        await self._post_form(
            "api/v2/torrents/pause",
            {"hashes": torrent_hash},
            fallback_path="api/v2/torrents/stop",
        )

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

        await self._post_form(
            "api/v2/torrents/resume",
            {"hashes": torrent_hash},
            fallback_path="api/v2/torrents/start",
        )

    async def pause(self, torrent_hash: str) -> None:
        await self._post_form(
            "api/v2/torrents/pause",
            {"hashes": torrent_hash},
            fallback_path="api/v2/torrents/stop",
        )

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

    async def _post_form(
        self,
        path: str,
        values: dict[str, str],
        *,
        fallback_path: str | None = None,
    ) -> None:
        response_status, _, _ = await _request(
            _url(self.endpoint, path),
            method="POST",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Cookie": self.cookie,
            },
            body=urlencode(values).encode(),
        )

        if response_status == 404 and fallback_path is not None:
            response_status, _, _ = await _request(
                _url(self.endpoint, fallback_path),
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
        return await _submit_qbittorrent(
            endpoint,
            download_url,
            category,
            save_path,
            tags=tags,
        )

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
        _url(endpoint, "json"),
        method="POST",
        headers=headers,
        body=login,
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

    response_status, _, payload = await _request(
        _url(endpoint, "json"),
        method="POST",
        headers=headers,
        body=body,
    )

    if response_status >= 400:
        raise HTTPException(status_code=502, detail="Deluge rejected the release")

    try:
        result = _require_deluge_result(payload)
    except HTTPException as exc:
        raise HTTPException(status_code=502, detail="Deluge rejected the release") from exc

    return str(result)


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
