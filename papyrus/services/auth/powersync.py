"""PowerSync-related auth helpers."""

from __future__ import annotations

from uuid import UUID

from papyrus.core.exceptions import ValidationError
from papyrus.core.security import create_powersync_token, get_powersync_jwks


async def create_powersync_credentials(user_id: UUID) -> tuple[str, int]:
    try:
        token, expires_in = create_powersync_token(str(user_id))
    except RuntimeError as exc:
        raise ValidationError("PowerSync signing is not configured") from exc

    return token, expires_in


def get_powersync_jwks_payload() -> dict[str, list[dict[str, object]]]:
    try:
        return get_powersync_jwks()
    except RuntimeError as exc:
        raise ValidationError("PowerSync signing is not configured") from exc
