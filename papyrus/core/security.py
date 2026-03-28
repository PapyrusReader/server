"""Authentication and token security helpers."""

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from secrets import token_urlsafe
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from jwt.algorithms import RSAAlgorithm
from pwdlib import PasswordHash
from pwdlib.hashers.bcrypt import BcryptHasher

from papyrus.config import get_settings

password_hash = PasswordHash((BcryptHasher(),))


def _normalize_pem(value: str) -> str:
    return value.replace("\\n", "\n")


def _load_pem_configured_value(value: str | None, file_path: Path | None) -> str | None:
    if value is not None:
        return _normalize_pem(value)

    if file_path is None:
        return None

    return file_path.read_text(encoding="utf-8")


@lru_cache
def _get_powersync_private_key() -> Any:
    settings = get_settings()
    private_key_pem = _load_pem_configured_value(
        settings.powersync_jwt_private_key,
        settings.powersync_jwt_private_key_path,
    )

    if private_key_pem is None:
        raise RuntimeError("PowerSync private key is not configured")

    return serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"),
        password=None,
    )


@lru_cache
def _get_powersync_public_key() -> Any:
    settings = get_settings()
    public_key_pem = _load_pem_configured_value(
        settings.powersync_jwt_public_key,
        settings.powersync_jwt_public_key_path,
    )

    if public_key_pem is not None:
        return serialization.load_pem_public_key(public_key_pem.encode("utf-8"))

    return _get_powersync_private_key().public_key()


def _create_signed_token(
    data: dict[str, Any],
    token_type: str,
    expires_delta: timedelta,
    secret: str,
    algorithm: str,
) -> str:
    issued_at = datetime.now(UTC)
    payload = data.copy()
    payload.update(
        {
            "iat": issued_at,
            "exp": issued_at + expires_delta,
            "type": token_type,
        }
    )
    return jwt.encode(payload, secret, algorithm=algorithm)


def hash_password(password: str) -> str:
    return password_hash.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return password_hash.verify(plain_password, hashed_password)


def hash_opaque_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def generate_opaque_token() -> str:
    return token_urlsafe(48)


def create_access_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    settings = get_settings()
    ttl = expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    return _create_signed_token(data, "access", ttl, settings.secret_key, settings.algorithm)


def create_state_token(data: dict[str, Any], expires_delta: timedelta | None = None) -> str:
    settings = get_settings()
    ttl = expires_delta or timedelta(minutes=settings.oauth_state_expire_minutes)
    return _create_signed_token(data, "oauth_state", ttl, settings.secret_key, settings.algorithm)


def decode_token(token: str) -> dict[str, Any] | None:
    settings = get_settings()

    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm], options={"verify_aud": False})
    except jwt.PyJWTError:
        return None


def decode_state_token(token: str) -> dict[str, Any] | None:
    payload = decode_token(token)

    if payload is None or payload.get("type") != "oauth_state":
        return None

    return payload


def create_powersync_token(user_id: str, expires_delta: timedelta | None = None) -> tuple[str, int]:
    settings = get_settings()

    if settings.powersync_jwt_audience is None:
        raise RuntimeError("PowerSync audience is not configured")

    ttl = expires_delta or timedelta(minutes=settings.powersync_token_expire_minutes)
    issued_at = datetime.now(UTC)
    expires_at = issued_at + ttl
    headers = {"kid": settings.powersync_jwt_key_id}
    payload = {
        "sub": user_id,
        "aud": settings.powersync_jwt_audience,
        "iat": issued_at,
        "exp": expires_at,
        "type": "powersync",
    }

    token = jwt.encode(payload, _get_powersync_private_key(), algorithm="RS256", headers=headers)
    return token, int(ttl.total_seconds())


def get_powersync_jwks() -> dict[str, list[dict[str, Any]]]:
    settings = get_settings()
    jwk = RSAAlgorithm.to_jwk(_get_powersync_public_key(), as_dict=True)

    jwk.update(
        {
            "kid": settings.powersync_jwt_key_id,
            "alg": "RS256",
            "use": "sig",
        }
    )
    return {"keys": [jwk]}
