"""Bookmark queue contracts and offline deletion regressions."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from papyrus.models import SyncBook, User
from tests.api.routes.test_library_sync import mutation, upload


async def test_bookmark_roundtrip_retry_partial_update_and_null_clear(client, auth_headers, auth_user, db_session):
    book_id, bookmark_id = uuid4(), uuid4()
    created_at = "2025-01-02T03:04:05Z"
    bookmark = mutation(
        "bookmarks",
        bookmark_id,
        {
            "book_id": str(book_id),
            "position": 0.25,
            "page_number": 12,
            "chapter_title": "Chapter",
            "note": "Remember",
            "color_hex": "#ABCDEF",
            "created_at": created_at,
            "owner_user_id": str(uuid4()),
            "updated_at": created_at,
        },
    )
    response = await upload(client, auth_headers, mutation("books", book_id, {"title": "Book"}), bookmark)
    assert response.status_code == 200, response.text
    assert (await upload(client, auth_headers, bookmark)).status_code == 200
    response = await upload(
        client,
        auth_headers,
        mutation(
            "bookmarks",
            bookmark_id,
            {
                "note": None,
                "page_number": None,
                "chapter_title": None,
            },
            "PATCH",
        ),
    )
    assert response.status_code == 200, response.text
    row = (await db_session.execute(text("SELECT * FROM bookmarks"))).mappings().one()
    assert row["position"] == 0.25
    assert row["color_hex"] == "#ABCDEF"
    assert row["note"] is row["page_number"] is row["chapter_title"] is None
    assert row["owner_user_id"] == UUID(auth_user["user_id"])
    assert row["created_at"] == datetime(2025, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert row["updated_at"] > row["created_at"]


async def test_bookmark_defaults_and_missing_patch(client, auth_headers, db_session):
    book_id, bookmark_id = uuid4(), uuid4()
    response = await upload(
        client,
        auth_headers,
        mutation("books", book_id, {"title": "Book"}),
        mutation("bookmarks", bookmark_id, {"book_id": str(book_id)}),
        mutation("bookmarks", uuid4(), {"note": "Missing"}, "PATCH"),
    )
    assert response.status_code == 200, response.text
    row = (await db_session.execute(text("SELECT position, color_hex FROM bookmarks"))).one()
    assert row == (0.0, "#FF5722")


@pytest.mark.parametrize("delete_parent", [False, True])
async def test_bookmark_deletion_wins_over_stale_writes(client, auth_headers, db_session, delete_parent):
    book_id, bookmark_id = uuid4(), uuid4()
    bookmark = mutation("bookmarks", bookmark_id, {"book_id": str(book_id), "position": 0.5})
    assert (
        await upload(client, auth_headers, mutation("books", book_id, {"title": "Book"}), bookmark)
    ).status_code == 200
    deletion = mutation(
        "books" if delete_parent else "bookmarks", book_id if delete_parent else bookmark_id, op="DELETE"
    )
    for _ in range(2):
        assert (await upload(client, auth_headers, deletion)).status_code == 200
    stale = [bookmark, mutation("bookmarks", bookmark_id, {"note": "Stale"}, "PATCH")]
    if delete_parent:
        stale.append(mutation("bookmarks", uuid4(), {"book_id": str(book_id), "position": 0.7}))
    assert (await upload(client, auth_headers, *stale)).status_code == 200
    assert (await db_session.execute(text("SELECT count(*) FROM bookmarks"))).scalar_one() == 0
    assert (
        await db_session.execute(text("SELECT count(*) FROM sync_tombstones WHERE table_name = 'bookmarks'"))
    ).scalar_one() == 1


async def test_bookmark_rejects_foreign_book_and_rolls_back(client, auth_headers, db_session):
    owner = User(
        display_name="Other",
        primary_email="bookmark-owner@example.com",
        primary_email_verified=True,
        last_login_at=datetime.now(UTC),
    )
    db_session.add(owner)
    await db_session.flush()
    book = SyncBook(book_id=uuid4(), owner_user_id=owner.user_id, title="Foreign")
    db_session.add(book)
    await db_session.commit()
    foreign_id = book.book_id
    response = await upload(
        client,
        auth_headers,
        mutation("shelves", uuid4(), {"name": "Rollback"}),
        mutation("bookmarks", uuid4(), {"book_id": str(foreign_id), "position": 0.5}),
    )
    assert response.status_code == 403, response.text
    assert (await db_session.execute(text("SELECT count(*) FROM shelves"))).scalar_one() == 0


async def test_bookmark_rejects_foreign_entity_mutations(client, auth_headers, db_session):
    from papyrus.models import SyncBookmark

    owner = User(
        display_name="Other",
        primary_email="bookmark-entity@example.com",
        primary_email_verified=True,
        last_login_at=datetime.now(UTC),
    )
    db_session.add(owner)
    await db_session.flush()
    book = SyncBook(book_id=uuid4(), owner_user_id=owner.user_id, title="Foreign")
    db_session.add(book)
    await db_session.flush()
    bookmark = SyncBookmark(bookmark_id=uuid4(), owner_user_id=owner.user_id, book_id=book.book_id)
    db_session.add(bookmark)
    await db_session.commit()
    bookmark_id = bookmark.bookmark_id
    for op in ("PUT", "PATCH", "DELETE"):
        response = await upload(client, auth_headers, mutation("bookmarks", bookmark_id, {"note": "Forbidden"}, op))
        assert response.status_code == 403, response.text


@pytest.mark.parametrize("position", [-0.1, 1.1, None, "0.5", True])
async def test_bookmark_invalid_position_rolls_back(client, auth_headers, db_session, position):
    book_id = uuid4()
    response = await upload(
        client,
        auth_headers,
        mutation("books", book_id, {"title": "Rollback"}),
        mutation("bookmarks", uuid4(), {"book_id": str(book_id), "position": position}),
    )
    assert response.status_code == 400, response.text
    assert (await db_session.execute(text("SELECT count(*) FROM books"))).scalar_one() == 0


async def test_bookmark_requires_live_book_and_rejects_unknown_fields(client, auth_headers):
    response = await upload(client, auth_headers, mutation("bookmarks", uuid4(), {"book_id": str(uuid4())}))
    assert response.status_code == 400
    response = await upload(client, auth_headers, mutation("bookmarks", uuid4(), {"local_path": "/tmp/book"}))
    assert response.status_code == 422
