import json
import os
import subprocess
import sys
from pathlib import Path

from tests.timing import summary


def test_phase_totals_and_rankings_include_failures():
    records = [
        {"test": "one", "phase": "setup", "seconds": 2.0, "outcome": "passed"},
        {"test": "two", "phase": "setup", "seconds": 3.0, "outcome": "failed"},
        {"test": "one", "phase": "call", "seconds": 4.0, "outcome": "failed"},
        {"test": "one", "phase": "teardown", "seconds": 1.0, "outcome": "passed"},
    ]
    report = summary(records)
    assert "| setup | 5.00 |" in report
    assert "| call | 4.00 |" in report
    assert "| teardown | 1.00 |" in report
    assert report.index("| 3.00 | failed | two |") < report.index("| 2.00 | passed | one |")


def test_plugin_exports_all_phases_on_failure_without_hiding_exit_status(tmp_path):
    source = tmp_path / "test_example.py"
    source.write_text(
        "import pytest\n"
        "@pytest.fixture\n"
        "def broken():\n    raise RuntimeError('setup failure')\n"
        "def test_setup(broken):\n    pass\n"
        "def test_failure():\n    assert False\n"
        "@pytest.mark.skip(reason='optional')\n"
        "def test_skip():\n    pass\n"
    )
    output = tmp_path / "reports/timing.json"
    step_summary = tmp_path / "summary.md"
    environment = {
        **os.environ,
        "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
        "PAPYRUS_TEST_TIMING": str(output),
        "GITHUB_STEP_SUMMARY": str(step_summary),
    }
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "tests.timing", str(source)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    records = json.loads(output.read_text())
    assert {record["phase"] for record in records} == {"setup", "call", "teardown"}
    assert any(record["phase"] == "setup" and record["outcome"] == "failed" for record in records)
    assert any(record["phase"] == "call" and record["outcome"] == "failed" for record in records)
    assert any(record["outcome"] == "skipped" for record in records)
    assert step_summary.read_text() == output.with_suffix(".md").read_text()
