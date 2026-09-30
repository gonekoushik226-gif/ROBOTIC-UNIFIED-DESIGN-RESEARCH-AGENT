"""The update notice: version comparison, caching, failure handling and the privacy of the request.

No test reaches the network: every check is given a fake fetch function.
"""

from __future__ import annotations

import json
import socket
import urllib.error
from datetime import UTC, datetime, timedelta

import pytest

from app import version
from app.updates import CHECK_INTERVAL, UpdateChecker, UpdateStatus, build_request, evaluate, parse_version
from app.updates import checker as checker_module

REPO_RELEASE = f"https://github.com/{version.GITHUB_REPOSITORY}/releases/tag/v"


def release(tag: str, *, draft: bool = False, prerelease: bool = False, **extra) -> dict:
    return {"tag_name": tag, "draft": draft, "prerelease": prerelease,
            "html_url": REPO_RELEASE + tag.lstrip("v"), **extra}


class Clock:
    def __init__(self):
        self.now = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


class FakeGitHub:
    """Records every request and answers with a payload, or raises."""

    def __init__(self, answer):
        self.answer = answer
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request, timeout))
        if isinstance(self.answer, BaseException):
            raise self.answer
        if isinstance(self.answer, bytes):
            return self.answer
        return json.dumps(self.answer).encode("utf-8")


def make(tmp_path, answer, current="0.1.0"):
    github, clock = FakeGitHub(answer), Clock()
    return UpdateChecker(tmp_path / "config" / "updates.json", current=current, fetch=github, now=clock), github, clock


# ---------------------------------------------------------------- versions


def test_the_current_version_is_a_stable_release_number():
    assert parse_version(version.VERSION) is not None


@pytest.mark.parametrize(("text", "parsed"), [
    ("1.2.3", (1, 2, 3)), ("v1.2.3", (1, 2, 3)), ("v0.2", (0, 2, 0)), (" 2.0.0 ", (2, 0, 0)),
    ("1.2.3-beta.1", None), ("v1.2.3rc1", None), ("latest", None), ("", None), (None, None), (3, None),
])
def test_version_numbers(text, parsed):
    assert parse_version(text) == parsed


def test_a_newer_stable_release_is_offered_with_its_own_page():
    info = evaluate(release("v0.2.0"), "0.1.0")
    assert info.status is UpdateStatus.UPDATE_AVAILABLE and info.available
    assert info.latest == "0.2.0" and info.url == REPO_RELEASE + "0.2.0"
    assert "0.2.0 is available" in info.message()


def test_the_same_version_is_up_to_date():
    info = evaluate(release("v0.1.0"), "0.1.0")
    assert info.status is UpdateStatus.UP_TO_DATE and not info.available


def test_an_older_release_is_not_offered():
    assert evaluate(release("0.0.9"), "0.1.0").status is UpdateStatus.UP_TO_DATE


@pytest.mark.parametrize("flags", [{"prerelease": True}, {"draft": True}])
def test_drafts_and_prereleases_are_never_offered(flags):
    assert evaluate(release("v9.0.0", **flags), "0.1.0").status is UpdateStatus.UP_TO_DATE


def test_a_prerelease_style_tag_is_never_offered():
    assert evaluate(release("v9.0.0-rc.1"), "0.1.0").status is UpdateStatus.UNAVAILABLE


@pytest.mark.parametrize("payload", [None, [], "text", {}, {"tag_name": "v1.0.0"},
                                     {"tag_name": 5, "draft": False, "prerelease": False},
                                     {"tag_name": "v1.0.0", "draft": "no", "prerelease": False}])
def test_a_malformed_response_is_unavailable(payload):
    assert evaluate(payload, "0.1.0").status is UpdateStatus.UNAVAILABLE


def test_a_link_to_anywhere_else_is_replaced_by_the_official_releases_page():
    info = evaluate(release("v2.0.0", html_url="https://example.com/download.exe"), "0.1.0")
    assert info.available and info.url == version.RELEASES_PAGE


# ---------------------------------------------------------------- checking


def test_a_check_finds_a_newer_release(tmp_path):
    checker, github, _ = make(tmp_path, release("v0.3.1"))
    info = checker.check()
    assert info.available and info.latest == "0.3.1" and len(github.requests) == 1


