import json

import pytest

from test_agent import cli


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    for path in (
        "backend/app/api/health.py",
        "backend/app/api/dependencies.py",
        "backend/app/core/config.py",
        "frontend/src/lib/http.ts",
    ):
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text("x = 1\n")
    (root / "backend/tests/api").mkdir(parents=True)
    (root / "backend/tests/api/__init__.py").write_text("")
    (root / "backend/tests/api/test_dependencies.py").write_text("existing\n")
    return root


def run_agent(tmp_path, repo, sources, *extra):
    changes = tmp_path / "changes.json"
    components = {"backend": {"changed_sources": []}, "frontend": {"changed_sources": []}}
    for source in sources:
        components[source.split("/")[0]]["changed_sources"].append(source)
    changes.write_text(json.dumps({"components": components}))
    out = tmp_path / "out"
    code = cli.main(["run", "--repo", str(repo), "--changes", str(changes), "--out", str(out), *extra])
    return code, json.loads((out / "report.json").read_text())


def test_stub_creates_smoke_tests_and_reports_decisions(tmp_path, repo):
    code, report = run_agent(
        tmp_path,
        repo,
        [
            "backend/app/api/health.py",
            "backend/app/api/dependencies.py",
            "backend/app/core/config.py",
            "frontend/src/lib/http.ts",
        ],
    )
    assert code == cli.EXIT_OK
    assert report["schema_version"] == 1 and report["engine"] == "stub"
    decisions = {t["source"]: (t["decision"], t["test_file"]) for t in report["targets"]}
    assert decisions == {
        "backend/app/api/health.py": ("create", "backend/tests/api/test_health.py"),
        "backend/app/api/dependencies.py": ("skip", "backend/tests/api/test_dependencies.py"),
        "backend/app/core/config.py": ("skip", "backend/tests/core/test_config.py"),
        "frontend/src/lib/http.ts": ("create", "frontend/src/lib/http.test.ts"),
    }
    assert "import app.api.health as module" in (repo / "backend/tests/api/test_health.py").read_text()
    assert "import * as module from './http'" in (repo / "frontend/src/lib/http.test.ts").read_text()
    assert (repo / "backend/tests/api/test_dependencies.py").read_text() == "existing\n"
    assert not (repo / "backend/tests/core").exists()


def test_stub_failing_scenario_writes_failing_tests(tmp_path, repo, monkeypatch):
    monkeypatch.setenv("TEST_AGENT_STUB_SCENARIO", "failing")
    run_agent(tmp_path, repo, ["backend/app/api/health.py"])
    assert "assert False" in (repo / "backend/tests/api/test_health.py").read_text()


def test_stub_forbidden_scenario_modifies_a_source_file(tmp_path, repo, monkeypatch):
    monkeypatch.setenv("TEST_AGENT_STUB_SCENARIO", "forbidden")
    run_agent(tmp_path, repo, ["backend/app/api/health.py"])
    assert "forbidden" in (repo / "backend/app/api/health.py").read_text()


def test_stub_none_scenario_writes_nothing(tmp_path, repo, monkeypatch):
    monkeypatch.setenv("TEST_AGENT_STUB_SCENARIO", "none")
    code, report = run_agent(tmp_path, repo, ["backend/app/api/health.py"])
    assert code == cli.EXIT_OK
    assert [t["decision"] for t in report["targets"]] == ["skip"]
    assert not (repo / "backend/tests/api/test_health.py").exists()


def test_unknown_engine_is_a_configuration_error(tmp_path, repo):
    code, report = run_agent(tmp_path, repo, [], "--engine", "langgraph")
    assert code == cli.EXIT_NOT_CONFIGURED
    assert "Unknown engine 'langgraph'" in report["errors"][0]


def test_failures_still_produce_a_report(tmp_path, repo, monkeypatch):
    monkeypatch.setenv("TEST_AGENT_STUB_SCENARIO", "bogus")
    code, report = run_agent(tmp_path, repo, ["backend/app/api/health.py"])
    assert code == cli.EXIT_FAILED
    assert report["errors"][0].startswith("ValueError: Unknown TEST_AGENT_STUB_SCENARIO")
