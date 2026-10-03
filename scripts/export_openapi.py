"""Export public API documentation without local settings, services or secrets."""

import argparse
import json
import os
from pathlib import Path
from unittest.mock import patch

from papyrus.config import Settings


def export() -> str:
    """Build the schema in a fresh process without starting the app lifespan."""
    with patch.dict(os.environ, {}, clear=True):
        settings = Settings(
            _env_file=None,
            debug=False,
            host="127.0.0.1",
            port=8080,
            api_prefix="/v1",
            cors_origins=[],
            postgres_user="docs",
            postgres_password="docs",
            postgres_host="127.0.0.1",
            postgres_port=5432,
            postgres_db="docs",
            secret_key="documentation-placeholder",
            algorithm="HS256",
            access_token_expire_minutes=60,
            refresh_token_expire_days=30,
            rate_limit_auth=5,
            rate_limit_general=100,
            rate_limit_upload=10,
            rate_limit_batch=20,
        )

        with patch("papyrus.config.get_settings", return_value=settings):
            from papyrus.main import create_app

            schema = create_app().openapi()

    return json.dumps(schema, indent=2, sort_keys=True) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = export()

    if args.check:
        if args.output.read_text() != content:
            raise SystemExit("API snapshot is stale; regenerate with scripts/export_openapi.py")

    else:
        args.output.write_text(content)


if __name__ == "__main__":
    main()
