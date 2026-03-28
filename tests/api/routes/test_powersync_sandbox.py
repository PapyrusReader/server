"""Tests for the development-only PowerSync sandbox."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.api.routes import dev_powersync_sandbox
from papyrus.core import dev_pages
from papyrus.main import settings as app_settings
from papyrus.models import PowerSyncDemoItem, User


async def _seed_demo_item(
    session: AsyncSession,
    *,
    owner_user_id: UUID,
    title: str,
    notes: str | None = None,
) -> PowerSyncDemoItem:
    item = PowerSyncDemoItem(
        item_id=uuid4(),
        owner_user_id=owner_user_id,
        title=title,
        notes=notes,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    session.add(item)
    await session.commit()
    await session.refresh(item)
    return item


async def test_powersync_sandbox_not_registered_in_production_mode(prod_client: AsyncClient):
    """The PowerSync sandbox page is absent when debug mode is disabled."""
    response = await prod_client.get("/__dev/powersync-sandbox")
    assert response.status_code == 404


async def test_powersync_sandbox_registered_in_debug_vite_mode(
    debug_client: AsyncClient,
    monkeypatch,
):
    """The PowerSync sandbox page uses Vite-served assets when configured."""
    monkeypatch.setattr(app_settings, "dev_pages_use_vite", True)
    monkeypatch.setattr(app_settings, "dev_pages_vite_url", "http://vite.test:5173")

    response = await debug_client.get("/__dev/powersync-sandbox")

    assert response.status_code == 200
    assert "PowerSync Sandbox" in response.text
    assert 'data-dev-page="powersync-sandbox"' in response.text
    assert 'data-shell-marker="sticky-status-rail"' in response.text
    assert 'src="http://vite.test:5173/@vite/client"' in response.text
    assert 'src="http://vite.test:5173/src/pages/powersync-sandbox/main.ts"' in response.text
    assert '"powersync_endpoint": "http://localhost:8081"' in response.text
    assert 'id="connect" disabled' in response.text
    assert 'id="create-item" disabled' in response.text


async def test_powersync_sandbox_renders_built_assets_when_manifest_exists(
    debug_client: AsyncClient,
    monkeypatch,
    tmp_path,
):
    """The PowerSync sandbox page falls back to built assets outside Vite mode."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "src/pages/powersync-sandbox/main.ts": {
                    "file": "assets/powersync-sandbox.js",
                    "css": ["assets/powersync-sandbox.css"],
                    "imports": [],
                    "src": "src/pages/powersync-sandbox/main.ts",
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(app_settings, "dev_pages_use_vite", False)
    monkeypatch.setattr(app_settings, "dev_pages_manifest_path", str(manifest_path))
    dev_pages._load_manifest.cache_clear()

    response = await debug_client.get("/__dev/powersync-sandbox")

    assert response.status_code == 200
    assert 'href="/__dev/static/assets/powersync-sandbox.css"' in response.text
    assert 'src="/__dev/static/assets/powersync-sandbox.js"' in response.text
    assert 'data-shell-marker="sticky-status-rail"' in response.text
    assert "@vite/client" not in response.text


async def test_powersync_sandbox_config_returns_expected_urls(debug_client: AsyncClient):
    """The sandbox config endpoint exposes the browser and backend integration URLs."""
    response = await debug_client.get("/__dev/powersync-sandbox/config?client=Two Tabs")
    assert response.status_code == 200
    body = response.json()
    assert body["register_url"] == "/v1/auth/register"
    assert body["powersync_token_url"] == "/v1/auth/powersync-token"
    assert body["upload_url"] == "/__dev/powersync-demo/upload"
    assert body["items_url"] == "/__dev/powersync-demo/items"
    assert body["db_filename"] == "powersync-demo-two-tabs.db"
    assert body["redirect_uri"] == "http://test/__dev/powersync-sandbox?client=Two Tabs"
    assert body["sdk_module_url"].startswith("https://esm.sh/@powersync/web@")
    assert body["db_worker_url"] == "http://test/__dev/powersync-sandbox/worker/WASQLiteDB.umd.js"
    assert body["sync_worker_url"] == "http://test/__dev/powersync-sandbox/worker/SharedSyncImplementation.umd.js"


