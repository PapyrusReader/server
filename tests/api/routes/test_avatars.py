import base64
from datetime import UTC, datetime, timedelta
from io import BytesIO
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import UploadFile
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from papyrus.models import User
from papyrus.services import account_deletion, avatars

PHOTO = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jP1kAAAAASUVORK5CYII=")


@pytest.fixture(autouse=True)
def photo_storage(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr("papyrus.main.settings.media_storage_root", str(tmp_path))


async def upload(client: AsyncClient, headers: dict[str, str], contents: bytes = PHOTO):
    return await client.post(
        "/v1/users/me/avatar",
        headers=headers,
        data={"display_name": "Photo Reader"},
        files={"file": ("photo.png", contents, "image/png")},
    )


async def test_photo_survives_login_and_replacement_removes_previous_file(
    client: AsyncClient, auth_headers: dict[str, str], auth_user: dict[str, str], tmp_path: Path
) -> None:
    saved = await upload(client, auth_headers)
    assert saved.status_code == 200
    first_url = saved.json()["avatar_url"]
    first_path = avatars.avatar_path(UUID(auth_user["user_id"]), first_url)
    assert first_path is not None and first_path.read_bytes() == PHOTO
    downloaded = await client.get(f"/v1{first_url}", headers=auth_headers)
    assert downloaded.status_code == 200
    assert downloaded.content == PHOTO
    assert downloaded.headers["content-type"] == "image/png"
    assert downloaded.headers["cache-control"] == "private, no-store"
    login = await client.post(
        "/v1/auth/login", json={"email": "user@example.com", "password": auth_user["password"], "client_type": "mobile"}
    )

    assert login.status_code == 200
    assert login.json()["user"]["avatar_url"] == first_url
    assert login.json()["user"]["display_name"] == "Photo Reader"
    replaced = await upload(client, auth_headers)
    assert replaced.status_code == 200
    assert replaced.json()["avatar_url"] != first_url
    assert not first_path.exists()
    assert len(list(tmp_path.rglob("avatars/*"))) == 1
    assert (await client.get(f"/v1{first_url}", headers=auth_headers)).status_code == 404


async def test_photo_requires_authentication_and_owner(
    client: AsyncClient, auth_headers: dict[str, str], auth_user: dict[str, str], db_session: AsyncSession
) -> None:
    saved = await upload(client, auth_headers)
    url = saved.json()["avatar_url"]
    assert (await client.get(f"/v1{url}")).status_code == 401
    assert (await upload(client, {})).status_code == 401
    other = await client.post(
        "/v1/auth/register",
        json={"email": "other@example.com", "password": "SecureP@ss123", "display_name": "Other"},
    )

    assert other.status_code == 201
    other_headers = {"Authorization": f"Bearer {other.json()['access_token']}"}
    assert (await client.get(f"/v1{url}", headers=other_headers)).status_code == 404
    forged = await client.patch("/v1/users/me", headers=other_headers, json={"avatar_url": url})
    assert forged.status_code == 400
    user = await db_session.get(User, UUID(auth_user["user_id"]))
    assert user is not None
    user.disabled_at = datetime.now(UTC)
    await db_session.commit()
    assert (await client.get(f"/v1{url}", headers=auth_headers)).status_code == 403
    assert (await upload(client, auth_headers)).status_code == 403


@pytest.mark.parametrize(
    "contents",
    [b"", b"<svg></svg>", PHOTO[:-8], b"x" * (avatars.MAX_AVATAR_BYTES + 1)],
    ids=["empty", "svg", "truncated", "oversize"],
)
async def test_invalid_upload_keeps_previous_profile(
    client: AsyncClient, auth_headers: dict[str, str], tmp_path: Path, contents: bytes
) -> None:
    saved = await upload(client, auth_headers)
    rejected = await upload(client, auth_headers, contents)
    assert rejected.status_code == 400
    current = await client.get("/v1/users/me", headers=auth_headers)
    assert current.json() == saved.json()
    assert len(list(tmp_path.rglob("avatars/*"))) == 1


async def test_commit_failure_retains_photo_and_name(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved = await upload(client, auth_headers)

    async def fail_commit() -> None:
        raise RuntimeError("Database unavailable")

    with monkeypatch.context() as patch:
        patch.setattr(db_session, "commit", fail_commit)

        with pytest.raises(RuntimeError, match="Database unavailable"):
            await avatars.upload_avatar(
                db_session,
                UUID(auth_user["user_id"]),
                file=UploadFile(filename="new.png", file=BytesIO(PHOTO)),
                display_name="Unsaved name",
            )

    current = await client.get("/v1/users/me", headers=auth_headers)
    assert current.json() == saved.json()
    assert len(list(tmp_path.rglob("avatars/*"))) == 1


async def test_permanent_account_cleanup_removes_photo_but_preserves_other_owners(
    client: AsyncClient,
    auth_headers: dict[str, str],
    auth_user: dict[str, str],
    db_session: AsyncSession,
    tmp_path: Path,
) -> None:
    saved = await upload(client, auth_headers)
    path = avatars.avatar_path(UUID(auth_user["user_id"]), saved.json()["avatar_url"])
    other_path = tmp_path / str(uuid4()) / "avatars" / str(uuid4())
    other_path.parent.mkdir(parents=True)
    other_path.write_bytes(PHOTO)
    user = await db_session.get(User, UUID(auth_user["user_id"]))
    assert user is not None
    user.disabled_at = datetime.now(UTC) - timedelta(days=31)
    user.deletion_due_at = datetime.now(UTC) - timedelta(days=1)
    await db_session.commit()
    assert await account_deletion.purge_due_accounts(db_session) == 1
    assert await account_deletion.cleanup_deleted_media(db_session) == 1
    assert path is not None and not path.exists()
    assert other_path.read_bytes() == PHOTO
