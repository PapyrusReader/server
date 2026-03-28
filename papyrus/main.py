"""FastAPI application factory and configuration."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import HTTPException as FastAPIHTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from papyrus.api.routes import api_router, include_debug_routers
from papyrus.config import get_settings
from papyrus.core.exceptions import AppError

settings = get_settings()
limiter = Limiter(key_func=get_remote_address)


def configure_logging() -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan events."""
    # Startup
    yield
    # Shutdown


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    configure_logging()
    auth_base_path = f"{settings.api_prefix}/auth" if settings.api_prefix else "/auth"

    app = FastAPI(
        title="Papyrus Server API",
        version="1.0.0",
        description=f"""
REST API for Papyrus - a cross-platform book management application.

## Overview

The Papyrus Server handles metadata storage and synchronization for the Papyrus
e-book reader application. It provides endpoints for:

- **Authentication**: User registration, login, OAuth, and session management
- **Books**: CRUD operations for book metadata and file references
- **Organization**: Shelves, tags, and series management
- **Annotations**: Highlights, notes, and bookmarks
- **Progress**: Reading sessions and statistics
- **Goals**: Reading goal tracking
- **Sync**: Cross-device synchronization
- **Storage**: File storage backend configuration
- **Files**: File upload/download (when server is file backend)

## Authentication

Most endpoints require authentication via JWT Bearer token. Obtain tokens
through the `{auth_base_path}/register`, `{auth_base_path}/login`, or the
browser-based `{auth_base_path}/oauth/google/start` flow.

```
Authorization: Bearer <access_token>
```

Access tokens expire after `{settings.access_token_expire_minutes}` minutes.
Use the refresh token to obtain new access tokens via `{auth_base_path}/refresh`.

## Rate Limiting

Rate limits are enforced per user:

| Endpoint Category | Limit |
|-------------------|-------|
| Authentication | 5 requests/minute |
| General API | 100 requests/minute |
| File uploads | 10 requests/minute |
| Batch operations | 20 requests/minute |
""",
        contact={
            "name": "Papyrus Support",
            "url": "https://github.com/Eoic/Papyrus",
        },
        license_info={
            "name": "AGPL-3.0",
            "url": "https://www.gnu.org/licenses/agpl-3.0.html",
        },
        lifespan=lifespan,
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(AppError)
    async def app_exception_handler(request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                    "details": exc.details,
                }
            },
        )

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, (FastAPIHTTPException, RequestValidationError)):
            raise exc

        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "INTERNAL_ERROR",
                    "message": "An unexpected error occurred",
                }
            },
        )

    app.include_router(api_router, prefix=settings.api_prefix)

    if settings.debug:
        include_debug_routers(app)

    @app.get("/", tags=["Index"])
    async def index(request: Request) -> dict[str, object]:
        """Return the available page-style endpoints for this server."""
        pages: list[dict[str, str]] = []

        if app.docs_url is not None:
            pages.append({"name": "docs", "path": app.docs_url})

        if app.redoc_url is not None:
            pages.append({"name": "redoc", "path": app.redoc_url})

        if app.openapi_url is not None:
            pages.append({"name": "openapi", "path": app.openapi_url})

        if any(route.path == "/__dev/auth-sandbox" for route in request.app.routes):
            pages.append({"name": "auth_sandbox", "path": "/__dev/auth-sandbox"})

        if any(route.path == "/__dev/powersync-sandbox" for route in request.app.routes):
            pages.append({"name": "powersync_sandbox", "path": "/__dev/powersync-sandbox"})

        return {
            "name": "Papyrus Server API",
            "pages": pages,
        }

    @app.get("/health", tags=["Health"])
    async def health_check() -> dict[str, str]:
        """Check API health status."""
        return {"status": "healthy"}

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run(
        "papyrus.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
    )


if __name__ == "__main__":
    run()
