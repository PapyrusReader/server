from typing import Annotated

from fastapi import APIRouter, Query, Request, Response

from papyrus.api.deps import CurrentUserId
from papyrus.config import get_settings
from papyrus.core.rate_limit import get_authenticated_user_key, limiter
from papyrus.schemas.metadata import MetadataPage, MetadataSearch
from papyrus.services.metadata import search_google_books

router = APIRouter()
SearchQuery = Annotated[MetadataSearch, Query()]


@router.get("/google-books", response_model=MetadataPage)
@limiter.limit("10/minute;60/hour")
@limiter.shared_limit(
    lambda: f"{get_settings().metadata_daily_request_limit}/day",
    scope="google-books",
    key_func=lambda: "google-books",
)
def guest_search(request: Request, response: Response, search: SearchQuery) -> MetadataPage:
    response.headers["Cache-Control"] = "no-store"
    return search_google_books(search)


@router.get("/google-books/authenticated", response_model=MetadataPage)
@limiter.limit("30/minute;300/hour", key_func=get_authenticated_user_key)
@limiter.shared_limit(
    lambda: f"{get_settings().metadata_daily_request_limit}/day",
    scope="google-books",
    key_func=lambda: "google-books",
)
def authenticated_search(
    request: Request,
    response: Response,
    user_id: CurrentUserId,
    search: SearchQuery,
) -> MetadataPage:
    response.headers["Cache-Control"] = "no-store"
    return search_google_books(search)
