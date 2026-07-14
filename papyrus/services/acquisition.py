"""Adapters for indexer protocols and self-hosted download clients."""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.core.security import decrypt_secret_payload
from papyrus.models.acquisition import AcquisitionEndpoint, AcquisitionJob, AcquisitionRule
from papyrus.schemas.acquisition import Release


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


async def search_endpoint(endpoint: AcquisitionEndpoint, query: str) -> list[Release]:
    """Search Prowlarr or a Torznab-compatible torrent indexer."""
    credentials = _credentials(endpoint)
    if endpoint.kind == "prowlarr":
        request_url = _url(endpoint, f"api/v1/search?{urlencode({'query': query})}")
        response_status, _, payload = await _request(request_url, headers={"X-Api-Key": credentials.get("api_key", "")})
        if response_status >= 400:
            raise HTTPException(status_code=502, detail="Prowlarr search failed")
        data = json.loads(payload)
        return [
            Release(
                title=item.get("title", "Untitled"),
                download_url=item.get("downloadUrl") or item.get("magnetUrl") or item.get("guid", ""),
                protocol="torrent",
                indexer=item.get("indexer", "Prowlarr"),
                size_bytes=item.get("size"),
                seeders=item.get("seeders"),
            )
            for item in data
            if (item.get("downloadUrl") or item.get("magnetUrl") or item.get("guid"))
            and item.get("protocol", "torrent") == "torrent"
        ]

    params = urlencode({"t": "search", "q": query, "apikey": credentials.get("api_key", "")})
    response_status, _, payload = await _request(_url(endpoint, f"api?{params}"))
    if response_status >= 400:
        raise HTTPException(status_code=502, detail=f"{endpoint.kind.title()} search failed")
    return _parse_torznab(payload, endpoint.name)


def _parse_torznab(payload: bytes, indexer: str) -> list[Release]:
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        raise HTTPException(status_code=502, detail="Indexer returned invalid XML") from exc
    releases: list[Release] = []
    for item in root.findall(".//item"):
        enclosure = item.find("enclosure")
        link = (enclosure.get("url") if enclosure is not None else None) or item.findtext("link")
        if not link:
            continue
        attrs = {child.attrib.get("name"): child.attrib.get("value") for child in item if child.tag.endswith("attr")}
        size = attrs.get("size")
        seeders = attrs.get("seeders")
        releases.append(
            Release(
                title=item.findtext("title") or "Untitled",
                download_url=link,
                protocol="torrent",
                indexer=indexer,
                size_bytes=int(size) if size is not None and size.isdigit() else None,
                seeders=int(seeders) if seeders is not None and seeders.isdigit() else None,
            )
        )
    return releases


async def submit_to_client(
    endpoint: AcquisitionEndpoint, download_url: str, category: str | None, save_path: str | None
) -> str | None:
    """Submit a magnet or torrent URL to a supported BitTorrent client."""
    if endpoint.kind == "qbittorrent":
        return await _submit_qbittorrent(endpoint, download_url, category, save_path)
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
        _url(endpoint, "api/v1/command"),
        method="POST",
        headers={"Content-Type": "application/json", "X-Api-Key": _credentials(endpoint).get("api_key", "")},
        body=json.dumps(payload).encode(),
    )
    if response_status >= 400:
        raise HTTPException(status_code=502, detail=f"{endpoint.kind.title()} command failed")
    response = json.loads(response_payload)
    return str(response.get("id")) if response.get("id") is not None else None


async def _submit_qbittorrent(
    endpoint: AcquisitionEndpoint, download_url: str, category: str | None, save_path: str | None
) -> str | None:
    credentials = _credentials(endpoint)
    login = urlencode(
        {"username": credentials.get("username", ""), "password": credentials.get("password", "")}
    ).encode()
    response_status, headers, _ = await _request(
        _url(endpoint, "api/v2/auth/login"),
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        body=login,
    )
    if response_status >= 400:
        raise HTTPException(status_code=502, detail="qBittorrent authentication failed")
    payload = {"urls": download_url}
    if category:
        payload["category"] = category
    if save_path:
        payload["savepath"] = save_path
    response_status, _, _ = await _request(
        _url(endpoint, "api/v2/torrents/add"),
        method="POST",
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Cookie": headers.get("Set-Cookie", "").split(";", 1)[0],
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
        _url(endpoint, "transmission/rpc"), method="POST", headers=headers, body=body
    )
    if response_status == 409:
        headers["X-Transmission-Session-Id"] = response_headers.get("X-Transmission-Session-Id", "")
        response_status, _, payload = await _request(
            _url(endpoint, "transmission/rpc"), method="POST", headers=headers, body=body
        )
    if response_status >= 400:
        raise HTTPException(status_code=502, detail="Transmission rejected the release")
    return str(json.loads(payload).get("arguments", {}).get("torrent-added", {}).get("hashString") or "") or None


async def _submit_deluge(endpoint: AcquisitionEndpoint, download_url: str, save_path: str | None) -> str | None:
    credentials = _credentials(endpoint)
    headers = {"Content-Type": "application/json"}
    login = json.dumps({"method": "auth.login", "params": [credentials.get("password", "")], "id": 1}).encode()
    response_status, response_headers, _ = await _request(
        _url(endpoint, "json"), method="POST", headers=headers, body=login
    )
    if response_status >= 400:
        raise HTTPException(status_code=502, detail="Deluge authentication failed")
    options = {"download_location": save_path} if save_path else {}
    body = json.dumps({"method": "core.add_torrent_magnet", "params": [download_url, options], "id": 2}).encode()
    headers["Cookie"] = response_headers.get("Set-Cookie", "").split(";", 1)[0]
    response_status, _, payload = await _request(_url(endpoint, "json"), method="POST", headers=headers, body=body)
    if response_status >= 400:
        raise HTTPException(status_code=502, detail="Deluge rejected the release")
    result = json.loads(payload).get("result")
    return str(result) if result is not None else None


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
