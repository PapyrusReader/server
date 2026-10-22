"""Production proxy trust must be explicit and limited to the edge proxy."""

import runpy
from pathlib import Path
from unittest.mock import Mock

import pytest

from papyrus import main

validate = runpy.run_path(str(Path(__file__).resolve().parents[1] / "deploy" / "validate_config.py"))["validate"]


def _production_values() -> dict[str, str]:
    example = Path(__file__).resolve().parents[1] / "deploy" / "production.env.example"
    values = dict(line.split("=", 1) for line in example.read_text().splitlines() if line and not line.startswith("#"))

    for key in ("POSTGRES_PASSWORD", "POWERSYNC_SOURCE_PASSWORD", "POWERSYNC_STORAGE_PASSWORD", "SECRET_KEY"):
        values[key] = "test-secret-" * 4

    values["SMTP_HOST"] = "smtp.papyrus.test"
    values["SMTP_FROM_EMAIL"] = "mail@papyrus.test"
    values["FORWARDED_ALLOW_IPS"] = "172.20.0.2"
    return values


def test_production_accepts_explicit_private_proxy_address() -> None:
    validate(_production_values())


@pytest.mark.parametrize("address", ["", "*", "172.20.0.0/16", "127.0.0.1", "0.0.0.0", "8.8.8.8"])
def test_production_rejects_missing_or_broad_proxy_trust(address: str) -> None:
    values = _production_values()
    values["FORWARDED_ALLOW_IPS"] = address

    with pytest.raises(ValueError, match="FORWARDED_ALLOW_IPS"):
        validate(values)


def test_api_startup_passes_configured_proxy_trust(monkeypatch: pytest.MonkeyPatch) -> None:
    run = Mock()
    monkeypatch.setattr("uvicorn.run", run)
    monkeypatch.setattr(main.settings, "forwarded_allow_ips", "172.20.0.2")
    main.run()
    assert run.call_args.kwargs["forwarded_allow_ips"] == "172.20.0.2"
