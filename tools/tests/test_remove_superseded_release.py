"""Protect unrelated package versions during the one-time release cleanup."""

import importlib.util
import unittest
from email.message import Message
from pathlib import Path
from typing import Any
from unittest.mock import patch
from urllib.error import HTTPError

SCRIPT_PATH = Path(__file__).resolve().parents[1] / "remove_superseded_release.py"
spec = importlib.util.spec_from_file_location("cleanup", SCRIPT_PATH)
assert spec is not None and spec.loader is not None
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)


def version(identifier: int, digest: str, *tags: str) -> dict[str, Any]:
    return {"id": identifier, "name": digest, "metadata": {"container": {"tags": list(tags)}}}


class CleanupTest(unittest.TestCase):
    def test_protects_replacement_shared_and_unrelated_manifests(self) -> None:
        root = version(1, cleanup.LEGACY_DIGEST, "1.0.0", cleanup.LEGACY_SHA_TAG)
        child = version(2, "old-child")
        versions = [root, child, version(3, "shared"), version(4, "new", "0.0.1"), version(5, "unrelated")]
        selected = cleanup.select_versions(versions, {cleanup.LEGACY_DIGEST, "old-child", "shared"}, {"new", "shared"})
        self.assertEqual(selected, [child, root])

    def test_refuses_to_delete_an_image_with_an_unrelated_tag(self) -> None:
        versions = [version(1, cleanup.LEGACY_DIGEST, "1.0.0"), version(2, "child", "0.0.2")]

        with self.assertRaisesRegex(ValueError, "unrelated tags"):
            cleanup.select_versions(versions, {cleanup.LEGACY_DIGEST, "child"}, {"new"})

    def test_replacement_cannot_be_the_obsolete_image(self) -> None:
        with self.assertRaisesRegex(ValueError, "different image"):
            cleanup.select_versions([], {cleanup.LEGACY_DIGEST}, {cleanup.LEGACY_DIGEST})

    def test_can_retry_after_a_child_manifest_was_already_deleted(self) -> None:
        def request(url: str, headers: dict[str, str]) -> dict[str, Any]:
            if url.endswith("/root"):
                return {"manifests": [{"digest": "removed"}, {"digest": "remaining"}]}

            if url.endswith("/removed"):
                raise HTTPError(url, 404, "Not Found", Message(), None)

            return {}

        with patch.object(cleanup, "request_json", side_effect=request):
            self.assertEqual(
                cleanup.manifest_graph("root", {}, missing_children=True), {"root", "removed", "remaining"}
            )

            with self.assertRaises(HTTPError):
                cleanup.manifest_graph("root", {})

    def test_main_paginates_and_deletes_only_obsolete_versions_children_first(self) -> None:
        replacement = "sha256:" + "a" * 64
        deleted = []
        legacy = {cleanup.LEGACY_DIGEST, "child"}

        def request(url: str, headers: dict[str, str], method: str = "GET") -> Any:
            if "ghcr.io/token" in url:
                return {"token": "test-only"}

            if method == "DELETE":
                deleted.append(url.rsplit("/", 1)[1])
                return None

            if url.endswith("&page=1"):
                return [version(1, cleanup.LEGACY_DIGEST, "1.0.0"), version(2, "child")] + [
                    version(identifier, f"other-{identifier}") for identifier in range(3, 101)
                ]

            return [version(101, replacement, "0.0.1")]

        environ = {
            "VERSION": "0.0.1",
            "GITHUB_REPOSITORY": "PapyrusReader/server",
            "IMAGE_DIGEST": replacement,
            "GITHUB_TOKEN": "test-only",
        }

        with (
            patch.dict("os.environ", environ, clear=True),
            patch.object(cleanup, "manifest_graph", side_effect=[{replacement}, legacy]),
            patch.object(cleanup, "request_json", side_effect=request),
            patch("builtins.print"),
        ):
            cleanup.main()

        self.assertEqual(deleted, ["2", "1"])

    def test_no_cleanup_for_another_release(self) -> None:
        with (
            patch.dict("os.environ", {"VERSION": "0.0.2"}, clear=True),
            patch.object(cleanup, "request_json") as request,
        ):
            with self.assertRaises(ValueError):
                cleanup.main()

            request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
