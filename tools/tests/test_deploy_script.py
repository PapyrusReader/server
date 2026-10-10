"""Exercise deployment orchestration with a disposable Compose command recorder."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


class DeploymentScriptTest(unittest.TestCase):
    def setUp(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        shutil.copy(ROOT / "deploy/deploy.sh", self.root / "deploy.sh")
        (self.root / "validate_config.py").write_text("pass\n")
        self.sql = self.root / "bootstrap-powersync.sql"
        self.calls = self.root / "compose-calls.jsonl"
        binary = self.root / "bin"
        binary.mkdir()
        docker = binary / "docker"

        docker.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\n"
            "args = sys.argv[1:]\n"
            "publication = 'exec' in args and 'database' in args\n"
            "data = sys.stdin.read() if publication else None\n"
            "with open(os.environ['COMPOSE_CALLS'], 'a') as output:\n"
            "    output.write(json.dumps({'args': args, 'stdin': data}) + '\\n')\n"
            "if publication and os.environ.get('PUBLICATION_FAILURE'):\n"
            "    sys.exit(42)\n"
        )

        docker.chmod(0o755)

        self.environment = {
            **os.environ,
            "PATH": str(binary) + os.pathsep + os.environ["PATH"],
            "COMPOSE_CALLS": str(self.calls),
        }

    def run_deployment(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["sh", str(self.root / "deploy.sh")],
            env=self.environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
        )

    def recorded_calls(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def test_each_upgrade_streams_current_host_sql(self) -> None:
        for sql in ("SELECT 'books';\n", "SELECT 'books', 'reading_goals', 'reading_activities';\n"):
            replacement = self.root / "bootstrap-next.sql"
            replacement.write_text(sql)
            replacement.replace(self.sql)
            result = self.run_deployment()
            self.assertEqual(result.returncode, 0, result.stderr)
            publication_calls = [call for call in self.recorded_calls() if call["stdin"] is not None]
            self.assertEqual(publication_calls[-1]["stdin"], sql)

    def test_publication_failure_prevents_service_activation(self) -> None:
        self.sql.write_text("SELECT 'reading_goals';\n")
        self.environment["PUBLICATION_FAILURE"] = "1"
        result = self.run_deployment()
        self.assertEqual(result.returncode, 42)
        calls = self.recorded_calls()
        self.assertIsNotNone(calls[-1]["stdin"])
        self.assertFalse(any("up" in call["args"] and "api" in call["args"] for call in calls))


if __name__ == "__main__":
    unittest.main()
