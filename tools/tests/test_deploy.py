"""Reject incomplete production configuration without connecting to a database."""

import importlib.util
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "deploy_config", Path(__file__).resolve().parents[2] / "deploy/validate_config.py"
)
assert spec is not None and spec.loader is not None
config = importlib.util.module_from_spec(spec)
spec.loader.exec_module(config)


class DeploymentTest(unittest.TestCase):
    def values(self) -> dict[str, str]:
        return {
            "API_DOMAIN": "api.papyrusreader.org",
            "SYNC_DOMAIN": "sync.papyrusreader.org",
            "APP_DOMAIN": "app.papyrusreader.org",
            "APP_PUBLIC_BASE_URL": "https://app.papyrusreader.org",
            "CORS_ORIGINS": '["https://app.papyrusreader.org"]',
            "API_PREFIX": "/v1",
            "PAPYRUS_VERSION": "1.0.0",
            "SECRET_KEY": "a" * 64,
            "POSTGRES_PASSWORD": "b" * 64,
            "POWERSYNC_SOURCE_PASSWORD": "c" * 64,
            "POWERSYNC_STORAGE_PASSWORD": "d" * 64,
            "POSTGRES_USER": "papyrus",
            "POSTGRES_DB": "papyrus",
            "POWERSYNC_STORAGE_USER": "powersync",
            "POWERSYNC_STORAGE_DB": "powersync",
            "ACME_EMAIL": "admin@papyrusreader.org",
            "SMTP_HOST": "smtp.papyrusreader.org",
            "SMTP_FROM_EMAIL": "support@papyrusreader.org",
            "SMTP_USE_TLS": "true",
            "POWERSYNC_JWT_KEY_ID": "v1",
            "POWERSYNC_JWT_AUDIENCE": "papyrus-production",
        }

    def test_valid_config(self) -> None:
        config.validate(self.values())

    def test_app_deployment_does_not_require_proxy_contact_settings(self) -> None:
        values = self.values()
        del values["ACME_EMAIL"]
        config.validate(values)

    def test_placeholder_secret_or_url_drift_is_rejected(self) -> None:
        for key, value in (
            ("API_DOMAIN", "api.example.com"),
            ("SECRET_KEY", ""),
            ("POSTGRES_PASSWORD", "password"),
            ("APP_PUBLIC_BASE_URL", "http://localhost"),
            ("SMTP_USE_TLS", "false"),
            ("POSTGRES_DB", "name/with/slashes"),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                config.validate({**self.values(), key: value})
