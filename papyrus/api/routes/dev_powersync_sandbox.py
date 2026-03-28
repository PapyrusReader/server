"""Development-only PowerSync sandbox routes."""

from __future__ import annotations

import tarfile
from functools import lru_cache
from io import BytesIO
from re import sub
from typing import Annotated
from urllib.error import URLError
from urllib.request import urlopen

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.api.deps import CurrentUserId
from papyrus.config import get_settings
from papyrus.core.database import get_db
from papyrus.core.dev_pages import render_dev_page
from papyrus.schemas.powersync_demo import (
    PowerSyncDemoItem,
    PowerSyncDemoItemList,
    PowerSyncSandboxConfigResponse,
    PowerSyncUploadRequest,
    PowerSyncUploadResponse,
)
from papyrus.services import powersync_demo as powersync_demo_service

router = APIRouter(tags=["Dev"])
DBSession = Annotated[AsyncSession, Depends(get_db)]
POWERSYNC_WEB_SDK_VERSION = "1.37.0"
POWERSYNC_NPM_TARBALL_URL = f"https://registry.npmjs.org/@powersync/web/-/web-{POWERSYNC_WEB_SDK_VERSION}.tgz"


def _normalize_client_label(raw_value: str | None) -> str:
    candidate = sub(r"[^a-zA-Z0-9_-]+", "-", (raw_value or "one").strip()).strip("-").lower()

    if not candidate:
        return "one"

    return candidate[:40]


def _build_redirect_uri(request: Request) -> str:
    redirect_uri = str(request.url_for("powersync_sandbox_page"))
    client = request.query_params.get("client")

    if client:
        redirect_uri = f"{redirect_uri}?client={client}"

    return redirect_uri


def _validate_dist_asset_path(asset_path: str) -> str:
    normalized = asset_path.strip()

    if not normalized or normalized.startswith("/") or ".." in normalized.split("/"):
        raise KeyError(asset_path)

    return normalized


@lru_cache(maxsize=1)
def _load_powersync_dist_assets() -> dict[str, bytes]:
    with urlopen(POWERSYNC_NPM_TARBALL_URL, timeout=20) as response:
        archive_bytes = response.read()

    assets: dict[str, bytes] = {}

    with tarfile.open(fileobj=BytesIO(archive_bytes), mode="r:gz") as archive:
        for member in archive.getmembers():
            if not member.isfile() or not member.name.startswith("package/dist/"):
                continue

            extracted = archive.extractfile(member)

            if extracted is None:
                continue

            relative_path = member.name.removeprefix("package/dist/")
            assets[relative_path] = extracted.read()

    return assets


@lru_cache(maxsize=64)
def _fetch_dist_asset(asset_path: str) -> bytes:
    normalized = _validate_dist_asset_path(asset_path)
    assets = _load_powersync_dist_assets()

    if normalized not in assets:
        raise KeyError(normalized)

    return assets[normalized]


def _asset_media_type(asset_path: str) -> str:
    if asset_path.endswith(".wasm"):
        return "application/wasm"

    if asset_path.endswith(".map"):
        return "application/json"

    return "text/javascript"


def _build_powersync_sandbox_config(request: Request, client: str | None = None) -> PowerSyncSandboxConfigResponse:
    settings = get_settings()
    client_label = _normalize_client_label(client)
    api_prefix = settings.api_prefix

    return PowerSyncSandboxConfigResponse(
        register_url=f"{api_prefix}/auth/register",
        login_url=f"{api_prefix}/auth/login",
        refresh_url=f"{api_prefix}/auth/refresh",
        logout_url=f"{api_prefix}/auth/logout",
        google_start_url=f"{api_prefix}/auth/oauth/google/start",
        exchange_url=f"{api_prefix}/auth/exchange-code",
        me_url=f"{api_prefix}/users/me",
        powersync_token_url=f"{api_prefix}/auth/powersync-token",
        powersync_endpoint=settings.powersync_service_url,
        items_url="/__dev/powersync-demo/items",
        upload_url="/__dev/powersync-demo/upload",
        redirect_uri=_build_redirect_uri(request),
        sdk_module_url=f"https://esm.sh/@powersync/web@{POWERSYNC_WEB_SDK_VERSION}?bundle",
        db_worker_url=str(request.url_for("powersync_sandbox_worker_asset", asset_path="WASQLiteDB.umd.js")),
        sync_worker_url=str(
            request.url_for("powersync_sandbox_worker_asset", asset_path="SharedSyncImplementation.umd.js")
        ),
        db_filename=f"powersync-demo-{client_label}.db",
    )


