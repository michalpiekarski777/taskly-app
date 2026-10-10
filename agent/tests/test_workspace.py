import pytest

from test_agent.workspace import conventional_test_path, python_module, resolve_inside, write_file


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("backend/app/api/health.py", "backend/tests/api/test_health.py"),
        ("backend/app/features/todos/router.py", "backend/tests/features/todos/test_router.py"),
        ("backend/app/factory.py", "backend/tests/test_factory.py"),
        ("frontend/src/lib/http.ts", "frontend/src/lib/http.test.ts"),
        ("frontend/src/features/todos/TodosPage.tsx", "frontend/src/features/todos/TodosPage.test.tsx"),
        ("frontend/src/styles.css", None),
        ("README.md", None),
    ],
)
def test_conventional_test_path(source, expected):
    assert conventional_test_path(source) == expected


def test_python_module():
    assert python_module("backend/app/features/todos/router.py") == "app.features.todos.router"


@pytest.mark.parametrize("relative", ["../outside.py", "/etc/passwd", ".git/hooks/pre-commit", "a/../../x"])
def test_refuses_paths_outside_the_repository(tmp_path, relative):
    with pytest.raises(ValueError):
        resolve_inside(tmp_path, relative)


def test_refuses_writing_through_a_symlink_out_of_the_repository(tmp_path):
    repo, outside = tmp_path / "repo", tmp_path / "outside"
    repo.mkdir()
    outside.mkdir()
    (repo / "link").symlink_to(outside)
    with pytest.raises(ValueError):
        write_file(repo, "link/test_x.py", "x")


def test_write_file_creates_directories(tmp_path):
    write_file(tmp_path, "a/b/test_x.py", "content")
    assert (tmp_path / "a/b/test_x.py").read_text() == "content"
