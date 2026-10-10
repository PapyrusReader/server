"""Validate host deployment settings without printing secrets."""

import json
import re
from pathlib import Path


def validate(values: dict[str, str]) -> None:
    for key in ("API_DOMAIN", "SYNC_DOMAIN", "APP_DOMAIN"):
        value = values.get(key, "")
        if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", value) or "." not in value:
            raise ValueError(f"{key} must be a DNS hostname")
        if value.endswith(("example.com", ".invalid", ".localhost", ".local")):
            raise ValueError(f"Replace the placeholder in {key} with your public hostname")
    if len({values[key] for key in ("API_DOMAIN", "SYNC_DOMAIN", "APP_DOMAIN")}) != 3:
        raise ValueError("API, sync and web app need distinct hostnames")
    if values.get("API_PREFIX") != "/v1":
        raise ValueError("API_PREFIX must remain /v1 for the current client and PowerSync configuration")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", values.get("PAPYRUS_VERSION", "")):
        raise ValueError("PAPYRUS_VERSION must be a release number")
    for key in ("POSTGRES_PASSWORD", "POWERSYNC_SOURCE_PASSWORD", "POWERSYNC_STORAGE_PASSWORD", "SECRET_KEY"):
        if not re.fullmatch(r"[A-Za-z0-9_-]{32,}", values.get(key, "")):
            raise ValueError(f"{key} needs at least 32 URL-safe characters; generate with openssl rand -hex 32")
    for key in ("POSTGRES_USER", "POSTGRES_DB", "POWERSYNC_STORAGE_USER", "POWERSYNC_STORAGE_DB"):
        if not re.fullmatch(r"[a-z][a-z0-9_]*", values.get(key, "")):
            raise ValueError(f"{key} must be a lowercase database identifier")
    for key in ("SMTP_HOST", "SMTP_FROM_EMAIL", "POWERSYNC_JWT_KEY_ID", "POWERSYNC_JWT_AUDIENCE"):
        if not values.get(key):
            raise ValueError(f"{key} is required")
    if values.get("APP_PUBLIC_BASE_URL") != f"https://{values['APP_DOMAIN']}":
        raise ValueError("APP_PUBLIC_BASE_URL must match the public HTTPS APP_DOMAIN")
    if json.loads(values.get("CORS_ORIGINS", "[]")) != [values["APP_PUBLIC_BASE_URL"]]:
        raise ValueError("CORS_ORIGINS must contain the HTTPS web app origin")
    if values.get("SMTP_USE_TLS") != "true" and values.get("SMTP_USE_SSL") != "true":
        raise ValueError("Production SMTP must use TLS or SSL")
    if bool(values.get("GOOGLE_OAUTH_CLIENT_ID")) != bool(values.get("GOOGLE_OAUTH_CLIENT_SECRET")):
        raise ValueError("Configure both Google OAuth credentials or neither")


def main() -> None:
    directory = Path(__file__).resolve().parent
    values = {}
    for line in (directory / "production.env").read_text().splitlines():
        if line.strip() and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip("'")
    validate(values)
    for name in ("powersync-private.pem", "powersync-public.pem"):
        if not (directory / "secrets" / name).is_file():
            raise ValueError(f"Missing secrets/{name}; see the deployment runbook")
    if not (directory / "web/current/index.html").is_file():
        raise ValueError(
            "Missing web/current/index.html; initialize the versioned web layout in the deployment runbook"
        )
    print("Deployment configuration validated")


if __name__ == "__main__":
    main()
