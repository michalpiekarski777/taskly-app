"""Render the test agent's pull request comment and job summary as Markdown.

Every input is optional and the agent's report is treated as untrusted: wrong
types are ignored and all text is escaped, so a report can be produced for any
outcome, including runs where the agent crashed or wrote garbage.

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
import re
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
    "verify-failed": "❌ Verification could not be completed; nothing was committed.",
    "push-failed": "⚠️ Verified tests could not be pushed (the branch has probably moved on).",
}

DECISION_ICONS = {"create": "🆕 create", "update": "✏️ update", "skip": "⏭️ skip"}
RESULT_ICONS = {"passed": "✅ passed", "failed": "❌ failed", "skipped": "—", "not-run": "not run by agent"}
MAX_CELL = 300

# Markdown that could turn untrusted text into links, images or formatting.
_MARKDOWN_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-!|~>])")


def _clip(value: object, limit: int) -> str:
    value = " ".join(str(value).split())
    return value[: limit - 1] + "…" if len(value) > limit else value


def text(value: object, limit: int = MAX_CELL) -> str:
    """Neutralise untrusted text for Markdown (including table cells).

    Escapes HTML and Markdown syntax, joins lines, and breaks @mentions and
    bare URLs so agent output cannot ping users or inject links and images.
    """
    value = _clip(value, limit)
    for char, replacement in (("&", "&amp;"), ("<", "&lt;"), (">", "&gt;")):
        value = value.replace(char, replacement)
    value = _MARKDOWN_SPECIAL.sub(r"\\\1", value)
    return value.replace("@", "@​").replace("://", ":​//").replace("www.", "www​.")


def code(value: object) -> str:
    """Untrusted text as an inline code span (rendered literally by GFM)."""
    if not value:
        return "—"
    value = _clip(value, MAX_CELL).replace("`", "'").replace("|", "\\|")
    return f"`{value}`"


def percent(value: object) -> str:
    return f"{value:.2f}%" if _is_number(value) else "—"


def signed(value: object) -> str:
    return f"{value:+.2f} pp" if _is_number(value) else "—"


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _dict(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list:
    return value if isinstance(value, list) else []


def _label(value: object) -> str:
    return value if isinstance(value, str) else ""


def read_json(path: Path | None) -> tuple[dict | None, str | None]:
    """(data, problem): a missing file is fine, an unreadable one is reported."""
    if path is None or not path.is_file():
        return None, None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        return None, f"{path.name} could not be read: {error}"
    if not isinstance(data, dict):
        return None, f"{path.name} is not a JSON object"
    return data, None


def read_junit(path: Path) -> dict:
    """Counts and failing test names from a JUnit XML report."""
    root = ElementTree.parse(path).getroot()
    summary = {"passed": 0, "failed": 0, "errors": 0, "skipped": 0, "time": 0.0, "failures": []}
    for case in root.iter("testcase"):
        try:
            summary["time"] += float(case.get("time") or 0)
        except ValueError:
            pass
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
    targets = [t for t in _list(report.get("targets")) if isinstance(t, dict)]
    if not targets:
        return []
    lines = [
        "### Agent decisions",
        "",
        "| Source file | Decision | Test file | Result | Skills | Reason |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for target in targets:
        decision, result = _label(target.get("decision")), _label(target.get("result"))
        result_text = RESULT_ICONS.get(result) or (text(result) if result else "—")
        attempts = target.get("attempts")
        if _is_number(attempts) and attempts > 1:
            result_text += f" ({int(attempts)} attempts)"
        skills = ", ".join(str(s) for s in _list(target.get("skills")))
        lines.append(
            f"| {code(target.get('source'))} "
            f"| {DECISION_ICONS.get(decision) or text(decision or '—')} "
            f"| {code(target.get('test_file'))} | {result_text} "
            f"| {text(skills) or '—'} | {text(target.get('reason') or '')} |"
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


def _pct(data: dict, side: str, metric: str) -> object:
    return _dict(_dict(data.get(side)).get(metric)).get("pct")


def coverage_section(coverage: dict) -> list[str]:
    components = _dict(coverage.get("components"))
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
        data = _dict(data)
        delta = _dict(data.get("delta"))
        lines.append(
            f"| {text(name)} | {percent(_pct(data, 'before', 'lines'))} "
            f"| {percent(_pct(data, 'after', 'lines'))} | {signed(delta.get('lines'))} "
            f"| {percent(_pct(data, 'before', 'branches'))} | {percent(_pct(data, 'after', 'branches'))} "
            f"| {signed(delta.get('branches'))} |"
        )
        files += [f for f in _list(data.get("files")) if isinstance(f, dict)]
    lines.append("")
    if files:
        lines += [
            "<details><summary>Changed coverage per file</summary>",
            "",
            "| File | Lines before | Lines after | Branches before | Branches after |",
            "| --- | ---: | ---: | ---: | ---: |",
        ]
        lines += [
            f"| {code(f.get('path'))} | {percent(f.get('lines_before'))} | {percent(f.get('lines_after'))} "
            f"| {percent(f.get('branches_before'))} | {percent(f.get('branches_after'))} |"
            for f in files
        ]
        lines += ["", "</details>", ""]
    return lines


def validation_section(validation: dict) -> list[str]:
    errors, warnings = _list(validation.get("errors")), _list(validation.get("warnings"))
    if not errors and not warnings:
        return []
    lines = ["### Patch validation", ""]
    lines += [f"- ⛔ {text(error)}" for error in errors]
    lines += [f"- ⚠️ {text(warning)}" for warning in warnings]
    return lines + [""]


def footer(report: dict | None, run_url: str | None) -> list[str]:
    parts = []
    if report:
        model = _dict(report.get("model"))
        if model:
            name = f"{model.get('provider', '?')}/{model.get('name', '?')}"
            parts.append(f"Model: {code(name)}")
        usage = _dict(report.get("usage"))
        tokens_in, tokens_out = usage.get("input_tokens"), usage.get("output_tokens")
        if _is_number(tokens_in) and _is_number(tokens_out):
            parts.append(f"Tokens: {int(tokens_in)} in / {int(tokens_out)} out")
        if _is_number(usage.get("duration_seconds")):
            parts.append(f"Agent time: {usage['duration_seconds']:.0f}s")
    if run_url and run_url.startswith("https://"):
        parts.append(f"[Workflow run](<{run_url.replace('>', '%3E')}>)")
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
    problems: list[str] | None = None,
) -> str:
    lines = [MARKER, "## 🤖 Test agent report", "", STATUSES[status]]
    if commit_sha:
        lines[-1] += f" Commit: {code(commit_sha[:12])}."
    if detail:
        lines += ["", f"> {text(detail, limit=1000)}"]
    lines.append("")
    if report:
        lines += decisions_section(report)
        errors = _list(report.get("errors"))
        if errors:
            lines += ["### Agent errors", ""] + [f"- {text(e)}" for e in errors] + [""]
    lines += tests_section(junit or {})
    lines += coverage_section(coverage or {})
    lines += validation_section(validation or {})
    if problems:
        lines += ["### Report problems", ""] + [f"- {text(p)}" for p in problems] + [""]
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

    problems: list[str] = []
    inputs = {}
    for name in ("agent_report", "validation", "coverage"):
        inputs[name], problem = read_json(getattr(args, name))
        if problem:
            problems.append(problem)

    junit = {}
    for item in args.junit:
        suite, _, path = item.partition("=")
        if Path(path).is_file():
            try:
                junit[suite] = read_junit(Path(path))
            except ElementTree.ParseError as error:
                problems.append(f"{Path(path).name} could not be parsed: {error}")

    markdown = render(
        args.status,
        report=inputs["agent_report"],
        validation=inputs["validation"],
        coverage=inputs["coverage"],
        junit=junit,
        commit_sha=args.commit_sha,
        run_url=args.run_url,
        detail=args.detail,
        problems=problems,
    )
    if args.out:
        args.out.write_text(markdown, encoding="utf-8")
    else:
        sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
