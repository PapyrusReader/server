"""Support is available to guests as well as signed-in readers."""

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.routing import APIRoute
from pydantic import Json
from starlette.types import Message

from papyrus.core.rate_limit import limiter
from papyrus.schemas.support import SupportRequest, SupportResponse
from papyrus.services.support import submit_support
from papyrus.services.support_attachments import MAX_REQUEST_BYTES, read_attachments

RequestHandler = Callable[[Request], Coroutine[Any, Any, Response]]


@limiter.limit("3/hour;10/day")
async def handle_support_request(request: Request, handler: RequestHandler) -> Response:
    """Rate-limit and bound uploads before the multipart parser spools their bodies."""
    received = 0

    async def receive() -> Message:
        nonlocal received
        message = await request.receive()
        received += len(message.get("body", b""))

        if received > MAX_REQUEST_BYTES:
            raise HTTPException(status_code=413, detail="Submission is too large.")

        return message

    return await handler(Request(request.scope, receive=receive))


class SupportRoute(APIRoute):
    def get_route_handler(self) -> RequestHandler:
        handler = super().get_route_handler()

        async def limited_request(request: Request) -> Response:
            response: Response = await handle_support_request(request, handler)
            return response

        return limited_request


router = APIRouter(route_class=SupportRoute)


@router.post("/issues", response_model=SupportResponse, status_code=201)
def create_support_issue(
    request: Request,
    payload: Annotated[Json[SupportRequest], Form()],
    attachments: Annotated[list[UploadFile] | None, File()] = None,
) -> SupportResponse:
    return submit_support(payload, read_attachments(attachments or []))
