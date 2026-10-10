"""Validate the agent's patch before it is applied or committed.

The patch is rejected unless every file it touches is an allowed test file
(see `tests` in .github/test-agent.toml), it only adds or modifies regular
text files, stays within the size limits and adds no obviously dangerous code.
Weaker signals, such as skipped tests or tests without assertions, are
reported as warnings.

Usage:
    validate_patch.py --patch changes.patch [--out validation.json]

Exits with 1 when the patch is invalid.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

from policy import DEFAULT_POLICY_PATH, Policy, is_safe_relative_path, load_policy

REGULAR_FILE_MODE = "100644"

# Added code matching these makes the patch invalid: unit tests have no reason
# to spawn processes, open sockets, evaluate strings or touch CI secrets.
FORBIDDEN_PATTERNS = {
    "imports a process, network or filesystem module": re.compile(
        r"^\s*(?:import|from)\s+(?:subprocess|socket|ctypes|shutil|requests|urllib|http\.client)\b"
        r"|from\s+['\"](?:node:)?(?:child_process|fs|fs/promises|net|http|https|os|worker_threads)['\"]"
        r"|require\s*\("
    ),
    "runs shell commands": re.compile(r"\bos\.(?:system|popen|exec\w*|spawn\w*)\s*\("),
    "evaluates code from strings": re.compile(
        r"(?<![\w.])(?:eval|exec)\s*\(|__import__\s*\(|new\s+Function\s*\("
    ),
    "references CI secrets": re.compile(r"GITHUB_TOKEN|LLM_API_KEY|ACTIONS_\w*TOKEN|ACTIONS_RUNTIME"),
}

WARNING_PATTERNS = {
    "skips or disables tests": re.compile(
        r"pytest\.mark\.(?:skip|skipif|xfail)|pytest\.(?:skip|xfail)\s*\("
        r"|\b(?:it|test|describe)\.(?:skip|todo)\b|\bx(?:it|test|describe)\s*\("
    ),
    "focuses tests (only)": re.compile(r"\b(?:it|test|describe)\.only\b|\bf(?:it|describe)\s*\("),
    "reads environment variables": re.compile(r"os\.environ|os\.getenv|process\.env"),
    "contains a trivially true assertion": re.compile(
        r"assert\s+True\b|expect\(\s*true\s*\)\.toBe\(\s*true\s*\)"
    ),
}

TEST_DEFINITION = re.compile(r"^\s*(?:async\s+)?def\s+test_\w*|\b(?:it|test)\s*\(")
ASSERTION = re.compile(r"\bassert\b|pytest\.raises|\.assert_\w+\(|\bexpect\s*\(")

_DIFF_HEADER = "diff --git "
_HUNK_HEADER = re.compile(r"@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")


class PatchFormatError(ValueError):
    pass


@dataclass
class FileChange:
    path: str
    status: str = "modified"
    old_path: str | None = None
    new_mode: str | None = None
    mode_changed: bool = False
    binary: bool = False
    added_lines: list[str] = field(default_factory=list)
    removed_lines: list[str] = field(default_factory=list)


def _header_paths(line: str) -> tuple[str, str]:
    """(old, new) paths from a `diff --git a/<old> b/<new>` header."""
    rest = line[len(_DIFF_HEADER) :]
    if '"' in rest:
        raise PatchFormatError(f"Quoted (special character) paths are not allowed: {line}")
    length = (len(rest) - 5) // 2
    path = rest[2 : 2 + length]
    if rest == f"a/{path} b/{path}":
        return path, path
    if rest.startswith("a/") and rest.count(" b/") == 1:
        old, new = rest[2:].split(" b/")
        return old, new
    raise PatchFormatError(f"Cannot parse diff header: {line}")


def parse_patch(text: str) -> list[FileChange]:
    """Parse `git diff` output; anything unexpected raises PatchFormatError."""
    # Split on "\n" only: str.splitlines() also breaks on characters such as
    # \x0c or   that may legitimately appear inside a hunk line.
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    files: list[FileChange] = []
    current: FileChange | None = None
    i = 0
    while i < len(lines):
        line = lines[i]
        i += 1
        if line.startswith(_DIFF_HEADER):
            old_path, new_path = _header_paths(line)
            current = FileChange(path=new_path)
            if old_path != new_path:
                current.status, current.old_path = "renamed", old_path
            files.append(current)
            continue
        if current is None:
            raise PatchFormatError(f"Content before the first diff header: {line!r}")
        if line.startswith("new file mode "):
            current.status = "added"
            current.new_mode = line.removeprefix("new file mode ")
        elif line.startswith("deleted file mode "):
            current.status = "deleted"
        elif line.startswith(("old mode ", "new mode ")):
            current.mode_changed = True
        elif line.startswith(("rename from ", "copy from ")):
            current.status = "renamed" if line.startswith("rename") else "copied"
            current.old_path = line.split(" ", 2)[2]
        elif line.startswith(("rename to ", "copy to ", "similarity index ", "dissimilarity index ")):
            pass
        elif line.startswith("index "):
            parts = line.split()
            if len(parts) == 3:
                current.new_mode = parts[2]
        elif line.startswith(("Binary files ", "GIT binary patch")):
            current.binary = True
            # Skip the encoded payload; binary files are rejected anyway.
            while i < len(lines) and not lines[i].startswith(_DIFF_HEADER):
                i += 1
        elif line.startswith("--- "):
            old_path = current.old_path or current.path
            expected = ("/dev/null",) if current.status == "added" else (f"a/{old_path}",)
            if line[4:] not in expected:
                raise PatchFormatError(f"Unexpected old file line for {current.path}: {line}")
        elif line.startswith("+++ "):
            expected = ("/dev/null",) if current.status == "deleted" else (f"b/{current.path}",)
            if line[4:] not in expected:
                raise PatchFormatError(f"Unexpected new file line for {current.path}: {line}")
        elif match := _HUNK_HEADER.match(line):
            old_count = int(match.group(1) or 1)
            new_count = int(match.group(2) or 1)
            while old_count > 0 or new_count > 0:
                if i >= len(lines):
                    raise PatchFormatError(f"Truncated hunk in {current.path}")
                body = lines[i]
                i += 1
                if body.startswith("+"):
                    current.added_lines.append(body[1:])
                    new_count -= 1
                elif body.startswith("-"):
                    current.removed_lines.append(body[1:])
                    old_count -= 1
                elif body.startswith(" ") or body == "":
                    old_count -= 1
                    new_count -= 1
                elif not body.startswith("\\"):
                    raise PatchFormatError(f"Unexpected hunk line in {current.path}: {body!r}")
            if old_count < 0 or new_count < 0:
                raise PatchFormatError(f"Hunk line counts do not match in {current.path}")
            while i < len(lines) and lines[i].startswith("\\"):
                i += 1
        else:
            raise PatchFormatError(f"Unexpected line in {current.path}: {line!r}")
    return files


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str]
    warnings: list[str]
    files: list[dict]


def validate(text: str, policy: Policy) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    if len(text.encode("utf-8")) > policy.limits.max_patch_bytes:
        errors.append(f"Patch is larger than {policy.limits.max_patch_bytes} bytes")
        return ValidationResult(False, errors, warnings, [])
    try:
        files = parse_patch(text)
    except PatchFormatError as error:
        return ValidationResult(False, [str(error)], warnings, [])

    if len(files) > policy.limits.max_files:
        errors.append(f"Patch touches {len(files)} files; the limit is {policy.limits.max_files}")
    added_total = sum(len(f.added_lines) for f in files)
    if added_total > policy.limits.max_added_lines:
        errors.append(f"Patch adds {added_total} lines; the limit is {policy.limits.max_added_lines}")

    for change in files:
        errors.extend(_file_errors(change, policy))
        warnings.extend(_file_warnings(change))

    summary = [
        {
            "path": f.path,
            "status": f.status,
            "added": len(f.added_lines),
            "removed": len(f.removed_lines),
        }
        for f in files
    ]
    return ValidationResult(not errors, errors, warnings, summary)


def _file_errors(change: FileChange, policy: Policy) -> list[str]:
    path = change.path
    if not all(is_safe_relative_path(p) for p in (path, change.old_path or path)):
        return [f"{path}: unsafe path"]
    errors = []
    if policy.test_component(path) is None:
        errors.append(f"{path}: not an allowed test file")
    if change.status in ("deleted", "renamed", "copied"):
        errors.append(f"{path}: {change.status} files are not allowed")
    if change.mode_changed:
        errors.append(f"{path}: file mode changes are not allowed")
    if change.status == "added" and change.new_mode != REGULAR_FILE_MODE:
        errors.append(f"{path}: new files must be regular files (mode {REGULAR_FILE_MODE})")
    if change.binary:
        errors.append(f"{path}: binary changes are not allowed")
    for number, line in enumerate(change.added_lines, start=1):
        for reason, pattern in FORBIDDEN_PATTERNS.items():
            if pattern.search(line):
                errors.append(f"{path}: added line {number} {reason}: {line.strip()[:120]}")
    return errors


def _file_warnings(change: FileChange) -> list[str]:
    path = change.path
    warnings = []
    added = "\n".join(change.added_lines)
    for reason, pattern in WARNING_PATTERNS.items():
        if pattern.search(added):
            warnings.append(f"{path}: {reason}")
    added_tests = sum(1 for line in change.added_lines if TEST_DEFINITION.search(line))
    added_assertions = sum(1 for line in change.added_lines if ASSERTION.search(line))
    if added_tests and not added_assertions:
        warnings.append(f"{path}: adds {added_tests} test(s) without assertions")
    removed_assertions = sum(1 for line in change.removed_lines if ASSERTION.search(line))
    if removed_assertions > added_assertions:
        warnings.append(f"{path}: removes more assertions than it adds ({removed_assertions} removed)")
    return warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--patch", type=Path, required=True)
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)

    result = validate(args.patch.read_text(encoding="utf-8"), load_policy(args.policy))
    text = json.dumps(asdict(result), indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if result.valid else 1


if __name__ == "__main__":
    sys.exit(main())
