"""Export pytest phase durations when PAPYRUS_TEST_TIMING names an output file."""

import json
import os
from pathlib import Path


def summary(records):
    lines = [
        "### Pytest phase durations",
        "",
        "Phase totals measure test execution, not workflow elapsed or aggregate runner time.",
        "Shared fixture costs are attributed to the test that initializes or finalizes them.",
        "",
        "| Phase | Seconds |",
        "| --- | ---: |",
    ]

    for phase in ("setup", "call", "teardown"):
        total = sum(record["seconds"] for record in records if record["phase"] == phase)
        lines.append(f"| {phase} | {total:.2f} |")

    for phase in ("setup", "call", "teardown"):
        lines.extend(["", f"#### Slowest {phase}", "", "| Seconds | Outcome | Test |", "| ---: | --- | --- |"])
        ranked = sorted(
            (record for record in records if record["phase"] == phase),
            key=lambda record: record["seconds"],
            reverse=True,
        )

        for record in ranked[:10]:
            name = record["test"].replace("|", "\\|").replace("\n", " ").replace("`", "'")
            lines.append(f"| {record['seconds']:.2f} | {record['outcome']} | {name} |")

    return "\n".join(lines) + "\n"


def pytest_terminal_summary(terminalreporter):
    destination = os.environ.get("PAPYRUS_TEST_TIMING")

    if not destination:
        return

    records = [
        {"test": report.nodeid, "phase": report.when, "seconds": report.duration, "outcome": report.outcome}
        for reports in terminalreporter.stats.values()
        for report in reports
        if getattr(report, "when", None) in ("setup", "call", "teardown")
    ]
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")
    markdown = summary(records)
    path.with_suffix(".md").write_text(markdown, encoding="utf-8")

    if step_summary := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(step_summary).open("a", encoding="utf-8") as stream:
            stream.write(markdown)
