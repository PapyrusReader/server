"""The web layout migration preserves the deployed app and is safe to repeat."""

import importlib.util
import tempfile
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "migrate_web_layout", Path(__file__).resolve().parents[2] / "deploy/migrate_web_layout.py"
)
assert spec is not None and spec.loader is not None
layout = importlib.util.module_from_spec(spec)
spec.loader.exec_module(layout)


class WebLayoutTest(unittest.TestCase):
    def test_snapshot_preserves_original_files_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text("existing release")
            (root / "assets").mkdir()
            (root / "assets/book.js").write_text("book")
            layout.migrate(root)
            target = (root / "current").resolve()
            layout.migrate(root)
            self.assertEqual((root / "current").resolve(), target)
            self.assertEqual((root / "index.html").read_text(), "existing release")
            self.assertEqual((root / "current/assets/book.js").read_text(), "book")

    def test_missing_initial_release_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory, self.assertRaises(ValueError):
            layout.migrate(Path(directory))

    def test_symlink_content_rejected_without_changes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "index.html").write_text("existing release")
            (root / "escape").symlink_to("/tmp")

            with self.assertRaises(ValueError):
                layout.migrate(root)

            self.assertFalse((root / "current").exists())
