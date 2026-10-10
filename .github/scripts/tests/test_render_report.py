import json

import render_report
from render_report import MARKER, read_junit, render, text

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


def test_text_neutralises_markdown_html_and_mentions():
    assert text("a | b") == "a \\| b"
    assert text("<img src=x onerror=alert(1)>") == "&lt;img src=x onerror=alert(1)&gt;"
    assert text("ping @octocat") == "ping @​octocat"
    assert text("line one\nline two") == "line one line two"
    assert text("`code`") == "'code'"
    assert text("x" * 400).endswith("…") and len(text("x" * 400)) == 300


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
                        {"path": "backend/app/api/health.py", "lines_before": 0.0, "lines_after": 100.0, "delta": 100.0}
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
    assert "| `backend/app/api/health.py` | 🆕 create | `backend/tests/api/test_health.py` | ✅ passed (2 attempts) |" in markdown
    assert "| backend | 30 | 0 | 0 | 0 | 3.2s |" in markdown
    assert "| backend | 42.00% | 55.50% | +13.50 pp | 50.00% | 60.00% | +10.00 pp |" in markdown
    assert "⚠️ backend/tests/x.py: reads environment variables" in markdown
    assert "Model: `anthropic/claude-sonnet-5-5`" in markdown
    assert "Tokens: 1200 in / 340 out" in markdown
    assert "[Workflow run](https://github.com/o/r/actions/runs/1)" in markdown


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


def test_main_reads_files_and_ignores_missing_ones(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(json.dumps(AGENT_REPORT))
    junit = tmp_path / "junit.xml"
    junit.write_text(JUNIT)
    out = tmp_path / "report.md"
    code = render_report.main(
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
    assert code == 0
    assert "| backend | 1 | 1 | 1 | 1 |" in markdown
    assert "| frontend |" not in markdown
