"""Account-independent OPDS relay endpoint."""

from anyio import CancelScope
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.types import Receive, Scope, Send

from papyrus.config import get_settings
from papyrus.core.rate_limit import limiter
from papyrus.schemas.opds import OpdsRelayRequest
from papyrus.services.opds import RelayResource, open_resource

router = APIRouter()


class RelayResponse(StreamingResponse):
    """Close the upstream socket even if a client leaves before streaming starts."""

    def __init__(self, resource: RelayResource) -> None:
        self.resource = resource
        headers = {
            "X-OPDS-URL": resource.url,
            "Content-Type": resource.content_type,
            "Cache-Control": "no-store",
            "Content-Disposition": "attachment",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        }
        if resource.length is not None:
            headers["Content-Length"] = str(resource.length)

        super().__init__(resource.chunks(), headers=headers)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with CancelScope(shield=True):
                await run_in_threadpool(self.resource.close)


@router.post("/relay", summary="Retrieve an OPDS resource", response_class=StreamingResponse)
@limiter.limit(lambda: f"{get_settings().rate_limit_general}/minute")
def relay_resource(request: Request, payload: OpdsRelayRequest) -> StreamingResponse:
    """Stream a public catalog resource without requiring a Papyrus account."""
    return RelayResponse(open_resource(payload))
