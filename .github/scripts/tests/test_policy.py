import re
import tomllib
from pathlib import Path

import pytest

from policy import glob_to_regex, is_safe_relative_path, matches_any

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize(
    ("pattern", "path", "expected"),
    [
        ("backend/app/**/*.py", "backend/app/main.py", True),
        ("backend/app/**/*.py", "backend/app/features/todos/router.py", True),
        ("backend/app/**/*.py", "backend/application/x.py", False),
        ("backend/app/*.py", "backend/app/core/config.py", False),
        ("backend/app/**/__init__.py", "backend/app/__init__.py", True),
        ("frontend/src/**/*.{ts,tsx}", "frontend/src/lib/http.ts", True),
        ("frontend/src/**/*.{ts,tsx}", "frontend/src/App.tsx", True),
        ("frontend/src/**/*.{ts,tsx}", "frontend/src/styles.css", False),
        ("frontend/src/test/**", "frontend/src/test/setup.ts", True),
        ("backend/tests/**/test_*.py", "backend/tests/test_x.py", True),
        ("backend/tests/**/test_*.py", "backend/tests/conftest.py", False),
        ("a?c", "abc", True),
        ("a?c", "a/c", False),
        ("a.b", "aXb", False),
    ],
)
def test_glob_to_regex(pattern, path, expected):
    assert bool(glob_to_regex(pattern).match(path)) is expected


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("backend/tests/test_x.py", True),
        ("", False),
        ("/etc/passwd", False),
        ("backend/../.github/workflows/ci.yml", False),
        ("./backend/tests/test_x.py", False),
        ("backend//tests/test_x.py", False),
        (".git/hooks/pre-commit", False),
        ("backend\\tests\\test_x.py", False),
        ("backend/tests/test_\nx.py", False),
    ],
)
def test_is_safe_relative_path(path, expected):
    assert is_safe_relative_path(path) is expected


def test_policy_classifies_repository_files(policy):
    assert policy.source_component("backend/app/core/config.py").name == "backend"
    assert policy.source_component("frontend/src/lib/http.ts").name == "frontend"
    assert policy.source_component("backend/app/features/todos/schemas.py") is None
    assert policy.source_component("frontend/src/lib/http.test.ts") is None
    assert policy.test_component("backend/tests/api/test_health.py").name == "backend"
    assert policy.test_component("frontend/src/lib/http.test.ts").name == "frontend"
    assert policy.test_component("backend/tests/conftest.py") is None
    assert policy.test_component("frontend/src/test/setup.ts") is None


def test_policy_excludes_everything_backend_coverage_omits(policy):
    pyproject = tomllib.loads((REPO_ROOT / "backend/pyproject.toml").read_text(encoding="utf-8"))
    backend = next(c for c in policy.components if c.name == "backend")
    for omitted in pyproject["tool"]["coverage"]["run"]["omit"]:
        assert omitted.startswith("*/app/")
        pattern = "backend/" + omitted.removeprefix("*/")
        assert pattern in backend.exclude, f"{pattern} is missing from test-agent.toml"


def test_policy_excludes_everything_frontend_coverage_excludes(policy):
    vite_config = (REPO_ROOT / "frontend/vite.config.ts").read_text(encoding="utf-8")
    block = re.search(r"exclude:\s*\[(.*?)\]", vite_config, re.DOTALL)
    assert block, "coverage.exclude not found in vite.config.ts"
    frontend = next(c for c in policy.components if c.name == "frontend")
    for excluded in re.findall(r"'([^']+)'", block.group(1)):
        pattern = "frontend/" + excluded
        assert pattern in frontend.exclude, f"{pattern} is missing from test-agent.toml"


def test_matches_any():
    assert matches_any("a/b.py", ("x/*", "a/*.py"))
    assert not matches_any("a/b.py", ())
