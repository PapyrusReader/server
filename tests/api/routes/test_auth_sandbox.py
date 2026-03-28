"""Tests for the development-only auth sandbox."""

import json

from httpx import AsyncClient

from papyrus.core import dev_pages
from papyrus.main import settings as app_settings


async def test_auth_sandbox_not_registered_in_production_mode(prod_client: AsyncClient):
    """Test the auth sandbox is absent when debug mode is disabled."""
    response = await prod_client.get("/__dev/auth-sandbox")
    assert response.status_code == 404


async def test_auth_sandbox_registered_in_debug_vite_mode(
    debug_client: AsyncClient,
    monkeypatch,
):
    """Test the auth sandbox page points at the Vite dev server when enabled."""
    monkeypatch.setattr(app_settings, "dev_pages_use_vite", True)
    monkeypatch.setattr(app_settings, "dev_pages_vite_url", "http://vite.test:5173")

    response = await debug_client.get("/__dev/auth-sandbox")

    assert response.status_code == 200
    assert "Authentication sandbox" in response.text
    assert "window.__PAPYRUS_DEV_PAGE_CONFIG__ =" in response.text
    assert 'data-dev-page="auth-sandbox"' in response.text
    assert 'data-shell-marker="sticky-status-rail"' in response.text
    assert 'href="/__dev/auth-sandbox"' in response.text
    assert 'href="/__dev/powersync-sandbox"' in response.text
    assert "dev-page-nav__link--active" in response.text
    assert 'src="http://vite.test:5173/@vite/client"' in response.text
    assert 'src="http://vite.test:5173/src/pages/auth-sandbox/main.ts"' in response.text
    assert '"registerUrl": "/v1/auth/register"' in response.text
    assert 'id="logout" class="secondary" disabled' in response.text
    assert 'id="get-me" class="secondary" disabled' in response.text


async def test_auth_sandbox_renders_built_assets_when_manifest_exists(
    debug_client: AsyncClient,
    monkeypatch,
    tmp_path,
):
    """Test the auth sandbox uses built assets when Vite mode is disabled."""
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps({
            "src/pages/auth-sandbox/main.ts": {
                "file": "assets/auth-sandbox.js",
                "css": ["assets/auth-sandbox.css"],
                "imports": [],
                "src": "src/pages/auth-sandbox/main.ts",
            }
        }),
        encoding="utf-8",
    )

    monkeypatch.setattr(app_settings, "dev_pages_use_vite", False)
    monkeypatch.setattr(app_settings, "dev_pages_manifest_path", str(manifest_path))
    dev_pages._load_manifest.cache_clear()

    response = await debug_client.get("/__dev/auth-sandbox")

    assert response.status_code == 200
    assert 'href="/__dev/static/assets/auth-sandbox.css"' in response.text
    assert 'src="/__dev/static/assets/auth-sandbox.js"' in response.text
    assert 'data-shell-marker="sticky-status-rail"' in response.text
    assert "@vite/client" not in response.text
    assert "src/pages/auth-sandbox/main.ts" not in response.text


async def test_auth_sandbox_session_endpoint(
    debug_client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
):
    """Test the auth sandbox session endpoint reports token and DB session state."""
    response = await debug_client.get("/__dev/auth-sandbox/session", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["session"]["user_id"] == auth_user["user_id"]
    assert body["session"]["session_id"] == auth_user["session_id"]
    assert body["access_payload"]["sub"] == auth_user["user_id"]
