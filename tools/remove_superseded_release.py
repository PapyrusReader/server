"""Remove the unused 1.0.0 container after publishing the initial 0.0.1 release."""

import json
import os
import re
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

LEGACY_DIGEST = "sha256:7da70ea17d60b5b0c71f7ea7743b9cb3e3256be1bc1d095bf009811be9749912"
LEGACY_SHA_TAG = "sha-c25ba084db02203db5835e0c424cc6bed4fe804b"
PACKAGE_API = "https://api.github.com/orgs/PapyrusReader/packages/container/server/versions"
MANIFEST_API = "https://ghcr.io/v2/papyrusreader/server/manifests"


def request_json(url: str, headers: dict[str, str], method: str = "GET") -> Any:
    with urlopen(Request(url, headers=headers, method=method), timeout=30) as response:
        data = response.read()

    return json.loads(data) if data else None


def manifest_graph(digest: str, headers: dict[str, str], *, missing_children: bool = False) -> set[str]:
    """Include architecture and attestation manifests, without touching layers."""
    visited: set[str] = set()
    pending = [digest]

    while pending:
        current = pending.pop()

        if current in visited:
            continue

        visited.add(current)

        try:
            manifest = request_json(f"{MANIFEST_API}/{current}", headers)
        except HTTPError as error:
            if error.code == 404 and missing_children and current != digest:
                continue

            raise

        pending.extend(item["digest"] for item in manifest.get("manifests", []))

    return visited


def select_versions(versions: list[dict[str, Any]], legacy: set[str], replacement: set[str]) -> list[dict[str, Any]]:
    """Protect shared manifests and refuse to remove unrelated tags."""
    if LEGACY_DIGEST in replacement:
        raise ValueError("The replacement must be a different image")

    selected = []

    for version in versions:
        digest = version["name"]

        if digest not in legacy or digest in replacement:
            continue

        tags = set(version.get("metadata", {}).get("container", {}).get("tags", []))

        if tags - {"1.0.0", LEGACY_SHA_TAG}:
            raise ValueError(f"Refusing to delete manifest with unrelated tags: {digest}")

        selected.append(version)

    return sorted(selected, key=lambda item: item["name"] == LEGACY_DIGEST)


def main() -> None:
    if os.environ.get("VERSION") != "0.0.1" or os.environ.get("GITHUB_REPOSITORY") != "PapyrusReader/server":
        raise ValueError("Cleanup is restricted to the initial Papyrus server 0.0.1 release")

    replacement_digest = os.environ["IMAGE_DIGEST"]

    if not re.fullmatch(r"sha256:[0-9a-f]{64}", replacement_digest) or replacement_digest == LEGACY_DIGEST:
        raise ValueError("A valid replacement image digest is required")

    token = request_json("https://ghcr.io/token?service=ghcr.io&scope=repository:papyrusreader/server:pull", {})[
        "token"
    ]
    registry_headers = {
        "Authorization": f"Bearer {token}",
        "Accept": ", ".join(
            (
                "application/vnd.oci.image.index.v1+json",
                "application/vnd.oci.image.manifest.v1+json",
                "application/vnd.docker.distribution.manifest.list.v2+json",
                "application/vnd.docker.distribution.manifest.v2+json",
            )
        ),
    }
    replacement = manifest_graph(replacement_digest, registry_headers)

    try:
        legacy = manifest_graph(LEGACY_DIGEST, registry_headers, missing_children=True)
    except HTTPError as error:
        if error.code != 404:
            raise

        print("The obsolete image is already absent; no package versions deleted.")
        return

    api_headers = {
        "Authorization": f"Bearer {os.environ['GITHUB_TOKEN']}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    versions = []
    page = 1

    while True:
        batch = request_json(f"{PACKAGE_API}?state=active&per_page=100&page={page}", api_headers)
        versions.extend(batch)

        if len(batch) < 100:
            break

        page += 1

    selected = select_versions(versions, legacy, replacement)

    for version in selected:
        request_json(f"{PACKAGE_API}/{version['id']}", api_headers, "DELETE")
        print(f"Deleted obsolete manifest {version['name']}")

    print(f"Removed {len(selected)} obsolete package versions; preserved the replacement and public package.")


if __name__ == "__main__":
    main()
