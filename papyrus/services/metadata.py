import json
import re
from collections import OrderedDict
from threading import BoundedSemaphore, Lock
from time import monotonic
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import ValidationError

from papyrus.config import get_settings
from papyrus.core.exceptions import AppError, ServiceUnavailableError
from papyrus.schemas.metadata import MetadataPage, MetadataResult, MetadataSearch

PAGE_SIZE = 10
CACHE_SECONDS = 300
CACHE_ENTRIES = 256
MAX_RESPONSE_BYTES = 1_048_576
_cache: OrderedDict[tuple[str, int, bool], tuple[float, MetadataPage]] = OrderedDict()
_cache_lock = Lock()
_connections = BoundedSemaphore(4)


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


def _parse_volume(item: dict[str, Any]) -> MetadataResult:
    info = item.get("volumeInfo") or {}
    images = info.get("imageLinks") or {}
    cover = (
        images.get("extraLarge")
        or images.get("large")
        or images.get("medium")
        or images.get("small")
        or images.get("thumbnail")
        or images.get("smallThumbnail")
    )

    if isinstance(cover, str) and cover.startswith("http://"):
        cover = "https://" + cover[7:]

    identifiers = {entry.get("type"): entry.get("identifier") for entry in info.get("industryIdentifiers", [])}

    return MetadataResult(
        title=info.get("title"),
        subtitle=info.get("subtitle"),
        authors=info.get("authors"),
        publisher=info.get("publisher"),
        published_date=info.get("publishedDate"),
        description=info.get("description"),
        cover_url=cover,
        language=info.get("language"),
        isbn=identifiers.get("ISBN_10"),
        isbn13=identifiers.get("ISBN_13"),
        page_count=info.get("pageCount"),
    )


def search_google_books(search: MetadataSearch) -> MetadataPage:
    """Cache public metadata only; credentials and failed responses never enter the cache."""
    secret = get_settings().google_books_api_key

    if secret is None or not secret.get_secret_value().strip():
        raise ServiceUnavailableError("Google Books is not configured on this server. Try Open Library.")

    query = re.sub(r"[-\s]", "", search.q) if search.isbn else search.q

    if not query:
        raise AppError("Enter a search query or ISBN.", code="INVALID_METADATA_QUERY", status_code=422)

    cache_key = (query, search.page, search.isbn)

    with _cache_lock:
        cached = _cache.get(cache_key)

        if cached is not None and cached[0] > monotonic():
            _cache.move_to_end(cache_key)
            return cached[1].model_copy(deep=True)

        _cache.pop(cache_key, None)

    if not _connections.acquire(blocking=False):
        raise ServiceUnavailableError("Metadata search is busy. Please try again shortly.")

    try:
        result = _fetch_google_books(query, search, secret.get_secret_value().strip())
    finally:
        _connections.release()

    with _cache_lock:
        _cache[cache_key] = (monotonic() + CACHE_SECONDS, result)
        _cache.move_to_end(cache_key)

        while len(_cache) > CACHE_ENTRIES:
            _cache.popitem(last=False)

    return result.model_copy(deep=True)


def _fetch_google_books(query: str, search: MetadataSearch, key: str) -> MetadataPage:
    parameters = urlencode(
        {
            "q": f"isbn:{query}" if search.isbn else query,
            "startIndex": (search.page - 1) * PAGE_SIZE,
            "maxResults": PAGE_SIZE,
            "key": key,
        }
    )

    request = Request(
        f"https://www.googleapis.com/books/v1/volumes?{parameters}",
        headers={"Accept": "application/json", "User-Agent": "Papyrus"},
    )

    try:
        with build_opener(NoRedirects()).open(request, timeout=15) as response:
            body = response.read(MAX_RESPONSE_BYTES + 1)

        if len(body) > MAX_RESPONSE_BYTES:
            raise ValueError("Response too large")

        data = json.loads(body)
        items = data.get("items") or []

        if not isinstance(items, list) or len(items) > PAGE_SIZE:
            raise ValueError("Invalid results")

        return MetadataPage(
            results=[_parse_volume(item) for item in items],
            total_results=data.get("totalItems"),
        )
    except HTTPError as error:
        error.close()

        if error.code == 429:
            raise AppError(
                "Google Books search quota is temporarily exhausted. Try Open Library or retry later.",
                code="METADATA_QUOTA_EXCEEDED",
                status_code=503,
            ) from None

        raise AppError(
            "Google Books is temporarily unavailable.", code="METADATA_UPSTREAM_ERROR", status_code=502
        ) from None
    except (TimeoutError, URLError, OSError):
        raise AppError(
            "Google Books did not respond. Please try again.", code="METADATA_TIMEOUT", status_code=504
        ) from None
    except (ValueError, TypeError, AttributeError, ValidationError):
        raise AppError(
            "Google Books returned an invalid response.", code="METADATA_INVALID_RESPONSE", status_code=502
        ) from None
