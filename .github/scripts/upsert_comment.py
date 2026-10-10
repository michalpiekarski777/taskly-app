"""Create or update the test agent's comment on a pull request.

The comment is found by the report marker and its author, so every run edits
the same comment instead of adding a new one, and comments written by people
are never touched.

Usage:
    upsert_comment.py --pr <number> --body-file report.md

Requires GITHUB_TOKEN and GITHUB_REPOSITORY; honours GITHUB_API_URL.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from collections.abc import Callable
from pathlib import Path

from render_report import MARKER

BOT_LOGIN = "github-actions[bot]"
MAX_BODY = 65_000  # GitHub rejects comments longer than 65,536 characters.
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")

# (method, url, payload) -> (decoded JSON body, Link header)
Transport = Callable[[str, str, dict | None], tuple[object, str]]


def http_transport(token: str) -> Transport:
    def send(method: str, url: str, payload: dict | None) -> tuple[object, str]:
        request = urllib.request.Request(
            url,
            method=method,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read() or b"null"), response.headers.get("Link", "")

    return send


def _next_page(link_header: str) -> str | None:
    match = re.search(r'<([^>]+)>;\s*rel="next"', link_header)
    return match.group(1) if match else None


def find_comment(send: Transport, api: str, repository: str, pr: int) -> int | None:
    url: str | None = f"{api}/repos/{repository}/issues/{pr}/comments?per_page=100"
    while url:
        comments, link = send("GET", url, None)
        for comment in comments:  # type: ignore[union-attr]
            if comment["user"]["login"] == BOT_LOGIN and MARKER in (comment.get("body") or ""):
                return comment["id"]
        url = _next_page(link)
        # The token goes with every request, so never follow links off the API host.
        if url and not url.startswith(f"{api}/"):
            raise ValueError(f"Refusing to follow pagination link outside {api}: {url}")
    return None


def prepare_body(body: str) -> str:
    if MARKER not in body:
        body = f"{MARKER}\n{body}"
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY] + "\n\n…report truncated; see the workflow run summary.\n"
    return body


def upsert(send: Transport, api: str, repository: str, pr: int, body: str) -> tuple[str, int]:
    """Returns ("created" | "updated", comment id)."""
    body = prepare_body(body)
    comment_id = find_comment(send, api, repository, pr)
    if comment_id is None:
        created, _ = send("POST", f"{api}/repos/{repository}/issues/{pr}/comments", {"body": body})
        return "created", created["id"]  # type: ignore[index]
    send("PATCH", f"{api}/repos/{repository}/issues/comments/{comment_id}", {"body": body})
    return "updated", comment_id


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--body-file", type=Path, required=True)
    args = parser.parse_args(argv)

    repository = os.environ.get("GITHUB_REPOSITORY", "")
    token = os.environ.get("GITHUB_TOKEN", "")
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com").rstrip("/")
    if not _REPOSITORY.match(repository) or not token or args.pr <= 0:
        parser.error("GITHUB_REPOSITORY, GITHUB_TOKEN and a positive --pr are required")

    action, comment_id = upsert(
        http_transport(token), api, repository, args.pr, args.body_file.read_text(encoding="utf-8")
    )
    print(f"{action} comment {comment_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
