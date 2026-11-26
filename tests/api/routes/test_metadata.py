import io
import json
from datetime import UTC, datetime
from unittest.mock import Mock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from fastapi import Request
from pydantic import SecretStr

from papyrus.api.deps import get_current_user_id
from papyrus.config import get_settings
from papyrus.models import AuthSession
from papyrus.services import metadata

PREFIX = f"{get_settings().api_prefix}/metadata/google-books"
SECRET = "server-only-test-key"
VOLUME = {
    "volumeInfo": {
        "title": "Alice",
        "authors": ["Lewis Carroll"],
        "publishedDate": "1865",
        "pageCount": 200,
        "imageLinks": {"thumbnail": "http://books.google.com/cover"},
        "industryIdentifiers": [{"type": "ISBN_13", "identifier": "9780141439518"}],
    },
    "private_upstream_field": "must-not-leak",
}


@pytest.fixture(autouse=True)
def clear_cache():
    metadata._cache.clear()
    yield
    metadata._cache.clear()


@pytest.fixture
def upstream(monkeypatch):
    monkeypatch.setattr(get_settings(), "google_books_api_key", SecretStr(SECRET))
    connection = Mock(
        side_effect=lambda *args, **kwargs: io.BytesIO(json.dumps({"totalItems": 24, "items": [VOLUME]}).encode())
    )
    monkeypatch.setattr(metadata, "build_opener", Mock(return_value=Mock(open=connection)))
    return connection


@pytest.mark.parametrize("isbn", [False, True])
async def test_guest_pages_use_server_key_and_return_only_metadata(public_client, upstream, isbn):
    query = "978-0 141439518" if isbn else "Alice & Wonderland"

    for page in [1, 2, 3]:
        response = await public_client.get(
            PREFIX,
            params={"q": query, "page": page, "isbn": str(isbn).lower()},
            headers={"Authorization": "Bearer untrusted", "Cookie": "private=value"},
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        payload = response.json()
        assert payload["total_results"] == 24
        assert payload["results"][0]["isbn13"] == "9780141439518"
        assert payload["results"][0]["cover_url"] == "https://books.google.com/cover"
        assert SECRET not in response.text
        assert "private_upstream_field" not in response.text
        request = upstream.call_args.args[0]
        uri = urlsplit(request.full_url)
        assert uri.scheme == "https" and uri.netloc == "www.googleapis.com"
        assert parse_qs(uri.query) == {
            "q": ["isbn:9780141439518" if isbn else query],
            "startIndex": [str((page - 1) * 10)],
            "maxResults": ["10"],
            "key": [SECRET],
        }
        assert request.get_header("Authorization") is None
        assert request.get_header("Cookie") is None


async def test_cache_expires_and_pages_and_modes_are_independent(public_client, upstream, monkeypatch):
    clock = [1.0]
    monkeypatch.setattr(metadata, "monotonic", lambda: clock[0])

    for query in ["Alice", " Alice "]:
        assert (await public_client.get(PREFIX, params={"q": query})).status_code == 200

    assert upstream.call_count == 1
    await public_client.get(PREFIX, params={"q": "Alice", "page": 2})
    await public_client.get(PREFIX, params={"q": "Alice", "isbn": "true"})
    assert upstream.call_count == 3
    clock[0] += 301
    await public_client.get(PREFIX, params={"q": "Alice"})
    assert upstream.call_count == 4


@pytest.mark.parametrize(
    "params",
    [
        {"q": " "},
        {"q": "a" * 301},
        {"q": "a", "page": 0},
        {"q": "a", "page": 10001},
        {"q": "a", "key": "client-key"},
        {"q": "---", "isbn": "true"},
    ],
)
async def test_invalid_query_never_contacts_google(public_client, upstream, params):
    assert (await public_client.get(PREFIX, params=params)).status_code == 422
    upstream.assert_not_called()


async def test_missing_key_disables_google_even_when_cached(public_client, upstream, monkeypatch):
    await public_client.get(PREFIX, params={"q": "Alice"})
    monkeypatch.setattr(get_settings(), "google_books_api_key", SecretStr(""))
    response = await public_client.get(PREFIX, params={"q": "Alice"})
    assert response.status_code == 503
    assert upstream.call_count == 1
    assert "Open Library" in response.text


@pytest.mark.parametrize(
    "failure, status",
    [
        (HTTPError("https://google.invalid/?key=" + SECRET, 429, SECRET, {}, io.BytesIO(SECRET.encode())), 503),
        (HTTPError("https://google.invalid/?key=" + SECRET, 403, SECRET, {}, io.BytesIO(SECRET.encode())), 502),
        (URLError(SECRET), 504),
        (TimeoutError(SECRET), 504),
    ],
)
async def test_failures_are_sanitized_and_not_cached(public_client, upstream, failure, status):
    upstream.side_effect = failure
    response = await public_client.get(PREFIX, params={"q": "Alice"})
    assert response.status_code == status
    assert SECRET not in response.text
    upstream.side_effect = lambda *args, **kwargs: io.BytesIO(b'{"totalItems":0}')
    response = await public_client.get(PREFIX, params={"q": "Alice"})
    assert response.status_code == 200
    assert response.json() == {"results": [], "total_results": 0}
    assert upstream.call_count == 2


@pytest.mark.parametrize(
    "body", [b"not json", b"[]", b'{"items":[{"volumeInfo":{"authors":5}}]}', b"x" * (metadata.MAX_RESPONSE_BYTES + 1)]
)
async def test_invalid_upstream_body_is_rejected(public_client, upstream, body):
    upstream.side_effect = lambda *args, **kwargs: io.BytesIO(body)
    assert (await public_client.get(PREFIX, params={"q": "Alice"})).status_code == 502


async def test_guest_limit_cannot_be_bypassed_with_bearer_header(public_client, upstream):
    for _ in range(10):
        assert (await public_client.get(PREFIX, params={"q": "Alice"})).status_code == 200

    response = await public_client.get(PREFIX, params={"q": "Alice"}, headers={"Authorization": "Bearer forged"})
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) > 0
    assert upstream.call_count == 1


