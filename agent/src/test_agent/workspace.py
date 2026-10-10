"""Where tests for a source file live, and safe writes into the repository."""

from __future__ import annotations

from pathlib import Path, PurePosixPath


def conventional_test_path(source: str) -> str | None:
    """The repo-relative test file for a source file, following repo conventions.

    backend/app/<pkg>/<name>.py  -> backend/tests/<pkg>/test_<name>.py
    frontend/src/<dir>/<name>.ts -> frontend/src/<dir>/<name>.test.ts (same for .tsx)
    """
    path = PurePosixPath(source)
    if path.parts[:2] == ("backend", "app") and path.suffix == ".py":
        return str(PurePosixPath("backend/tests", *path.parts[2:-1], f"test_{path.name}"))
    if path.parts[:2] == ("frontend", "src") and path.suffix in (".ts", ".tsx"):
        return str(path.with_name(f"{path.stem}.test{path.suffix}"))
    return None


def python_module(source: str) -> str:
    """backend/app/api/health.py -> app.api.health"""
    return ".".join(PurePosixPath(source).with_suffix("").parts[1:])


def resolve_inside(repo: Path, relative: str) -> Path:
    """Absolute path of `relative` within `repo`; refuses anything outside it."""
    root = repo.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root) or ".git" in PurePosixPath(relative).parts:
        raise ValueError(f"Refusing to write outside the repository: {relative}")
    return target


def write_file(repo: Path, relative: str, content: str) -> None:
    target = resolve_inside(repo, relative)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
