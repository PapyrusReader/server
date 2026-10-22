"""Only the configured proxy may provide client addresses for anonymous limits."""

import pytest
from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from papyrus.core.rate_limit import rate_limit_exceeded_handler


@pytest.mark.parametrize("peer", ["172.20.0.2", "172.20.0.3"])
async def test_forwarded_ip_is_only_used_from_trusted_proxy(peer: str) -> None:
    app = FastAPI()
    limiter = Limiter(key_func=get_remote_address)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)

    @app.get("/limited")
    @limiter.limit("1/minute")
    async def limited(request: Request) -> dict[str, str]:
        assert request.client is not None
        return {"client": request.client.host}

    proxy_app = ProxyHeadersMiddleware(app, trusted_hosts="172.20.0.2")
    transport = ASGITransport(app=proxy_app, client=(peer, 1234))

    async with AsyncClient(transport=transport, base_url="http://test") as client:
        first = await client.get("/limited", headers={"X-Forwarded-For": "198.51.100.1"})
        second = await client.get("/limited", headers={"X-Forwarded-For": "198.51.100.2"})
        repeated = await client.get("/limited", headers={"X-Forwarded-For": "198.51.100.1"})

    assert first.status_code == 200
    assert first.json()["client"] == ("198.51.100.1" if peer == "172.20.0.2" else peer)
    assert second.status_code == (200 if peer == "172.20.0.2" else 429)
    assert repeated.status_code == 429
    assert int(repeated.headers["Retry-After"]) > 0
