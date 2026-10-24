"""Public, narrowly authorized deletion and recovery routes."""

from pathlib import Path
from secrets import token_urlsafe
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.api.deps import CurrentSessionId, CurrentUserId
from papyrus.api.routes.auth import _auth_rate_limit, _auth_tokens_response, _public_callback_url
from papyrus.config import get_settings
from papyrus.core.database import get_db
from papyrus.core.rate_limit import limiter
from papyrus.schemas.account_deletion import (
    AccountActionRequest,
    AccountDeletionAuthorization,
    AccountDeletionStatus,
    AccountGoogleExchangeRequest,
    AccountPasswordRequest,
    AccountRecoveryRequest,
)
from papyrus.schemas.auth import AuthTokens
from papyrus.services import account_deletion
from papyrus.services.auth.google import build_google_account_authorization_url


def no_store(response: Response) -> None:
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


router = APIRouter(dependencies=[Depends(no_store)])
DBSession = Annotated[AsyncSession, Depends(get_db)]


@router.get("", response_class=HTMLResponse, include_in_schema=False)
async def account_deletion_page() -> HTMLResponse:
    nonce = token_urlsafe(24)
    template = Path(__file__).parents[2] / "web" / "account_deletion.html"

    return HTMLResponse(
        template.read_text().replace("__NONCE__", nonce),
        headers={
            "Cache-Control": "no-store",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": (
                f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
            ),
        },
    )


@router.post("/password", response_model=AccountDeletionAuthorization)
@limiter.limit(_auth_rate_limit)
async def authenticate_password(
    request: Request, payload: AccountPasswordRequest, db: DBSession
) -> AccountDeletionAuthorization:
    return await account_deletion.authenticate_password(db, payload)


@router.get("/google/start")
@limiter.limit(_auth_rate_limit)
async def google_start(
    request: Request,
    redirect_uri: str,
    challenge: str = Query(pattern=r"^[0-9a-f]{64}$"),
    expected_user_id: UUID | None = None,
) -> RedirectResponse:
    url = build_google_account_authorization_url(
        redirect_uri,
        _public_callback_url(request, "google_oauth_callback"),
        challenge,
        expected_user_id,
    )

    return RedirectResponse(url, status_code=302)


@router.post("/exchange", response_model=AccountDeletionAuthorization)
@limiter.limit(_auth_rate_limit)
async def exchange(
    request: Request, payload: AccountGoogleExchangeRequest, db: DBSession
) -> AccountDeletionAuthorization:
    return await account_deletion.exchange_google(db, payload.code, payload.verifier)


@router.post("/schedule", response_model=AccountDeletionStatus)
@limiter.limit(_auth_rate_limit)
async def schedule(request: Request, payload: AccountActionRequest, db: DBSession) -> AccountDeletionStatus:
    return await account_deletion.schedule_deletion(db, payload.authorization_token)


@router.post("/current", response_model=AccountDeletionStatus)
@limiter.limit(_auth_rate_limit)
async def schedule_current(
    request: Request, db: DBSession, user_id: CurrentUserId, session_id: CurrentSessionId
) -> AccountDeletionStatus:
    return await account_deletion.schedule_current_account(db, user_id, session_id)


@router.post("/cancel", response_model=AuthTokens)
@limiter.limit(_auth_rate_limit)
async def cancel(request: Request, payload: AccountRecoveryRequest, db: DBSession) -> AuthTokens:
    result = await account_deletion.cancel_deletion(
        db,
        payload.authorization_token,
        client_type=payload.client_type,
        device_label=payload.device_label,
        user_agent=request.headers.get("user-agent"),
    )

    return await _auth_tokens_response(db, result)


@router.get("/policy")
async def retention_policy() -> dict[str, str]:
    return {"retention_notice": get_settings().account_deletion_retention_notice}
