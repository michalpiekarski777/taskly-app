"""List the files a pull request changed, grouped by component.

Only testable source files (per .github/test-agent.toml) count as changes the
agent should look at; changed tests are reported so it can update them.

Usage:
    detect_changes.py --base <sha> --head <sha> [--repo .] [--out changes.json]
                      [--github-output]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from policy import DEFAULT_POLICY_PATH, Policy, is_safe_relative_path, load_policy

# Commit SHAs or plain ref names; never anything git could read as an option.
_REVISION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")


def git_name_status(repo: Path, base: str, head: str) -> list[tuple[str, str]]:
    """(status letter, path) for each file changed since the merge base."""
    for revision in (base, head):
        if not _REVISION.match(revision):
            raise ValueError(f"Invalid revision: {revision!r}")
    output = subprocess.run(
        ["git", "-C", str(repo), "diff", "--name-status", "-z", "--find-renames", f"{base}...{head}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return parse_name_status(output)


def parse_name_status(output: str) -> list[tuple[str, str]]:
    fields = output.split("\0")
    changes: list[tuple[str, str]] = []
    i = 0
    while i < len(fields) and fields[i]:
        status = fields[i][0]
        if status in "RC":
            # A rename is a deletion of the old path and an addition of the new one.
            old, new = fields[i + 1], fields[i + 2]
            if status == "R":
                changes.append(("D", old))
            changes.append(("A", new))
            i += 3
        else:
            changes.append((status, fields[i + 1]))
            i += 2
    return changes


def classify(changes: list[tuple[str, str]], policy: Policy) -> dict:
    components = {
        c.name: {"changed_sources": [], "deleted_sources": [], "changed_tests": []}
        for c in policy.components
    }
    ignored: list[str] = []
    for status, path in changes:
        if not is_safe_relative_path(path):
            ignored.append(path)
        elif component := policy.test_component(path):
            if status != "D":
                components[component.name]["changed_tests"].append(path)
        elif component := policy.source_component(path):
            key = "deleted_sources" if status == "D" else "changed_sources"
            components[component.name][key].append(path)
        else:
            ignored.append(path)
    return {
        "has_testable_changes": any(c["changed_sources"] for c in components.values()),
        "components": components,
        "ignored": ignored,
    }


def write_github_output(result: dict) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if not output_path:
        raise RuntimeError("GITHUB_OUTPUT is not set")
    changed = [name for name, c in result["components"].items() if c["changed_sources"]]
    with open(output_path, "a", encoding="utf-8") as output:
        output.write(f"has_testable_changes={str(result['has_testable_changes']).lower()}\n")
        output.write(f"components={','.join(changed)}\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--repo", type=Path, default=Path("."))
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY_PATH)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--github-output", action="store_true")
    args = parser.parse_args(argv)

    changes = git_name_status(args.repo, args.base, args.head)
    result = {"base": args.base, "head": args.head, **classify(changes, load_policy(args.policy))}
    text = json.dumps(result, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    if args.github_output:
        write_github_output(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
