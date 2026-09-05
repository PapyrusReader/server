"""Library upload transaction and offline conflict regression tests."""

from uuid import uuid4

from sqlalchemy import text


def mutation(table, row_id, data=None, op="PUT"):
    return {"type": table, "op": op, "id": str(row_id), "data": data}


async def upload(client, auth_headers, *batch):
    return await client.post("/v1/sync/powersync-upload", headers=auth_headers, json={"batch": batch})


async def test_mixed_roundtrip_and_null_patch(client, auth_headers, db_session):
    book, shelf, tag, note, annotation = [uuid4() for _ in range(5)]
    location = {"chapter": 3, "chapter_title": "Chapter", "page_number": 12, "percentage": 0.4}
    batch = [
        mutation("books", book, {"title": "Book", "series_id": "legacy-series", "is_physical": True}),
        mutation("shelves", shelf, {"name": "Shelf", "icon_code_point": 123, "icon_font_family": "MaterialIcons"}),
        mutation("tags", tag, {"name": "Tag", "color_hex": "#FFFFFF"}),
        mutation(
            "notes",
            note,
            {
                "book_id": str(book),
                "title": "Note",
                "content": "Body",
                "location": location,
                "tags": ["free text"],
                "is_pinned": True,
            },
        ),
        mutation(
            "annotations",
            annotation,
            {"book_id": str(book), "selected_text": "Quote", "location": location, "color": "green", "note": "Comment"},
        ),
        mutation("book_shelves", f"{book}:{shelf}", {"book_id": str(book), "shelf_id": str(shelf), "sort_order": 2}),
        mutation("book_tags", f"{book}:{tag}", {"book_id": str(book), "tag_id": str(tag)}),
    ]
    for _ in range(2):
        response = await upload(client, auth_headers, *batch)
        assert response.status_code == 200, response.text
    response = await upload(
        client, auth_headers, mutation("notes", note, {"location": None, "content": "Edited"}, "PATCH")
    )
    assert response.status_code == 200
    row = (await db_session.execute(text("SELECT title, content, location, tags, is_pinned FROM notes"))).one()
    assert row == ("Note", "Edited", None, ["free text"], True)
    assert (await db_session.execute(text("SELECT count(*) FROM book_tags"))).scalar_one() == 1


