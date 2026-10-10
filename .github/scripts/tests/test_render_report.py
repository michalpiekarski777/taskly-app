import json

import pytest

import render_report
from render_report import MARKER, code, read_junit, render, text

AGENT_REPORT = {
    "schema_version": 1,
    "model": {"provider": "anthropic", "name": "claude-sonnet-5-5"},
    "targets": [
        {
            "component": "backend",
            "source": "backend/app/api/health.py",
            "decision": "create",
            "test_file": "backend/tests/api/test_health.py",
            "reason": "New endpoint without tests",
            "skills": ["pytest-unit-tests", "run-and-fix-tests"],
            "attempts": 2,
            "result": "passed",
        },
        {
            "component": "frontend",
            "source": "frontend/src/styles.ts",
            "decision": "skip",
            "reason": "Styling only",
            "result": "skipped",
        },
    ],
    "usage": {"input_tokens": 1200, "output_tokens": 340, "duration_seconds": 42.4},
    "errors": [],
}

JUNIT = """<?xml version="1.0"?>
<testsuites>
  <testsuite name="pytest">
    <testcase classname="tests.api.test_health" name="test_ok" time="0.5"/>
    <testcase classname="tests.api.test_health" name="test_bad" time="0.25"><failure message="x"/></testcase>
    <testcase classname="tests.api.test_health" name="test_broken" time="0"><error message="y"/></testcase>
    <testcase classname="tests.api.test_health" name="test_later"><skipped/></testcase>
  </testsuite>
</testsuites>
"""


def test_text_neutralises_html_tables_and_mentions():
    assert text("a | b") == "a \\| b"
    assert text("<img src=x>") == "&lt;img src=x&gt;"
    assert text("ping @octocat") == "ping @​octocat"
    assert text("line one\nline two") == "line one line two"
    assert text("x" * 400).endswith("…") and len(text("x" * 400)) == 300


@pytest.mark.parametrize(
    "payload",
    [
        "![x](https://evil.example/pixel.png)",
        "[click here](https://phish.example)",
        "visit https://phish.example now",
        "visit www.phish.example now",
        "**bold** _it_ ~~gone~~ `code`",
    ],
)
def test_text_cannot_produce_links_images_or_formatting(payload):
    escaped = text(payload)
    assert "](" not in escaped and "https://" not in escaped and "www." not in escaped
    for char in "[]!*_~`":
        assert escaped.count(char) == escaped.count("\\" + char)


def test_code_keeps_text_literal_inside_the_span():
    assert code("test_x[a<b] & c") == "`test_x[a<b] & c`"
    assert code("a`b|c") == "`a'b\\|c`"
    assert code(None) == "—"


def test_read_junit_counts_each_outcome(tmp_path):
    path = tmp_path / "junit.xml"
    path.write_text(JUNIT)
    summary = read_junit(path)
    assert {k: summary[k] for k in ("passed", "failed", "errors", "skipped")} == {
        "passed": 1,
        "failed": 1,
        "errors": 1,
        "skipped": 1,
    }
    assert summary["time"] == 0.75
    assert summary["failures"] == ["tests.api.test_health::test_bad", "tests.api.test_health::test_broken"]