async def test_powersync_sandbox_worker_asset_is_served_from_backend_origin(
    debug_client: AsyncClient,
    monkeypatch,
):
    """Worker assets and their nested chunks are proxied through the backend origin."""
    monkeypatch.setattr(
        dev_powersync_sandbox,
        "_fetch_dist_asset",
        lambda asset_path: f"// {asset_path}".encode(),
    )

    response = await debug_client.get("/__dev/powersync-sandbox/worker/WASQLiteDB.umd.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["x-papyrus-vendor-path"] == "WASQLiteDB.umd.js"
    assert response.text == "// worker/WASQLiteDB.umd.js"

    nested_response = await debug_client.get(
        "/__dev/powersync-sandbox/worker/node_modules_pnpm_example_chunk.umd.js"
    )
    assert nested_response.status_code == 200
    assert nested_response.headers["x-papyrus-vendor-path"] == "node_modules_pnpm_example_chunk.umd.js"
    assert nested_response.text == "// worker/node_modules_pnpm_example_chunk.umd.js"


async def test_powersync_sandbox_root_asset_is_served_from_backend_origin(
    debug_client: AsyncClient,
    monkeypatch,
):
    """Root dist assets such as WASM binaries are proxied through the backend origin."""
    monkeypatch.setattr(
        dev_powersync_sandbox,
        "_fetch_dist_asset",
        lambda asset_path: f"// {asset_path}".encode(),
    )

    response = await debug_client.get("/__dev/powersync-sandbox/ca59e199e1138b553fad.wasm")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/wasm")
    assert response.headers["x-papyrus-vendor-path"] == "ca59e199e1138b553fad.wasm"
    assert response.text == "// ca59e199e1138b553fad.wasm"


async def test_powersync_demo_items_requires_authentication(debug_client: AsyncClient):
    """Source snapshot and upload routes are protected."""
    items_response = await debug_client.get("/__dev/powersync-demo/items")
    upload_response = await debug_client.post("/__dev/powersync-demo/upload", json={"batch": []})
    assert items_response.status_code == 401
    assert upload_response.status_code == 401


async def test_powersync_demo_items_only_lists_owned_rows(
    debug_client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
):
    """Source snapshot only includes rows owned by the authenticated user."""
    owner = await db_session.get(User, UUID(auth_user["user_id"]))
    assert owner is not None

    other_user = User(
        display_name="Other User",
        primary_email="other@example.com",
        primary_email_verified=True,
        last_login_at=datetime.now(UTC),
    )
    db_session.add(other_user)
    await db_session.flush()

    await _seed_demo_item(db_session, owner_user_id=owner.user_id, title="Owned Item")
    await _seed_demo_item(db_session, owner_user_id=other_user.user_id, title="Other Item")

    response = await debug_client.get("/__dev/powersync-demo/items", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert [item["title"] for item in body["items"]] == ["Owned Item"]


async def test_powersync_upload_applies_create_update_and_delete(
    debug_client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
):
    """The upload endpoint applies PowerSync CRUD batches to the source table."""
    item_id = str(uuid4())

    create_response = await debug_client.post(
        "/__dev/powersync-demo/upload",
        headers=auth_headers,
        json={
            "batch": [
                {
                    "type": "demo_items",
                    "op": "PUT",
                    "id": item_id,
                    "data": {"title": "Created Item", "notes": "Initial notes"},
                }
            ]
        },
    )
    assert create_response.status_code == 200
    assert create_response.json()["applied_count"] == 1

    created = await db_session.get(PowerSyncDemoItem, UUID(item_id))
    assert created is not None
    assert created.owner_user_id == UUID(auth_user["user_id"])
    assert created.title == "Created Item"

    update_response = await debug_client.post(
        "/__dev/powersync-demo/upload",
        headers=auth_headers,
        json={
            "batch": [
                {
                    "type": "demo_items",
                    "op": "PATCH",
                    "id": item_id,
                    "data": {"title": "Updated Item"},
                }
            ]
        },
    )
    assert update_response.status_code == 200

    await db_session.refresh(created)
    assert created.title == "Updated Item"
    assert created.notes == "Initial notes"

    delete_response = await debug_client.post(
        "/__dev/powersync-demo/upload",
        headers=auth_headers,
        json={"batch": [{"type": "demo_items", "op": "DELETE", "id": item_id}]},
    )
    assert delete_response.status_code == 200
    db_session.expire_all()
    assert await db_session.get(PowerSyncDemoItem, UUID(item_id)) is None


async def test_powersync_upload_rejects_mutating_other_users_rows(
    debug_client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
):
    """Users cannot mutate demo rows owned by another account."""
    other_user = User(
        display_name="Other User",
        primary_email="other-owner@example.com",
        primary_email_verified=True,
        last_login_at=datetime.now(UTC),
    )
    db_session.add(other_user)
    await db_session.flush()
    foreign_item = await _seed_demo_item(db_session, owner_user_id=other_user.user_id, title="Other Owner Item")

    response = await debug_client.post(
        "/__dev/powersync-demo/upload",
        headers=auth_headers,
        json={
            "batch": [
                {
                    "type": "demo_items",
                    "op": "PATCH",
                    "id": str(foreign_item.item_id),
                    "data": {"title": "Unauthorized edit"},
                }
            ]
        },
    )

    assert response.status_code == 403
