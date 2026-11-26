from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from papyrus.core.exceptions import NotFoundError
from papyrus.models import AuthSession, MediaAsset, SyncBook, User
from papyrus.services.admin import set_role


async def test_directory_requires_current_admin_role(client, db_session, auth_user, auth_headers):
    path = "/v1/admin/users"
    user_id = UUID(auth_user["user_id"])
    assert (await client.get(path)).status_code == 401
    profile = await client.patch("/v1/users/me", headers=auth_headers, json={"display_name": "Reader", "role": "admin"})
    assert profile.json()["role"] == "user"
    assert (await client.get(path, headers=auth_headers)).status_code == 403

    with pytest.raises(NotFoundError):
        await set_role(db_session, uuid4(), "admin")

    await set_role(db_session, user_id, "admin")
    assert (await client.get(path, headers=auth_headers)).status_code == 200
    await set_role(db_session, user_id, "user")
    assert (await client.get(path, headers=auth_headers)).status_code == 403


async def test_directory_counts_storage_and_session_creation(client, db_session, auth_user, auth_headers):
    owner = UUID(auth_user["user_id"])
    await set_role(db_session, owner, "admin")
    digital = SyncBook(owner_user_id=owner, title="Digital")
    physical = SyncBook(owner_user_id=owner, title="Physical", is_physical=True)
    db_session.add_all([digital, physical])
    await db_session.flush()

    for kind, size in [("book_file", 800), ("cover_image", 200)]:
        db_session.add(
            MediaAsset(
                owner_user_id=owner,
                book_id=digital.book_id,
                kind=kind,
                size_bytes=size,
                original_filename="test",
                extension="test",
                content_type="test",
                sha256="test",
                storage_path="test",
            )
        )

    now = datetime.now(UTC)
    sign_in = now - timedelta(days=2)
    session = await db_session.get(AuthSession, UUID(auth_user["session_id"]))
    session.created_at = now - timedelta(days=4)

    db_session.add(
        AuthSession(user_id=owner, refresh_token_hash="previous", created_at=sign_in, revoked_at=now, expires_at=now)
    )

    awaiting = User(display_name="Awaiting", created_at=now)
    disabled = User(display_name="Disabled", disabled_at=now, created_at=now)
    pending = User(display_name="Pending", disabled_at=now, deletion_due_at=now, created_at=now)
    db_session.add_all([awaiting, disabled, pending])
    await db_session.commit()
    db_session.add(SyncBook(owner_user_id=awaiting.user_id, title="Catalog entry"))
    await db_session.commit()

    refreshed = await client.post("/v1/auth/refresh", json={"refresh_token": auth_user["refresh_token"]})
    assert refreshed.json()["user"]["role"] == "admin"
    usage = (await client.get("/v1/media/usage", headers=auth_headers)).json()
    response = await client.get("/v1/admin/users", headers=auth_headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "private, no-store"
    directory = response.json()
    assert directory["quota_bytes"] == usage["quota_bytes"]
    by_name = {user["display_name"]: user for user in directory["users"]}
    admin = by_name["Example User"]
    assert admin["book_count"] == 2
    assert admin["used_bytes"] == usage["used_bytes"] == 1000
    assert datetime.fromisoformat(admin["last_sign_in_at"]) == sign_in
    assert admin["status"] == "active"
    assert by_name["Awaiting"]["status"] == "unverified"
    assert by_name["Awaiting"]["book_count"] == 1
    assert by_name["Awaiting"]["used_bytes"] == 0
    assert by_name["Disabled"]["status"] == "disabled"
    assert by_name["Pending"]["status"] == "pending_deletion"
    assert by_name["Pending"]["book_count"] == 0
    assert by_name["Pending"]["last_sign_in_at"] is None
    expected_ids = [str(awaiting.user_id), str(disabled.user_id), str(owner), str(pending.user_id)]

    for page in (1, 2):
        response = await client.get("/v1/admin/users", params={"page": page, "limit": 2}, headers=auth_headers)
        data = response.json()
        assert [user["user_id"] for user in data["users"]] == expected_ids[(page - 1) * 2 : page * 2]
        assert data["pagination"] == {
            "page": page,
            "limit": 2,
            "total": 4,
            "total_pages": 2,
            "has_next": page == 1,
            "has_prev": page == 2,
        }

    assert (await client.get("/v1/admin/users?limit=101", headers=auth_headers)).status_code == 422


async def test_directory_sorts_before_pagination(client, db_session, auth_headers, auth_user):
    owner = UUID(auth_user["user_id"])
    await set_role(db_session, owner, "admin")
    now = datetime.now(UTC)
    alpha = User(user_id=UUID(int=1), display_name="alpha", created_at=now + timedelta(days=1))
    zeta = User(
        user_id=UUID(int=2),
        display_name="Zeta",
        primary_email="a@example.com",
        disabled_at=now,
        created_at=now - timedelta(days=1),
    )

    db_session.add_all([alpha, zeta])
    await db_session.flush()

    for user, count, size in [(alpha, 4, 5), (zeta, 2, 3)]:
        books = [SyncBook(owner_user_id=user.user_id, title=str(index)) for index in range(count)]
        db_session.add_all(books)
        await db_session.flush()

        db_session.add(
            MediaAsset(
                owner_user_id=user.user_id,
                book_id=books[0].book_id,
                kind="book_file",
                size_bytes=size,
                original_filename="test",
                extension="test",
                content_type="test",
                sha256="test",
                storage_path="test",
            )
        )

    await db_session.commit()
    admin = "Example User"

    for field, ascending, descending in [
        ("display_name", ["alpha", admin, "Zeta"], ["Zeta", admin, "alpha"]),
        ("email", ["Zeta", admin, "alpha"], [admin, "Zeta", "alpha"]),
        ("role", [admin, "alpha", "Zeta"], ["alpha", "Zeta", admin]),
        ("status", [admin, "alpha", "Zeta"], ["Zeta", "alpha", admin]),
        ("created_at", ["Zeta", admin, "alpha"], ["alpha", admin, "Zeta"]),
        ("last_sign_in_at", [admin, "alpha", "Zeta"], [admin, "alpha", "Zeta"]),
        ("book_count", [admin, "Zeta", "alpha"], ["alpha", "Zeta", admin]),
        ("used_bytes", [admin, "Zeta", "alpha"], ["alpha", "Zeta", admin]),
        ("average_bytes", ["alpha", "Zeta", admin], ["Zeta", "alpha", admin]),
    ]:
        for reverse, expected in [(False, ascending), (True, descending)]:
            names = []

            for page in (1, 2):
                response = await client.get(
                    "/v1/admin/users",
                    params={"sort_by": field, "descending": reverse, "page": page, "limit": 2},
                    headers=auth_headers,
                )

                assert response.status_code == 200
                names.extend(row["display_name"] for row in response.json()["users"])

            assert names == expected, (field, reverse)

    assert (await client.get("/v1/admin/users?sort_by=unknown", headers=auth_headers)).status_code == 422
