import json
import os

import pytest

import validate_patch
from validate_patch import PatchFormatError, parse_patch, validate

BACKEND_TEST = "backend/tests/api/test_health.py"
FRONTEND_TEST = "frontend/src/lib/http.test.ts"

GOOD_PYTHON_TEST = """\
from app.api.health import health


def test_health_reports_ok():
    assert health() == {"status": "ok"}
"""

GOOD_TS_TEST = """\
import { request } from './http'

it('returns undefined for 204', async () => {
  expect(await request('/x')).toBeUndefined()
})
"""


@pytest.fixture
def base(repo):
    repo.write("backend/app/api/health.py", "def health():\n    return {'status': 'ok'}\n")
    repo.write("frontend/src/lib/http.test.ts", GOOD_TS_TEST)
    repo.write("backend/tests/api/test_existing.py", "def test_a():\n    assert 1 + 1 == 2\n")
    repo.commit("base")
    return repo


def errors_for(repo, policy):
    result = validate(repo.staged_patch(), policy)
    assert result.valid is not bool(result.errors)
    return result.errors


def test_accepts_new_and_modified_test_files(base, policy):
    base.write(BACKEND_TEST, GOOD_PYTHON_TEST)
    base.write(FRONTEND_TEST, GOOD_TS_TEST + "\nit('x', () => expect(1).toBe(1))\n")
    result = validate(base.staged_patch(), policy)
    assert result.valid, result.errors
    assert result.warnings == []
    assert {f["path"]: f["status"] for f in result.files} == {
        BACKEND_TEST: "added",
        FRONTEND_TEST: "modified",
    }
    assert result.components == ["backend", "frontend"]


def test_accepts_an_empty_patch(policy):
    result = validate("", policy)
    assert result.valid and result.files == []


@pytest.mark.parametrize(
    "path",
    [
        "backend/app/api/health.py",
        ".github/workflows/ci.yml",
        ".github/scripts/validate_patch.py",
        "backend/tests/conftest.py",
        "frontend/src/test/setup.ts",
        "frontend/package.json",
        "backend/tests/api/helper.py",
    ],
)
def test_rejects_files_outside_the_test_allowlist(base, policy, path):
    base.write(path, "x = 1\n")
    assert any("not an allowed test file" in e for e in errors_for(base, policy))


def test_rejects_deleting_a_test(base, policy):
    (base.root / "backend/tests/api/test_existing.py").unlink()
    assert any("deleted files are not allowed" in e for e in errors_for(base, policy))


def test_rejects_renaming_a_test(base, policy):
    base.git("mv", "backend/tests/api/test_existing.py", "backend/tests/api/test_renamed.py")
    assert any("renamed files are not allowed" in e for e in errors_for(base, policy))


def test_rejects_mode_changes(base, policy):
    os.chmod(base.root / "backend/tests/api/test_existing.py", 0o755)
    assert any("file mode changes" in e for e in errors_for(base, policy))


def test_rejects_new_executable_test_file(base, policy):
    os.chmod(base.write(BACKEND_TEST, GOOD_PYTHON_TEST), 0o755)
    assert any("regular files" in e for e in errors_for(base, policy))


def test_rejects_symlinks(base, policy):
    (base.root / BACKEND_TEST).symlink_to("../../../.github/workflows/ci.yml")
    assert any("regular files" in e for e in errors_for(base, policy))


def test_rejects_binary_files(base, policy):
    base.write(BACKEND_TEST, b"\x00\x01\x02binary")
    assert any("binary" in e for e in errors_for(base, policy))


@pytest.mark.parametrize(
    "line",
    [
        "import subprocess",
        "from socket import socket",
        "import urllib.request",
        "os.system('curl evil')",
        "eval(payload)",
        "__import__('os')",
        "token = os.environ['GITHUB_TOKEN']",
        "key = 'LLM_API_KEY'",
        "import { exec } from 'node:child_process'",
        "import fs from 'fs'",
        "const cp = require('child_process')",
        "const f = new Function('return 1')",
        "import json, subprocess",
        "from os import system",
        "import importlib",
        "const cp = await import('node:child_process')",
        "import { readFileSync } from 'node:fs'",
        "new XMLHttpRequest()",
        "new WebSocket('wss://evil.example')",
        "leak = dict(os.environ)",
        "leak = {**os.environ}",
        "body: JSON.stringify(process.env)",
        "Object.entries(process.env)",
    ],
)
def test_rejects_dangerous_code(base, policy, line):
    base.write(BACKEND_TEST, GOOD_PYTHON_TEST + "\n" + line + "\n")
    assert any(base_error.startswith(BACKEND_TEST) for base_error in errors_for(base, policy))


