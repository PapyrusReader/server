"""Owner-scoped release selection tokens."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import HTTPException

from papyrus.core.security import decrypt_secret_payload, encrypt_secret_payload
from papyrus.models.acquisition import AcquisitionEndpoint

from .types import ReleaseCandidate, ReleaseTokenPayload

RELEASE_TOKEN_LIFETIME = timedelta(minutes=5)


def create_release_token(
    release: ReleaseCandidate,
    endpoint: AcquisitionEndpoint,
    *,
    now: datetime | None = None,
) -> str:
    issued_at = now or datetime.now(UTC)

    if endpoint.endpoint_id is None:
        raise ValueError("Release tokens require a persisted endpoint")

    return encrypt_secret_payload(
        {
            "endpoint_id": str(endpoint.endpoint_id),
            "owner_user_id": str(endpoint.owner_user_id),
            "title": release.title,
            "download_url": release.download_url,
            "protocol": release.protocol,
            "indexer": release.indexer,
            "size_bytes": "" if release.size_bytes is None else str(release.size_bytes),
            "seeders": "" if release.seeders is None else str(release.seeders),
            "publish_date": release.publish_date.isoformat() if release.publish_date is not None else "",
            "format_hints": json.dumps(release.format_hints),
            "expires_at": str(int((issued_at + RELEASE_TOKEN_LIFETIME).timestamp())),
        }
    )


def decode_release_token(
    token: str,
    owner_user_id: UUID,
    *,
    now: datetime | None = None,
) -> ReleaseTokenPayload:
    try:
        payload = decrypt_secret_payload(token)
        expires_at = int(payload["expires_at"])
        token_owner_user_id = UUID(payload["owner_user_id"])
        endpoint_id = UUID(payload["endpoint_id"])
        format_hints = json.loads(payload["format_hints"])
        size_bytes = int(payload["size_bytes"]) if payload["size_bytes"] else None
        seeders = int(payload["seeders"]) if payload["seeders"] else None
        publish_date = datetime.fromisoformat(payload["publish_date"]) if payload["publish_date"] else None

        if (
            token_owner_user_id != owner_user_id
            or expires_at <= int((now or datetime.now(UTC)).timestamp())
            or not isinstance(format_hints, list)
            or not all(isinstance(value, str) for value in format_hints)
        ):
            raise ValueError

        return ReleaseTokenPayload(
            endpoint_id=endpoint_id,
            owner_user_id=token_owner_user_id,
            title=payload["title"],
            download_url=payload["download_url"],
            protocol=payload["protocol"],
            indexer=payload["indexer"],
            size_bytes=size_bytes,
            seeders=seeders,
            publish_date=publish_date,
            format_hints=format_hints,
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Release token is invalid or expired") from exc