async def test_authenticated_endpoint_rejects_missing_or_invalid_credentials(public_client, upstream):
    for headers in [{}, {"Authorization": "Bearer forged"}]:
        response = await public_client.get(PREFIX + "/authenticated", params={"q": "Alice"}, headers=headers)
        assert response.status_code in {401, 403}

    upstream.assert_not_called()


async def validated_user(request: Request):
    request.state.authenticated_user_id = UUID(int=1)
    return UUID(int=1)


async def test_authenticated_limit_uses_validated_identity(public_client, upstream):
    public_client._transport.app.dependency_overrides[get_current_user_id] = validated_user

    for _ in range(30):
        response = await public_client.get(PREFIX + "/authenticated", params={"q": "Alice"})
        assert response.status_code == 200

    assert (await public_client.get(PREFIX + "/authenticated", params={"q": "Alice"})).status_code == 429
    assert upstream.call_count == 1


async def test_daily_budget_is_shared_between_guest_and_authenticated(public_client, upstream, monkeypatch):
    monkeypatch.setattr(get_settings(), "metadata_daily_request_limit", 2)
    public_client._transport.app.dependency_overrides[get_current_user_id] = validated_user
    assert (await public_client.get(PREFIX, params={"q": "Alice"})).status_code == 200
    assert (await public_client.get(PREFIX + "/authenticated", params={"q": "Alice"})).status_code == 200
    assert (await public_client.get(PREFIX, params={"q": "Alice"})).status_code == 429
    assert upstream.call_count == 1


async def test_real_session_must_remain_valid_even_for_cached_results(
    client, auth_headers, auth_user, db_session, upstream
):
    response = await client.get(PREFIX + "/authenticated", params={"q": "Alice"}, headers=auth_headers)
    assert response.status_code == 200
    session = await db_session.get(AuthSession, UUID(auth_user["session_id"]))
    session.revoked_at = datetime.now(UTC)
    await db_session.commit()
    response = await client.get(PREFIX + "/authenticated", params={"q": "Alice"}, headers=auth_headers)
    assert response.status_code == 401
    assert upstream.call_count == 1


def test_cache_capacity_is_bounded(upstream, monkeypatch):
    monkeypatch.setattr(metadata, "CACHE_ENTRIES", 2)

    for query in ["Alice", "Dune", "Hobbit", "Alice"]:
        metadata.search_google_books(metadata.MetadataSearch(q=query))

    assert len(metadata._cache) == 2
    assert upstream.call_count == 4


def test_busy_upstream_returns_without_starting_another_connection(upstream, monkeypatch):
    semaphore = Mock()
    semaphore.acquire.return_value = False
    monkeypatch.setattr(metadata, "_connections", semaphore)

    with pytest.raises(metadata.ServiceUnavailableError):
        metadata.search_google_books(metadata.MetadataSearch(q="Alice"))

    upstream.assert_not_called()


@pytest.mark.parametrize("size", ["extraLarge", "large", "medium", "small", "thumbnail", "smallThumbnail"])
async def test_each_google_cover_size_is_supported(public_client, upstream, size):
    volume = {"volumeInfo": {"title": "Alice", "imageLinks": {size: "http://books.google.com/cover"}}}
    upstream.side_effect = lambda *args, **kwargs: io.BytesIO(json.dumps({"items": [volume]}).encode())
    response = await public_client.get(PREFIX, params={"q": "Alice"})
    assert response.status_code == 200
    assert response.json()["results"][0]["cover_url"] == "https://books.google.com/cover"