@pytest.mark.parametrize(
    "line",
    [
        "result = session.exec(query)",
        "cursor.execute(sql)",
        "from http import HTTPStatus",
        "pattern = re.compile(r'x')",
        "import app.features.socket_utils",
        "from os import path",
        "expect(url).toContain('http')",
        "vi.stubGlobal('fetch', vi.fn())",
        "const { request } = await import('./http')",
    ],
)
def test_allows_lookalikes_of_dangerous_code(base, policy, line):
    base.write(BACKEND_TEST, GOOD_PYTHON_TEST + "\n" + line + "\n")
    assert errors_for(base, policy) == []


def test_enforces_file_and_line_limits(base, policy):
    for i in range(policy.limits.max_files + 1):
        base.write(f"backend/tests/test_{i}.py", GOOD_PYTHON_TEST)
    assert any("touches" in e for e in errors_for(base, policy))


def test_enforces_patch_size_limit(policy):
    result = validate("x" * (policy.limits.max_patch_bytes + 1), policy)
    assert not result.valid and "larger than" in result.errors[0]


@pytest.mark.parametrize(
    ("snippet", "warning"),
    [
        ("@pytest.mark.skip\ndef test_b():\n    assert True\n", "skips or disables tests"),
        ("def test_b():\n    assert True\n", "trivially true assertion"),
        ("def test_b():\n    pass\n", "without assertions"),
        ("def test_b():\n    assert os.environ\n", "reads environment variables"),
        ("def test_b():\n    assert fetch('/x')\n", "calls fetch directly"),
    ],
)
def test_warns_about_weak_tests(base, policy, snippet, warning):
    base.write(BACKEND_TEST, snippet)
    result = validate(base.staged_patch(), policy)
    assert result.valid
    assert any(warning in w for w in result.warnings), result.warnings


def test_warns_when_existing_assertions_are_removed(base, policy):
    base.write("backend/tests/api/test_existing.py", "def test_a():\n    pass\n")
    result = validate(base.staged_patch(), policy)
    assert any("removes more assertions" in w for w in result.warnings)


def test_warns_about_focused_frontend_tests(base, policy):
    base.write(FRONTEND_TEST, GOOD_TS_TEST.replace("it(", "it.only("))
    assert any("only" in w for w in validate(base.staged_patch(), policy).warnings)


def test_parses_hunk_lines_that_look_like_headers(base, policy):
    base.write("backend/tests/api/test_existing.py", "-- not a header\n+++ nor this\n")
    base.commit("odd content")
    base.write("backend/tests/api/test_existing.py", "def test_a():\n    assert 1\n")
    result = validate(base.staged_patch(), policy)
    assert result.valid, result.errors
    assert result.files[0]["removed"] == 2


def test_keeps_form_feed_inside_a_line(base, policy):
    base.write(BACKEND_TEST, "def test_a():\n    assert '\x0c' == '\x0c'\n")
    assert validate(base.staged_patch(), policy).valid


@pytest.mark.parametrize(
    "patch",
    [
        "garbage before header\n",
        "diff --git x y\n",
        'diff --git "a/x\\ty" "b/x\\ty"\n',
        f"diff --git a/{BACKEND_TEST} b/{BACKEND_TEST}\nsomething unexpected\n",
        f"diff --git a/{BACKEND_TEST} b/{BACKEND_TEST}\n--- a/{BACKEND_TEST}\n+++ b/other.py\n",
        f"diff --git a/{BACKEND_TEST} b/{BACKEND_TEST}\n--- a/{BACKEND_TEST}\n+++ b/{BACKEND_TEST}\n"
        "@@ -1,2 +1,2 @@\n-a\n",
        f"diff --git a/{BACKEND_TEST} b/{BACKEND_TEST}\n--- a/{BACKEND_TEST}\n+++ b/{BACKEND_TEST}\n"
        "@@ -1 +1 @@\n-a\n+b\n*extra\n",
    ],
)
def test_rejects_malformed_patches(policy, patch):
    with pytest.raises(PatchFormatError):
        parse_patch(patch)
    assert not validate(patch, policy).valid


def test_rejects_paths_escaping_the_repository(policy):
    patch = "diff --git a/../x/test_a.py b/../x/test_a.py\nnew file mode 100644\n"
    assert "unsafe path" in validate(patch, policy).errors[0]


def test_main_writes_result_and_sets_exit_code(base, policy, tmp_path):
    base.write("backend/app/api/health.py", "changed = True\n")
    patch = tmp_path / "changes.patch"
    patch.write_text(base.staged_patch())
    out = tmp_path / "validation.json"
    assert validate_patch.main(["--patch", str(patch), "--out", str(out)]) == 1
    assert json.loads(out.read_text())["valid"] is False

    patch.write_text("")
    assert validate_patch.main(["--patch", str(patch), "--out", str(out)]) == 0
