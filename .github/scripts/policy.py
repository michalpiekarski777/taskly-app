"""Shared test agent policy: path globs, path safety and limits."""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from functools import cache
from pathlib import Path

DEFAULT_POLICY_PATH = Path(__file__).resolve().parents[1] / "test-agent.toml"


@cache
def glob_to_regex(pattern: str) -> re.Pattern[str]:
    """Translate a path glob to a regex.

    Supports `**` (any number of directories), `*` and `?` (within one path
    segment) and one level of `{a,b}` alternation.
    """
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:[^/]+/)*")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "{":
            end = pattern.index("}", i)
            options = pattern[i + 1 : end].split(",")
            out.append("(?:" + "|".join(re.escape(o) for o in options) + ")")
            i = end + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out) + r"\Z")


def matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(glob_to_regex(p).match(path) for p in patterns)


_UNSAFE_CHARS = re.compile(r"[\x00-\x1f\x7f\\]")


def is_safe_relative_path(path: str) -> bool:
    """True for a plain repo-relative POSIX path that stays inside the repo."""
    if not path or path.startswith("/") or _UNSAFE_CHARS.search(path):
        return False
    parts = path.split("/")
    return all(part not in ("", ".", "..", ".git") for part in parts)


@dataclass(frozen=True)
class Component:
    name: str
    source: tuple[str, ...]
    exclude: tuple[str, ...]
    tests: tuple[str, ...]

    def is_test(self, path: str) -> bool:
        return matches_any(path, self.tests)

    def is_testable_source(self, path: str) -> bool:
        return matches_any(path, self.source) and not matches_any(path, self.exclude)


@dataclass(frozen=True)
class Limits:
    max_files: int
    max_added_lines: int
    max_patch_bytes: int


@dataclass(frozen=True)
class Policy:
    components: tuple[Component, ...]
    limits: Limits

    def test_component(self, path: str) -> Component | None:
        """The component whose test allowlist covers `path`, if any."""
        return next((c for c in self.components if c.is_test(path)), None)

    def source_component(self, path: str) -> Component | None:
        """The component for which `path` is testable source code, if any."""
        return next((c for c in self.components if c.is_testable_source(path)), None)


def load_policy(path: Path = DEFAULT_POLICY_PATH) -> Policy:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    if data.get("schema_version") != 1:
        raise ValueError(f"Unsupported policy schema_version in {path}")
    components = tuple(
        Component(
            name=name,
            source=tuple(spec["source"]),
            exclude=tuple(spec.get("exclude", ())),
            tests=tuple(spec["tests"]),
        )
        for name, spec in data["components"].items()
    )
    return Policy(components=components, limits=Limits(**data["limits"]))
