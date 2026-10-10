"""Render the test agent's pull request comment and job summary as Markdown.

Every input is optional, so a report can be produced for any outcome,
including runs where the agent failed before writing its report.

Usage:
    render_report.py --status <status> [--agent-report report.json]
                     [--validation validation.json] [--coverage coverage-delta.json]
                     [--junit backend=pytest.xml --junit frontend=vitest.xml]
                     [--commit-sha <sha>] [--run-url <url>] [--detail <text>]
                     [--out report.md]
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

MARKER = "<!-- test-agent-report -->"

STATUSES = {
    "committed": "✅ Tests were added or updated and committed to this pull request.",
    "no-changes": "ℹ️ No testable source changes; the agent did not run.",
    "nothing-to-add": "ℹ️ The agent decided no test changes are needed.",
    "skipped": "⏭️ The test agent was skipped.",
    "rejected": "⛔ The agent's patch was rejected by validation and was not committed.",
    "tests-failed": "❌ The generated tests failed verification and were not committed.",
    "agent-failed": "❌ The test agent failed before producing a patch.",
    "push-failed": "⚠️ Verified tests could not be pushed (the branch has probably moved on).",
}

DECISION_ICONS = {"create": "🆕 create", "update": "✏️ update", "skip": "⏭️ skip"}
RESULT_ICONS = {"passed": "✅ passed", "failed": "❌ failed", "skipped": "—"}
MAX_CELL = 300


def text(value: object, limit: int = MAX_CELL) -> str:
    """Neutralise untrusted text for a Markdown table cell.

    Escapes HTML and table syntax, joins lines and breaks @mentions so agent
    output cannot ping users or inject markup into the comment.
    """
    value = " ".join(str(value).split())
    if len(value) > limit:
        value = value[: limit - 1] + "…"
    for char, replacement in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;"), ("|", "\\|")):
        value = value.replace(char, replacement)
    return value.replace("@", "@​").replace("`", "'")


def code(value: object) -> str:
    return f"`{text(value)}`" if value else "—"


def percent(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}%"


def signed(value: float) -> str:
    return f"{value:+.2f} pp"


def read_json(path: Path | None) -> dict | None:
    if path is None or not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def read_junit(path: Path) -> dict:
    """Counts and failing test names from a JUnit XML report."""
    root = ElementTree.parse(path).getroot()
    summary = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "time": 0.0, "failures": []}
    for case in root.iter("testcase"):
        summary["time"] += float(case.get("time") or 0)
        name = f"{case.get('classname', '')}::{case.get('name', '')}".strip(":")
        if case.find("failure") is not None:
            summary["failed"] += 1
            summary["failures"].append(name)
        elif case.find("error") is not None:
            summary["errors"] += 1
            summary["failures"].append(name)
        elif case.find("skipped") is not None:
            summary["skipped"] += 1
        else:
            summary["passed"] += 1
    return summary


def decisions_section(report: dict) -> list[str]:
    targets = report.get("targets") or []
    if not targets:
        return []
    lines = [
        "### Agent decisions",
        "",
        "| Source file | Decision | Test file | Result | Skills | Reason |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for target in targets:
        decision = target.get("decision", "")
        result = target.get("result", "")
        attempts = target.get("attempts")
        if attempts and attempts > 1:
            result_text = f"{RESULT_ICONS.get(result, text(result))} ({attempts} attempts)"
        else:
            result_text = RESULT_ICONS.get(result, text(result) or "—")
        lines.append(
            f"| {code(target.get('source'))} | {DECISION_ICONS.get(decision, text(decision))} "
            f"| {code(target.get('test_file'))} | {result_text} "
            f"| {text(', '.join(target.get('skills') or [])) or '—'} | {text(target.get('reason', ''))} |"
        )
    return lines + [""]


def tests_section(junit: dict[str, dict]) -> list[str]:
    if not junit:
        return []
    lines = [
        "### Test results (independent verification)",
        "",
        "| Suite | Passed | Failed | Errors | Skipped | Time |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    failures: list[str] = []
    for suite, result in junit.items():
        lines.append(
            f"| {text(suite)} | {result['passed']} | {result['failed']} | {result['errors']} "
            f"| {result['skipped']} | {result['time']:.1f}s |"
        )
        failures += [f"{suite}: {name}" for name in result["failures"]]
    lines.append("")
    if failures:
        lines += ["<details><summary>Failing tests</summary>", ""]
        lines += [f"- {code(name)}" for name in failures[:20]]
        if len(failures) > 20:
            lines.append(f"- … and {len(failures) - 20} more")
        lines += ["", "</details>", ""]
    return lines


def coverage_section(coverage: dict) -> list[str]:
    components = coverage.get("components") or {}
    if not components:
        return []
    lines = [
        "### Coverage",
        "",
        "| Component | Lines before | Lines after | Δ lines | Branches before | Branches after | Δ branches |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    files: list[dict] = []
    for name, data in components.items():
        before, after, delta = data["before"], data["after"], data["delta"]
        lines.append(
            f"| {text(name)} | {percent(before['lines']['pct'])} | {percent(after['lines']['pct'])} "
            f"| {signed(delta['lines'])} | {percent(before['branches']['pct'])} "
            f"| {percent(after['branches']['pct'])} | {signed(delta['branches'])} |"
        )
        files += data.get("files") or []
    lines.append("")
    if files:
        lines += [
            "<details><summary>Changed coverage per file</summary>",
            "",
            "| File | Lines before | Lines after | Δ |",
            "| --- | ---: | ---: | ---: |",
        ]
        lines += [
            f"| {code(f['path'])} | {percent(f['lines_before'])} | {percent(f['lines_after'])} "
            f"| {signed(f['delta'])} |"
            for f in files
        ]
        lines += ["", "</details>", ""]
    return lines


def validation_section(validation: dict) -> list[str]:
    errors, warnings = validation.get("errors") or [], validation.get("warnings") or []
    if not errors and not warnings:
        return []
    lines = ["### Patch validation", ""]
    lines += [f"- ⛔ {text(error)}" for error in errors]
    lines += [f"- ⚠️ {text(warning)}" for warning in warnings]
    return lines + [""]


def footer(report: dict | None, run_url: str | None) -> list[str]:
    parts = []
    if report:
        model = report.get("model") or {}
        if model:
            name = f"{model.get('provider', '?')}/{model.get('name', '?')}"
            parts.append(f"Model: {code(name)}")
        usage = report.get("usage") or {}
        if "input_tokens" in usage:
            parts.append(f"Tokens: {usage['input_tokens']} in / {usage.get('output_tokens', 0)} out")
        if "duration_seconds" in usage:
            parts.append(f"Agent time: {usage['duration_seconds']:.0f}s")
    if run_url:
        parts.append(f"[Workflow run]({run_url})")
    return ["---", " · ".join(parts)] if parts else []


def render(
    status: str,
    report: dict | None = None,
    validation: dict | None = None,
    coverage: dict | None = None,
    junit: dict[str, dict] | None = None,
    commit_sha: str | None = None,
    run_url: str | None = None,
    detail: str | None = None,
) -> str:
    lines = [MARKER, "## 🤖 Test agent report", "", STATUSES[status]]
    if commit_sha:
        lines[-1] += f" Commit: {code(commit_sha[:12])}."
    if detail:
        lines += ["", f"> {text(detail, limit=1000)}"]
    lines.append("")
    if report:
        lines += decisions_section(report)
        errors = report.get("errors") or []
        if errors:
            lines += ["### Agent errors", ""] + [f"- {text(e)}" for e in errors] + [""]
    lines += tests_section(junit or {})
    lines += coverage_section(coverage or {})
    lines += validation_section(validation or {})
    lines += footer(report, run_url)
    return "\n".join(lines).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--status", required=True, choices=sorted(STATUSES))
    parser.add_argument("--agent-report", type=Path)
    parser.add_argument("--validation", type=Path)
    parser.add_argument("--coverage", type=Path)
    parser.add_argument("--junit", action="append", default=[], metavar="SUITE=PATH")
    parser.add_argument("--commit-sha")
    parser.add_argument("--run-url")
    parser.add_argument("--detail")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    junit = {}
    for item in args.junit:
        suite, _, path = item.partition("=")
        if Path(path).is_file():
            junit[suite] = read_junit(Path(path))

    markdown = render(
        args.status,
        report=read_json(args.agent_report),
        validation=read_json(args.validation),
        coverage=read_json(args.coverage),
        junit=junit,
        commit_sha=args.commit_sha,
        run_url=args.run_url,
        detail=args.detail,
    )
    if args.out:
        args.out.write_text(markdown, encoding="utf-8")
    else:
        sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
