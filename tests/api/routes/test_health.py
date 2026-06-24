"""Tests for health check endpoint."""

from httpx import AsyncClient


async def test_index_lists_available_pages(prod_client: AsyncClient):
    """Test root index returns the available page endpoints."""
    response = await prod_client.get("/")
    assert response.status_code == 200
    data = response.json()
    pages = {page["name"]: page["path"] for page in data["pages"]}
    assert data["name"] == "Papyrus Server API"
    assert pages["docs"] == "http://localhost:8080/docs"
    assert pages["redoc"] == "http://localhost:8080/redoc"
    assert pages["openapi"] == "http://localhost:8080/openapi.json"
    assert "auth_sandbox" not in pages


async def test_index_lists_debug_pages(debug_client: AsyncClient):
    """Test root index includes the debug sandboxes in debug mode."""
    response = await debug_client.get("/")
    assert response.status_code == 200
    data = response.json()
    pages = {page["name"]: page["path"] for page in data["pages"]}
    assert pages["auth_sandbox"] == "http://localhost:8080/__dev/auth-sandbox"
    assert pages["powersync_sandbox"] == "http://localhost:8080/__dev/powersync-sandbox"


async def test_health_check(client: AsyncClient):
    """Test health check endpoint."""
    response = await client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
