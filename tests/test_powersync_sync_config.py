import re
from pathlib import Path


def test_books_stream_downloads_media_references() -> None:
    config = (Path(__file__).parents[1] / "powersync" / "sync-config.yaml").read_text()
    books_select = config.split("streams:\n  books:", 1)[1].split("FROM books", 1)[0]
    assert "file_media_id" in books_select
    assert "cover_media_id" in books_select


def test_library_streams_filter_owners_and_setup_publishes_tables() -> None:
    root = Path(__file__).parents[1]
    config = (root / "powersync/sync-config.yaml").read_text()
    setup = (root / "scripts/setup_local_powersync.sh").read_text()

    for table in (
        "books",
        "shelves",
        "tags",
        "notes",
        "annotations",
        "bookmarks",
        "book_shelves",
        "book_tags",
        "reading_timers",
    ):
        stream = config.split(f"  {table}:\n", 1)[1].split("\n\n", 1)[0]
        assert "auto_subscribe: true" in stream
        assert "WHERE owner_user_id::text = auth.user_id()" in stream
        assert f"public.{table}" in setup

    assert "  demo_items:" in config
    assert "sync_tombstones" not in config
    assert "file_path" not in config
    assert "series_id" in config
    assert "location::text AS location" in config


def test_production_publishes_and_grants_every_synced_table() -> None:
    root = Path(__file__).parents[1]
    config = (root / "powersync/sync-config.yaml").read_text()
    bootstrap = (root / "deploy/bootstrap-powersync.sql").read_text()
    tables = set(re.findall(r"\bFROM\s+(\w+)", config))
    grants = bootstrap.split("GRANT SELECT ON TABLE ", 1)[1].split(" TO powersync_role;", 1)[0]
    publication = bootstrap.split("ALTER PUBLICATION powersync SET TABLE ", 1)[1].split(";", 1)[0]
    assert tables
    assert tables <= set(re.findall(r"public\.(\w+)", grants))
    assert tables <= set(re.findall(r"public\.(\w+)", publication))
