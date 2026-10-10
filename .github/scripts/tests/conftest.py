import subprocess
from pathlib import Path

import pytest

from policy import load_policy


class GitRepo:
    """A throwaway git repository for producing real `git diff` output."""

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.git("config", "commit.gpgsign", "false")

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.root), *args], check=True, capture_output=True, text=True
        ).stdout

    def write(self, path: str, content: str | bytes) -> Path:
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            file.write_bytes(content)
        else:
            file.write_text(content, encoding="utf-8")
        return file

    def commit(self, message: str = "commit") -> str:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)
        return self.git("rev-parse", "HEAD").strip()

    def staged_patch(self) -> str:
        """Patch of the working tree against HEAD, as the workflow builds it."""
        self.git("add", "-A")
        return self.git("diff", "--cached", "--binary", "--no-color", "--no-ext-diff")


@pytest.fixture
def repo(tmp_path):
    return GitRepo(tmp_path / "repo")


@pytest.fixture(scope="session")
def policy():
    return load_policy()