async def test_delete_wins_and_cascades(client, auth_headers, db_session):
    book, shelf, note = [uuid4() for _ in range(3)]
    response = await upload(
        client,
        auth_headers,
        mutation("books", book, {"title": "Book"}),
        mutation("shelves", shelf, {"name": "Shelf"}),
        mutation("notes", note, {"book_id": str(book), "title": "Note", "content": "Body"}),
        mutation("book_shelves", f"{book}:{shelf}", {"book_id": str(book), "shelf_id": str(shelf)}),
    )
    assert response.status_code == 200, response.text
    assert (await upload(client, auth_headers, mutation("books", book, op="DELETE"))).status_code == 200
    response = await upload(
        client,
        auth_headers,
        mutation("books", book, {"title": "Stale"}),
        mutation("notes", note, {"book_id": str(book), "title": "Stale", "content": "Body"}),
        mutation("notes", uuid4(), {"book_id": str(book), "title": "Late", "content": "Body"}),
        mutation("book_shelves", f"{book}:{shelf}", {"book_id": str(book), "shelf_id": str(shelf)}),
    )
    assert response.status_code == 200, response.text
    for table in ("books", "notes", "book_shelves"):
        assert (await db_session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one() == 0


async def test_shelf_cycle_rolls_back_and_delete_reparents(client, auth_headers, db_session):
    parent, child = uuid4(), uuid4()
    assert (
        await upload(
            client,
            auth_headers,
            mutation("shelves", parent, {"name": "Parent"}),
            mutation("shelves", child, {"name": "Child", "parent_shelf_id": str(parent)}),
        )
    ).status_code == 200
    response = await upload(
        client,
        auth_headers,
        mutation("shelves", child, {"name": "Wrong"}, "PATCH"),
        mutation("shelves", parent, {"parent_shelf_id": str(child)}, "PATCH"),
    )
    assert response.status_code == 400
    assert (
        await db_session.execute(text("SELECT name FROM shelves WHERE shelf_id = :id"), {"id": child})
    ).scalar_one() == "Child"
    await db_session.rollback()
    assert (await upload(client, auth_headers, mutation("shelves", parent, op="DELETE"))).status_code == 200
    assert (await db_session.execute(text("SELECT parent_shelf_id FROM shelves"))).scalar_one() is None


async def test_membership_remove_readd_and_pair_validation(client, auth_headers):
    book, tag = uuid4(), uuid4()
    membership = mutation("book_tags", f"{book}:{tag}", {"book_id": str(book), "tag_id": str(tag)})
    assert (
        await upload(
            client,
            auth_headers,
            mutation("books", book, {"title": "Book"}),
            mutation("tags", tag, {"name": "Tag", "color_hex": "#FFFFFF"}),
            membership,
        )
    ).status_code == 200
    assert (await upload(client, auth_headers, mutation("book_tags", membership["id"], op="DELETE"))).status_code == 200
    assert (await upload(client, auth_headers, membership)).status_code == 200
    assert (
        await upload(client, auth_headers, mutation("book_tags", f"{book}:{uuid4()}", membership["data"]))
    ).status_code == 400


async def test_legacy_envelope_normalizes_and_preserves_metadata(client, auth_headers, db_session):
    envelope = {
        "publication_date": "2020-01-01T00:00:00Z",
        "series_id": "old-id",
        "file_size": 42,
        "is_physical": True,
        "custom_metadata": {"key": "value"},
    }
    response = await upload(
        client,
        auth_headers,
        mutation("books", uuid4(), {"title": "Book", "custom_metadata": envelope, "series_id": "explicit"}),
    )
    assert response.status_code == 200, response.text
    row = (await db_session.execute(text("SELECT series_id, file_size, is_physical, custom_metadata FROM books"))).one()
    assert row == ("explicit", 42, True, envelope)


async def test_foreign_reference_rolls_back_mixed_batch(client, auth_headers, db_session):
    from datetime import UTC, datetime

    from papyrus.models import SyncBook, User

    other = User(
        display_name="Other",
        primary_email="other-library@example.com",
        primary_email_verified=True,
        last_login_at=datetime.now(UTC),
    )
    db_session.add(other)
    await db_session.flush()
    foreign_book = SyncBook(book_id=uuid4(), owner_user_id=other.user_id, title="Foreign")
    db_session.add(foreign_book)
    await db_session.commit()
    for table, data in (
        ("notes", {"book_id": str(foreign_book.book_id), "title": "Note", "content": "Body"}),
        (
            "annotations",
            {"book_id": str(foreign_book.book_id), "selected_text": "Quote", "location": {"page_number": 1}},
        ),
    ):
        shelf_id = uuid4()
        response = await upload(
            client,
            auth_headers,
            mutation("shelves", shelf_id, {"name": "Must roll back"}),
            mutation(table, uuid4(), data),
        )
        assert response.status_code == 403, response.text
        assert (await db_session.execute(text("SELECT count(*) FROM shelves"))).scalar_one() == 0
        await db_session.rollback()


async def test_delete_before_create_and_missing_patch_do_not_create(client, auth_headers, db_session):
    for table in ("books", "shelves", "tags", "notes", "annotations"):
        row_id = uuid4()
        assert (await upload(client, auth_headers, mutation(table, row_id, op="DELETE"))).status_code == 200
        assert (await upload(client, auth_headers, mutation(table, row_id, {}))).status_code == 200
        assert (await upload(client, auth_headers, mutation(table, uuid4(), {}, "PATCH"))).status_code == 200
        assert (await db_session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one() == 0
        await db_session.rollback()


async def test_deleting_annotation_or_tag_blocks_stale_recreation(client, auth_headers, db_session):
    book, tag, annotation = uuid4(), uuid4(), uuid4()
    batch = [
        mutation("books", book, {"title": "Book"}),
        mutation("tags", tag, {"name": "Tag", "color_hex": "red"}),
        mutation(
            "annotations", annotation, {"book_id": str(book), "selected_text": "Quote", "location": {"page_number": 1}}
        ),
        mutation("book_tags", f"{book}:{tag}", {"book_id": str(book), "tag_id": str(tag)}),
    ]
    assert (await upload(client, auth_headers, *batch)).status_code == 200
    assert (
        await upload(
            client, auth_headers, mutation("tags", tag, op="DELETE"), mutation("annotations", annotation, op="DELETE")
        )
    ).status_code == 200
    assert (await upload(client, auth_headers, *batch[1:])).status_code == 200
    for table in ("tags", "annotations", "book_tags"):
        assert (await db_session.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one() == 0


async def test_concurrent_patches_preserve_unrelated_values(client, auth_headers, db_session):
    import asyncio

    book = uuid4()
    assert (
        await upload(
            client,
            auth_headers,
            mutation(
                "books",
                book,
                {"title": "Book", "author": "Author", "custom_metadata": {"custom_metadata": {"keep": True}}},
            ),
        )
    ).status_code == 200
    results = await asyncio.gather(
        upload(client, auth_headers, mutation("books", book, {"title": "New title"}, "PATCH")),
        upload(client, auth_headers, mutation("books", book, {"author": "New author"}, "PATCH")),
    )
    assert [result.status_code for result in results] == [200, 200]
    row = (await db_session.execute(text("SELECT title, author, custom_metadata FROM books"))).one()
    assert row == ("New title", "New author", {"custom_metadata": {"keep": True}})


async def test_library_field_validation_is_atomic(client, auth_headers, db_session):
    for table, data in (
        ("shelves", {"name": "Shelf", "is_smart": "maybe"}),
        ("notes", {"title": "Note", "content": "Body", "location": {"page_number": "bad"}}),
        ("annotations", {"location": {"page_number": 1, "unknown": True}}),
        ("books", {"title": "Book", "file_size": 1.5}),
    ):
        response = await upload(
            client, auth_headers, mutation("books", uuid4(), {"title": "Rollback"}), mutation(table, uuid4(), data)
        )
        assert response.status_code == 400, response.text
        assert (await db_session.execute(text("SELECT count(*) FROM books"))).scalar_one() == 0
        await db_session.rollback()
