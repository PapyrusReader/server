"""Bounded integration transport and credential decoding."""

from __future__ import annotations

import asyncio
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from fastapi import HTTPException

from papyrus.core.security import decrypt_secret_payload
from papyrus.models.acquisition import AcquisitionEndpoint


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


def _header_value(headers: dict[str, str], name: str) -> str:
    normalized_name = name.casefold()
    return next((value for key, value in headers.items() if key.casefold() == normalized_name), "")


def _qbittorrent_login_succeeded(response_status: int, response_payload: bytes) -> bool:
    return response_status < 400 and (response_status == 204 or response_payload.strip() == b"Ok.")