def test_render_full_report():
    markdown = render(
        "committed",
        report=AGENT_REPORT,
        validation={"valid": True, "errors": [], "warnings": ["backend/tests/x.py: reads environment variables"]},
        coverage={
            "components": {
                "backend": {
                    "before": {"lines": {"pct": 42.0}, "branches": {"pct": 50.0}},
                    "after": {"lines": {"pct": 55.5}, "branches": {"pct": 60.0}},
                    "delta": {"lines": 13.5, "branches": 10.0},
                    "files": [
                        {
                            "path": "backend/app/api/health.py",
                            "lines_before": 0.0,
                            "lines_after": 100.0,
                            "branches_before": None,
                            "branches_after": 100.0,
                        }
                    ],
                }
            }
        },
        junit={"backend": {"passed": 30, "failed": 0, "errors": 0, "skipped": 0, "time": 3.2, "failures": []}},
        commit_sha="0123456789abcdef",
        run_url="https://github.com/o/r/actions/runs/1",
    )
    assert markdown.startswith(MARKER)
    assert "Commit: `0123456789ab`." in markdown
    assert (
        "| `backend/app/api/health.py` | 🆕 create | `backend/tests/api/test_health.py` "
        "| ✅ passed (2 attempts) | pytest\\-unit\\-tests, run\\-and\\-fix\\-tests | New endpoint without tests |"
    ) in markdown
    assert "| `frontend/src/styles.ts` | ⏭️ skip | — | — |" in markdown
    assert "| backend | 30 | 0 | 0 | 0 | 3.2s |" in markdown
    assert "| backend | 42.00% | 55.50% | +13.50 pp | 50.00% | 60.00% | +10.00 pp |" in markdown
    assert "| `backend/app/api/health.py` | 0.00% | 100.00% | — | 100.00% |" in markdown
    assert "⚠️ backend/tests/x.py: reads environment variables" in markdown
    assert "Model: `anthropic/claude-sonnet-5-5`" in markdown
    assert "Tokens: 1200 in / 340 out" in markdown
    assert "[Workflow run](<https://github.com/o/r/actions/runs/1>)" in markdown


def test_render_without_any_inputs():
    markdown = render("agent-failed", detail="Model returned @someone <b>")
    assert render_report.STATUSES["agent-failed"] in markdown
    assert "> Model returned @​someone &lt;b&gt;" in markdown
    assert "###" not in markdown


def test_render_lists_failing_tests_and_validation_errors():
    markdown = render(
        "rejected",
        validation={"errors": ["backend/app/main.py: not an allowed test file"], "warnings": []},
        junit={"backend": {"passed": 0, "failed": 1, "errors": 0, "skipped": 0, "time": 0, "failures": ["t::x"]}},
    )
    assert "- ⛔ backend/app/main.py: not an allowed test file" in markdown
    assert "<summary>Failing tests</summary>" in markdown
    assert "- `backend: t::x`" in markdown


def test_render_survives_a_malformed_agent_report():
    report = {
        "model": "not a dict",
        "targets": ["oops", {"attempts": "2", "skills": [1, None], "decision": 5, "result": ["x"]}],
        "usage": {"input_tokens": "@org/admins ![x](https://evil/p)", "output_tokens": 1, "duration_seconds": "12"},
        "errors": "not a list",
    }
    markdown = render("committed", report=report, coverage={"components": {"backend": "bad"}})
    assert "@org" not in markdown
    assert "https://evil" not in markdown
    assert "Tokens:" not in markdown and "Agent time:" not in markdown


def test_render_ignores_non_https_run_urls():
    assert "Workflow run" not in render("skipped", run_url="javascript:alert(1)")


def test_main_reads_files_and_ignores_missing_ones(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps(AGENT_REPORT))
    junit = tmp_path / "junit.xml"
    junit.write_text(JUNIT)
    out = tmp_path / "report.md"
    code_ = render_report.main(
        [
            "--status", "tests-failed",
            "--agent-report", str(report),
            "--validation", str(tmp_path / "missing.json"),
            "--junit", f"backend={junit}",
            "--junit", f"frontend={tmp_path / 'missing.xml'}",
            "--out", str(out),
        ]
    )
    markdown = out.read_text()
    assert code_ == 0
    assert "| backend | 1 | 1 | 1 | 1 |" in markdown
    assert "| frontend |" not in markdown
    assert "Report problems" not in markdown


def test_main_reports_unreadable_inputs(tmp_path):
    report = tmp_path / "report.json"
    report.write_text("{truncated")
    junit = tmp_path / "junit.xml"
    junit.write_text("<testsuites><testcase")
    out = tmp_path / "report.md"
    render_report.main(
        ["--status", "agent-failed", "--agent-report", str(report), "--junit", f"backend={junit}", "--out", str(out)]
    )
    markdown = out.read_text()
    assert "### Report problems" in markdown
    assert "report.json could not be read" in markdown
    assert "junit.xml could not be parsed" in markdown
