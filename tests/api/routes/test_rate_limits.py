"""Rate-limit identity and retry contracts through the authenticated routes."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Response
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.config import get_settings
from papyrus.core.security import create_access_token, generate_opaque_token, hash_opaque_token
from papyrus.models import AuthSession, SyncBook, User


async def _session_headers(db: AsyncSession, user_id: UUID) -> dict[str, str]:
    session = AuthSession(
        user_id=user_id,
        refresh_token_hash=hash_opaque_token(generate_opaque_token()),
        client_type="test",
        expires_at=datetime.now(UTC) + timedelta(days=1),
        last_seen_at=datetime.now(UTC),
    )

    db.add(session)
    await db.commit()
    token = create_access_token({"sub": str(user_id), "sid": str(session.session_id)})
    return {"Authorization": f"Bearer {token}"}


async def _upload(client: AsyncClient, path: str, headers: dict[str, str], book_id: UUID) -> Response:
    if path == "/v1/sync/powersync-upload":
        return await client.post(path, headers=headers, json={"batch": []})

    return await client.post(
        path,
        headers=headers,
        data={"book_id": str(book_id), "kind": "cover_image"},
        files={"file": ("cover.png", b"cover", "image/png")},
    )


@pytest.mark.parametrize(
    ("path", "limit_setting", "success_status"),
    [
        ("/v1/sync/powersync-upload", "rate_limit_batch", 200),
        ("/v1/media", "rate_limit_upload", 201),
    ],
)
async def test_upload_limits_are_per_validated_user_across_sessions(
    client: AsyncClient,
    auth_user: dict[str, str],
    auth_headers: dict[str, str],
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    path: str,
    limit_setting: str,
    success_status: int,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, limit_setting, 1)
    monkeypatch.setattr(settings, "media_storage_root", str(tmp_path))
    monkeypatch.setattr(settings, "cors_origins", ["https://app.papyrus.test"])
    other_user = User(display_name="Other", primary_email="rate-other@example.com", primary_email_verified=True)
    db_session.add(other_user)
    await db_session.flush()
    owner_id = UUID(auth_user["user_id"])
    other_id = other_user.user_id
    owner_book = SyncBook(book_id=uuid4(), owner_user_id=owner_id, title="Owner")
    other_book = SyncBook(book_id=uuid4(), owner_user_id=other_id, title="Other")
    db_session.add_all([owner_book, other_book])
    await db_session.commit()
    owner_book_id, other_book_id = owner_book.book_id, other_book.book_id
    second_session_headers = await _session_headers(db_session, owner_id)
    other_headers = await _session_headers(db_session, other_id)
    first = await _upload(client, path, auth_headers, owner_book_id)
    assert first.status_code == success_status
    second_session_headers["X-Forwarded-For"] = "198.51.100.2"
    second_session_headers["Origin"] = "https://app.papyrus.test"
    limited = await _upload(client, path, second_session_headers, owner_book_id)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "RATE_LIMIT_EXCEEDED"
    retry_after = int(limited.headers["Retry-After"])
    assert 1 <= retry_after <= 60
    assert limited.json()["error"]["details"]["retry_after"] == retry_after
    assert "Retry-After" in limited.headers["access-control-expose-headers"]
    other = await _upload(client, path, other_headers, other_book_id)
    assert other.status_code == success_status
    assert (await _upload(client, path, other_headers, other_book_id)).status_code == 429


async def test_invalid_session_cannot_consume_another_users_upload_allowance(
    client: AsyncClient,
    auth_user: dict[str, str],
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_batch", 1)
    forged_session = create_access_token({"sub": auth_user["user_id"], "sid": str(uuid4())})

    response = await client.post(
        "/v1/sync/powersync-upload",
        headers={"Authorization": f"Bearer {forged_session}"},
        json={"batch": []},
    )

    assert response.status_code == 401
    valid = await client.post("/v1/sync/powersync-upload", headers=auth_headers, json={"batch": []})
    assert valid.status_code == 200
