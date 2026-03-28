"""Tests for token security helpers."""

from __future__ import annotations

from pathlib import Path

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from papyrus.config import get_settings
from papyrus.core import security as security_module


@pytest.fixture
def powersync_key_files(tmp_path: Path) -> tuple[Path, Path]:
    """Create a temporary RSA keypair for PowerSync file-based config tests."""
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private_key_path = tmp_path / "private.pem"
    public_key_path = tmp_path / "public.pem"

    private_key_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_key_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

    return private_key_path, public_key_path


def test_create_powersync_token_supports_file_based_keys(
    monkeypatch: pytest.MonkeyPatch,
    powersync_key_files: tuple[Path, Path],
):
    """PowerSync tokens can be signed from key files instead of inline PEM env vars."""
    private_key_path, public_key_path = powersync_key_files
    settings = get_settings()
    monkeypatch.setattr(settings, "powersync_jwt_private_key", None)
    monkeypatch.setattr(settings, "powersync_jwt_public_key", None)
    monkeypatch.setattr(settings, "powersync_jwt_private_key_file", str(private_key_path))
    monkeypatch.setattr(settings, "powersync_jwt_public_key_file", str(public_key_path))
    monkeypatch.setattr(settings, "powersync_jwt_audience", "powersync-dev")
    monkeypatch.setattr(settings, "powersync_jwt_key_id", "papyrus-powersync-dev")
    security_module._get_powersync_private_key.cache_clear()
    security_module._get_powersync_public_key.cache_clear()

    try:
        token, expires_in = security_module.create_powersync_token("user-123")
        payload = jwt.decode(
            token,
            public_key_path.read_text(encoding="utf-8"),
            algorithms=["RS256"],
            audience="powersync-dev",
        )
        jwks = security_module.get_powersync_jwks()
    finally:
        security_module._get_powersync_private_key.cache_clear()
        security_module._get_powersync_public_key.cache_clear()

    assert expires_in > 0
    assert payload["sub"] == "user-123"
    assert payload["type"] == "powersync"
    assert jwks["keys"][0]["kid"] == "papyrus-powersync-dev"
