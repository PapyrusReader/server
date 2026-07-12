from pathlib import Path


def test_books_stream_downloads_media_references() -> None:
    config = (Path(__file__).parents[1] / "powersync" / "sync-config.yaml").read_text()
    books_select = config.split("streams:\n  books:", 1)[1].split("FROM books", 1)[0]

    assert "file_media_id" in books_select
    assert "cover_media_id" in books_select
