"""Tests for local environment template drift."""

from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _env_keys(path: Path) -> list[str]:
    keys: list[str] = []

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()

        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue

        keys.append(stripped.split("=", 1)[0])

    return keys


def test_local_env_keys_match_example_when_env_exists() -> None:
    """Keep local .env aligned with the checked-in template without reading values."""
    env_path = REPO_ROOT / ".env"

    if not env_path.exists():
        pytest.skip("local .env is not present")

    example_keys = _env_keys(REPO_ROOT / ".env.example")
    env_keys = _env_keys(env_path)

    assert env_keys == example_keys