@router.get("/__dev/powersync-sandbox", response_class=HTMLResponse, name="powersync_sandbox_page")
async def powersync_sandbox_page(request: Request, client: str | None = None) -> HTMLResponse:
    """Render the development-only PowerSync validation sandbox."""
    return render_dev_page(
        request,
        template_name="powersync_sandbox.html",
        page_title="Papyrus PowerSync Sandbox",
        page_id="powersync-sandbox",
        body_class="dev-page--powersync",
        entry_module="src/pages/powersync-sandbox/main.ts",
        page_config=_build_powersync_sandbox_config(request, client).model_dump(),
    )


@router.get("/__dev/powersync-sandbox/worker/{asset_path:path}", name="powersync_sandbox_worker_asset")
async def powersync_sandbox_worker_asset(asset_path: str) -> Response:
    """Serve PowerSync worker assets from the sandbox origin."""
    try:
        content = _fetch_dist_asset(f"worker/{asset_path}")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown PowerSync worker asset") from exc
    except URLError as exc:
        raise HTTPException(status_code=502, detail="Failed to fetch PowerSync worker asset") from exc

    headers = {"Cache-Control": "public, max-age=86400", "X-Papyrus-Vendor-Path": asset_path}
    return Response(content, media_type=_asset_media_type(asset_path), headers=headers)


@router.get(
    "/__dev/powersync-sandbox/config",
    response_model=PowerSyncSandboxConfigResponse,
)
async def powersync_sandbox_config(request: Request, client: str | None = None) -> PowerSyncSandboxConfigResponse:
    """Return runtime config for the PowerSync sandbox page."""
    return _build_powersync_sandbox_config(request, client)


@router.get(
    "/__dev/powersync-demo/items",
    response_model=PowerSyncDemoItemList,
)
async def list_powersync_demo_items(user_id: CurrentUserId, db: DBSession) -> PowerSyncDemoItemList:
    """Return the authenticated user's PowerSync demo rows from the source database."""
    items = await powersync_demo_service.list_demo_items(db, user_id)
    return PowerSyncDemoItemList(items=[PowerSyncDemoItem.model_validate(item) for item in items])


@router.post(
    "/__dev/powersync-demo/upload",
    response_model=PowerSyncUploadResponse,
)
async def upload_powersync_demo_items(
    request: PowerSyncUploadRequest,
    user_id: CurrentUserId,
    db: DBSession,
) -> PowerSyncUploadResponse:
    """Apply a PowerSync upload queue batch to the demo source table."""
    applied_count = await powersync_demo_service.apply_upload_batch(db, user_id, request.batch)
    return PowerSyncUploadResponse(applied_count=applied_count)


@router.get("/__dev/powersync-sandbox/{asset_name}")
async def powersync_sandbox_root_asset(asset_name: str) -> Response:
    """Serve root dist assets such as WASM binaries from the sandbox origin."""
    if not (asset_name.endswith(".wasm") or asset_name.endswith(".map")):
        raise HTTPException(status_code=404, detail="Unknown PowerSync root asset")

    try:
        content = _fetch_dist_asset(asset_name)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Unknown PowerSync root asset") from exc
    except URLError as exc:
        raise HTTPException(status_code=502, detail="Failed to fetch PowerSync root asset") from exc

    headers = {"Cache-Control": "public, max-age=86400", "X-Papyrus-Vendor-Path": asset_name}
    return Response(content, media_type=_asset_media_type(asset_name), headers=headers)