@pytest.mark.parametrize("failure", [
    urllib.error.URLError(OSError("getaddrinfo failed")),  # offline
    TimeoutError("timed out"),
    socket.timeout("timed out"),
    ConnectionResetError("reset"),
    urllib.error.HTTPError(version.LATEST_RELEASE_API, 404, "Not Found", {}, None),
    urllib.error.HTTPError(version.LATEST_RELEASE_API, 403, "rate limited", {}, None),
])
def test_an_unreachable_github_fails_quietly(tmp_path, failure):
    checker, _, _ = make(tmp_path, failure)
    info = checker.check(manual=True)
    assert info.status is UpdateStatus.UNAVAILABLE and not info.available
    assert "Everything else works as usual" in info.message()


@pytest.mark.parametrize("body", [b"not json", b"\xff\xfe", b"[]", pytest.param(b"x" * 1_000_001, id="oversized")])
def test_an_unreadable_response_fails_quietly(tmp_path, body):
    checker, _, _ = make(tmp_path, body)
    assert checker.check(manual=True).status is UpdateStatus.UNAVAILABLE


def test_automatic_checks_run_at_most_once_per_interval_and_reuse_the_cached_result(tmp_path):
    checker, github, clock = make(tmp_path, release("v0.2.0"))
    assert checker.due()
    first = checker.check()
    assert first.available and not checker.due()
    clock.now += timedelta(hours=5)
    again = checker.check()  # not due: the cached result, no request
    assert again.available and again.latest == "0.2.0" and len(github.requests) == 1
    clock.now += CHECK_INTERVAL
    assert checker.due()
    checker.check()
    assert len(github.requests) == 2


def test_a_failed_attempt_also_waits_for_the_interval(tmp_path):
    checker, github, _ = make(tmp_path, urllib.error.URLError("offline"))
    checker.check()
    assert not checker.due()
    checker.check()
    assert len(github.requests) == 1


def test_a_manual_check_always_asks(tmp_path):
    checker, github, _ = make(tmp_path, release("v0.1.0"))
    checker.check()
    info = checker.check(manual=True)
    assert info.status is UpdateStatus.UP_TO_DATE and len(github.requests) == 2


def test_the_cache_is_ignored_after_an_upgrade(tmp_path):
    checker, _, clock = make(tmp_path, release("v0.2.0"))
    checker.check()
    upgraded = UpdateChecker(checker.state_path, current="0.2.0", fetch=FakeGitHub(release("v0.2.0")), now=clock)
    assert upgraded.cached() is None


def test_automatic_checking_can_be_turned_off(tmp_path):
    checker, github, _ = make(tmp_path, release("v0.2.0"))
    assert checker.enabled
    checker.set_enabled(False)
    assert not checker.enabled and not checker.due()
    assert checker.check() is None and not github.requests
    assert checker.check(manual=True).available  # the manual check still works


def test_a_damaged_state_file_is_treated_as_empty(tmp_path):
    checker, _, _ = make(tmp_path, release("v0.2.0"))
    checker.state_path.parent.mkdir(parents=True)
    checker.state_path.write_text("{not json", encoding="utf-8")
    assert checker.enabled and checker.due() and checker.cached() is None
    assert checker.check().available


# ---------------------------------------------------------------- privacy


def test_the_request_carries_nothing_about_the_user(tmp_path, monkeypatch):
    checker, github, _ = make(tmp_path, release("v0.2.0"))
    checker.check(manual=True)
    ((request, timeout),) = github.requests
    assert request.full_url == version.LATEST_RELEASE_API
    assert request.full_url == f"https://api.github.com/repos/{version.GITHUB_REPOSITORY}/releases/latest"
    assert request.get_method() == "GET" and request.data is None
    headers = {name.lower(): value for name, value in request.header_items()}
    assert headers == {"accept": "application/vnd.github+json", "user-agent": "RUDRA-update-check"}
    assert 0 < timeout <= 10
    # Beyond the fixed public address, the only thing sent is the two headers above.
    sent = json.dumps(headers)
    import getpass
    import os
    for private in {getpass.getuser(), os.environ.get("COMPUTERNAME", ""), str(tmp_path), version.VERSION}:
        if private:
            assert private not in sent, private


def test_the_request_is_the_same_for_everyone():
    first, second = build_request(), build_request()
    assert first.full_url == second.full_url and first.header_items() == second.header_items()


def test_the_real_fetch_uses_no_cookies_or_credentials(monkeypatch):
    opened = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self, limit):
            return b"{}"

    class Opener:
        def open(self, request, timeout):
            opened.update(request=request, timeout=timeout)
            return Response()

    def build_opener(*handlers):
        opened["handlers"] = handlers
        return Opener()

    monkeypatch.setattr(checker_module.urllib.request, "build_opener", build_opener)
    assert checker_module._fetch(build_request(), 3.0) == b"{}"
    assert opened["handlers"] == () and opened["timeout"] == 3.0
