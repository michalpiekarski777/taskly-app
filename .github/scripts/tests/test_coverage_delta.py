import json

import pytest

import coverage_delta


def pytest_report(files: dict[str, tuple[int, int, int, int]]) -> dict:
    def summary(covered, statements, covered_branches, branches):
        return {
            "covered_lines": covered,
            "num_statements": statements,
            "covered_branches": covered_branches,
            "num_branches": branches,
        }

    totals = [sum(values) for values in zip(*files.values())]
    return {
        "files": {name: {"summary": summary(*values)} for name, values in files.items()},
        "totals": summary(*totals),
    }


def vitest_report(root: str, files: dict[str, tuple[int, int, int, int]]) -> dict:
    def entry(covered, lines, covered_branches, branches):
        return {
            "lines": {"total": lines, "covered": covered, "skipped": 0, "pct": 0},
            "branches": {"total": branches, "covered": covered_branches, "skipped": 0, "pct": 0},
        }

    totals = [sum(values) for values in zip(*files.values())]
    report = {"total": entry(*totals)}
    report.update({f"{root}/{name}": entry(*values) for name, values in files.items()})
    return report


@pytest.fixture
def write(tmp_path):
    def write(name: str, data: dict):
        path = tmp_path / name
        path.write_text(json.dumps(data))
        return path

    return write


def test_backend_delta_lists_only_files_whose_coverage_changed(write):
    before = write("b.json", pytest_report({"app/a.py": (5, 10, 1, 4), "app/b.py": (2, 2, 0, 0)}))
    after = write(
        "a.json",
        pytest_report({"app/a.py": (10, 10, 4, 4), "app/b.py": (2, 2, 0, 0), "app/c.py": (1, 4, 0, 0)}),
    )
    result = coverage_delta.compare(coverage_delta.read_pytest(before), coverage_delta.read_pytest(after))

    assert result["before"]["lines"] == {"covered": 7, "total": 12, "pct": 58.33}
    assert result["after"]["lines"] == {"covered": 13, "total": 16, "pct": 81.25}
    assert result["delta"] == {"lines": 22.92, "branches": 75.0}
    assert result["files"] == [
        {"path": "backend/app/a.py", "lines_before": 50.0, "lines_after": 100.0, "delta": 50.0},
        {"path": "backend/app/c.py", "lines_before": None, "lines_after": 25.0, "delta": 25.0},
    ]


def test_files_without_statements_count_as_fully_covered(write):
    report = write("r.json", pytest_report({"app/empty.py": (0, 0, 0, 0)}))
    totals, files = coverage_delta.read_pytest(report)
    assert files["backend/app/empty.py"]["lines"]["pct"] == 100.0


def test_frontend_paths_are_made_relative_to_the_root(write):
    root = "/runner/work/repo/frontend"
    before = write("b.json", vitest_report(root, {"src/lib/http.ts": (0, 10, 0, 6)}))
    after = write("a.json", vitest_report(root, {"src/lib/http.ts": (9, 10, 5, 6)}))
    result = coverage_delta.compare(
        coverage_delta.read_vitest(before, root), coverage_delta.read_vitest(after, root)
    )
    assert result["files"][0]["path"] == "frontend/src/lib/http.ts"
    assert result["delta"]["lines"] == 90.0


def test_main_combines_components(write, tmp_path):
    root = "/w/frontend"
    out = tmp_path / "delta.json"
    code = coverage_delta.main(
        [
            "--backend-before", str(write("bb.json", pytest_report({"app/a.py": (1, 2, 0, 0)}))),
            "--backend-after", str(write("ba.json", pytest_report({"app/a.py": (2, 2, 0, 0)}))),
            "--frontend-before", str(write("fb.json", vitest_report(root, {"src/a.ts": (1, 2, 0, 0)}))),
            "--frontend-after", str(write("fa.json", vitest_report(root, {"src/a.ts": (1, 2, 0, 0)}))),
            "--frontend-root", root,
            "--out", str(out),
        ]
    )
    assert code == 0
    result = json.loads(out.read_text())["components"]
    assert result["backend"]["delta"]["lines"] == 50.0
    assert result["frontend"]["files"] == []


def test_main_requires_at_least_one_component():
    with pytest.raises(SystemExit):
        coverage_delta.main([])
