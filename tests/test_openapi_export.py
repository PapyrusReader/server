"""Public documentation export must be independent of local configuration."""

import json
import os
import subprocess
import sys
from pathlib import Path


def test_export_ignores_environment_and_dev_routes(tmp_path: Path) -> None:
    output = tmp_path / "api.json"
    command = [sys.executable, "scripts/export_openapi.py", str(output)]
    subprocess.run(command, check=True)
    baseline = output.read_text()
    environment = {
        **os.environ,
        "DEBUG": "true",
        "API_PREFIX": "/private-prefix",
        "PUBLIC_BASE_URL": "https://private-environment.example",
        "SECRET_KEY": "private-environment-secret",
    }
    subprocess.run(command, env=environment, check=True)
    assert output.read_text() == baseline
    assert "private-environment" not in baseline
    schema = json.loads(baseline)
    assert all(not path.startswith("/__dev") for path in schema["paths"])
    assert {"/v1/books", "/v1/auth/login", "/v1/opds/relay", "/v1/sync/powersync-upload"} <= schema["paths"].keys()
    assert schema["info"]["contact"]["url"] == "https://github.com/PapyrusReader/papyrus"

    subprocess.run([*command, "--check"], check=True)
