import json

import pytest

import detect_changes
from detect_changes import classify, parse_name_status


def test_parse_name_status_splits_renames_into_delete_and_add():
    output = "M\0a.py\0R087\0old.py\0new.py\0C100\0src.py\0copy.py\0D\0gone.py\0"
    assert parse_name_status(output) == [
        ("M", "a.py"),
        ("D", "old.py"),
        ("A", "new.py"),
        ("A", "copy.py"),
        ("D", "gone.py"),
    ]


def test_parse_name_status_handles_empty_output():
    assert parse_name_status("") == []


def test_classify_groups_files_by_component(policy):
    result = classify(
        [
            ("M", "backend/app/core/config.py"),
            ("A", "frontend/src/lib/http.ts"),
            ("D", "backend/app/api/health.py"),
            ("M", "backend/tests/api/test_dependencies.py"),
            ("D", "frontend/src/App.test.tsx"),
            ("M", "backend/app/features/todos/schemas.py"),
            ("M", "README.md"),
            ("M", "../escape.py"),
        ],
        policy,
    )
    assert result["has_testable_changes"] is True
    assert result["components"]["backend"] == {
        "changed_sources": ["backend/app/core/config.py"],
        "deleted_sources": ["backend/app/api/health.py"],
        "changed_tests": ["backend/tests/api/test_dependencies.py"],
    }
    assert result["components"]["frontend"]["changed_sources"] == ["frontend/src/lib/http.ts"]
    assert result["components"]["frontend"]["changed_tests"] == []
    assert result["ignored"] == ["backend/app/features/todos/schemas.py", "README.md", "../escape.py"]


def test_classify_reports_no_testable_changes_for_docs_only(policy):
    result = classify([("M", "README.md"), ("A", "backend/tests/test_new.py")], policy)
    assert result["has_testable_changes"] is False


@pytest.mark.parametrize("revision", ["--output=/tmp/x", "-p", "main;rm", "a b", ""])
def test_rejects_revisions_git_could_read_as_options(tmp_path, revision):
    with pytest.raises(ValueError):
        detect_changes.git_name_status(tmp_path, revision, "HEAD")


def test_main_diffs_from_the_merge_base(repo, tmp_path, monkeypatch):
    repo.write("backend/app/core/config.py", "A = 1\n")
    base = repo.commit("base")
    repo.git("switch", "-q", "-c", "feature")
    repo.write("backend/app/core/config.py", "A = 2\n")
    repo.write("frontend/src/lib/http.ts", "export const x = 1\n")
    head = repo.commit("feature")
    # A later commit on main must not show up as a change of the feature branch.
    repo.git("switch", "-q", "main")
    repo.write("backend/app/api/health.py", "B = 1\n")
    main_head = repo.commit("main moves on")

    out = tmp_path / "changes.json"
    github_output = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(github_output))
    code = detect_changes.main(
        ["--repo", str(repo.root), "--base", main_head, "--head", head, "--out", str(out), "--github-output"]
    )

    assert code == 0
    result = json.loads(out.read_text())
    assert result["components"]["backend"]["changed_sources"] == ["backend/app/core/config.py"]
    assert result["components"]["frontend"]["changed_sources"] == ["frontend/src/lib/http.ts"]
    assert github_output.read_text() == "has_testable_changes=true\ncomponents=backend,frontend\n"
    assert base
