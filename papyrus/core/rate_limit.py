"""Shared application rate limiter."""

import math
import time

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)


def get_authenticated_user_key(request: Request) -> str:
    """Use only the identity established by the validated session dependency."""
    return f"user:{request.state.authenticated_user_id}"


def rate_limit_exceeded_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RateLimitExceeded)
    limit, arguments = request.state.view_rate_limit
    request_limiter: Limiter = request.app.state.limiter
    reset_at, _ = request_limiter.limiter.get_window_stats(limit, *arguments)
    retry_after = max(1, math.ceil(reset_at - time.time()))

    return JSONResponse(
        status_code=429,
        content={
            "error": {
                "code": "RATE_LIMIT_EXCEEDED",
                "message": "Too many requests. Please try again later.",
                "details": {"retry_after": retry_after},
            },
        },
        headers={"Retry-After": str(retry_after)},
    )
