import pytest

import upsert_comment
from upsert_comment import BOT_LOGIN, MARKER, MAX_BODY, upsert

API = "https://api.github.com"
COMMENTS = f"{API}/repos/o/r/issues/5/comments?per_page=100"


class FakeGitHub:
    def __init__(self, pages: dict[str, tuple[list, str]]):
        self.pages = pages
        self.calls: list[tuple[str, str, dict | None]] = []

    def __call__(self, method, url, payload):
        self.calls.append((method, url, payload))
        if method == "GET":
            return self.pages[url]
        return {"id": 99}, ""


def comment(id_, login, body):
    return {"id": id_, "user": {"login": login}, "body": body}


def test_creates_a_comment_when_none_exists():
    github = FakeGitHub({COMMENTS: ([comment(1, "alice", "hello")], "")})
    assert upsert(github, API, "o/r", 5, "report") == ("created", 99)
    method, url, payload = github.calls[-1]
    assert (method, url) == ("POST", f"{API}/repos/o/r/issues/5/comments")
    assert payload["body"].startswith(MARKER)


def test_updates_the_bots_comment_and_ignores_copies_by_people():
    page2 = f"{API}/repositories/1/issues/5/comments?page=2"
    github = FakeGitHub(
        {
            COMMENTS: ([comment(1, "mallory", f"{MARKER} fake")], f'<{page2}>; rel="next"'),
            page2: ([comment(2, BOT_LOGIN, f"{MARKER}\nold")], ""),
        }
    )
    assert upsert(github, API, "o/r", 5, f"{MARKER}\nnew") == ("updated", 2)
    assert github.calls[-1] == ("PATCH", f"{API}/repos/o/r/issues/comments/2", {"body": f"{MARKER}\nnew"})


def test_refuses_to_follow_pagination_off_the_api_host():
    github = FakeGitHub({COMMENTS: ([], '<https://evil.example/steal>; rel="next"')})
    with pytest.raises(ValueError):
        upsert(github, API, "o/r", 5, "report")


def test_truncates_long_bodies():
    body = upsert_comment.prepare_body("x" * (MAX_BODY + 10))
    assert len(body) < 65_536
    assert body.endswith("see the workflow run summary.\n")


def test_main_validates_environment(monkeypatch, tmp_path):
    body = tmp_path / "report.md"
    body.write_text("report")
    monkeypatch.setenv("GITHUB_REPOSITORY", "o/r; rm -rf")
    monkeypatch.setenv("GITHUB_TOKEN", "t")
    with pytest.raises(SystemExit):
        upsert_comment.main(["--pr", "5", "--body-file", str(body)])
