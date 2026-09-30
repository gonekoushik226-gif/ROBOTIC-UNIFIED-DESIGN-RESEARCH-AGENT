"""Checking GitHub for a newer stable release of RUDRA.

What is sent: one anonymous HTTPS GET of the repository's public "latest release"
endpoint (`app.version.LATEST_RELEASE_API`), with a fixed ``User-Agent`` (GitHub
requires one) and an ``Accept`` header. Nothing else: no question, answer, document,
file name, path, user name, machine identifier, version, token or key. GitHub sees the
request as it sees any visit to a public page.

What is decided: the release's ``tag_name`` (``v1.2.3`` or ``1.2.3``) is compared with
`app.version.VERSION`. Drafts, pre-releases and tags that are not plain version numbers
are never offered. The only link shown is the repository's own release page.

When: at most once per `CHECK_INTERVAL` automatically (the time of the last attempt is
kept in a small JSON file in the user's configuration folder), or whenever the user
asks. Offline, timed out or refused, the check reports UNAVAILABLE and nothing else
happens: RUDRA never waits on GitHub and never needs it.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from app.version import GITHUB_REPOSITORY, LATEST_RELEASE_API, RELEASES_PAGE, VERSION

CHECK_INTERVAL = timedelta(hours=24)
TIMEOUT_SECONDS = 6.0
#: A release record is a few kilobytes; anything far larger is not one.
MAX_RESPONSE_BYTES = 1_000_000
USER_AGENT = "RUDRA-update-check"
STATE_FILE = "updates.json"

_VERSION = re.compile(r"^v?(\d+)\.(\d+)(?:\.(\d+))?$")

Fetcher = Callable[[urllib.request.Request, float], bytes]


class UpdateStatus(StrEnum):
    UPDATE_AVAILABLE = "UPDATE_AVAILABLE"
    UP_TO_DATE = "UP_TO_DATE"
    #: GitHub could not be reached or its answer could not be used. Not an error.
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True)
class UpdateInfo:
    status: UpdateStatus
    current: str
    latest: str | None = None
    url: str = RELEASES_PAGE
    checked_at: str | None = None
    reason: str = ""

    @property
    def available(self) -> bool:
        return self.status is UpdateStatus.UPDATE_AVAILABLE

    def message(self) -> str:
        if self.status is UpdateStatus.UPDATE_AVAILABLE:
            return f"RUDRA {self.latest} is available. You have {self.current}."
        if self.status is UpdateStatus.UP_TO_DATE:
            return f"RUDRA {self.current} is the latest version."
        return "RUDRA could not check for updates right now. Everything else works as usual."


def parse_version(text: object) -> tuple[int, int, int] | None:
    """A stable release number (``1.2.3``, ``v1.2``), or None for anything else."""
    match = _VERSION.match(text.strip()) if isinstance(text, str) else None
    if match is None:
        return None
    major, minor, patch = match.groups()
    return int(major), int(minor), int(patch or 0)


def _release_url(payload: dict) -> str:
    """The release's own page when it is this repository's, else the releases page."""
    url = payload.get("html_url")
    prefix = f"https://github.com/{GITHUB_REPOSITORY}/releases/"
    return url if isinstance(url, str) and url.startswith(prefix) else RELEASES_PAGE


def evaluate(payload: object, current: str = VERSION, checked_at: str | None = None) -> UpdateInfo:
    """Compare a GitHub release record with the running version."""
    if not isinstance(payload, dict) or not isinstance(payload.get("draft"), bool) \
            or not isinstance(payload.get("prerelease"), bool):
        return UpdateInfo(UpdateStatus.UNAVAILABLE, current, checked_at=checked_at,
                          reason="the response was not a release record")
    if payload["draft"] or payload["prerelease"]:
        return UpdateInfo(UpdateStatus.UP_TO_DATE, current, checked_at=checked_at,
                          reason="the newest release is not a published stable release")
    tag = payload.get("tag_name")
    latest = parse_version(tag)
    mine = parse_version(current)
    if latest is None or mine is None:
        return UpdateInfo(UpdateStatus.UNAVAILABLE, current, checked_at=checked_at,
                          reason=f"unrecognised version {tag!r}")
    shown = ".".join(str(part) for part in latest)
    if latest > mine:
        return UpdateInfo(UpdateStatus.UPDATE_AVAILABLE, current, shown, _release_url(payload), checked_at)
    return UpdateInfo(UpdateStatus.UP_TO_DATE, current, shown, _release_url(payload), checked_at)


def build_request() -> urllib.request.Request:
    """The one request the check makes. It carries nothing about the user or the machine."""
    return urllib.request.Request(
        LATEST_RELEASE_API,
        headers={"Accept": "application/vnd.github+json", "User-Agent": USER_AGENT},
        method="GET",
    )


def _fetch(request: urllib.request.Request, timeout: float) -> bytes:
    # A dedicated opener: no cookie jar, no credentials, only the system's proxy settings.
    opener = urllib.request.build_opener()
    with opener.open(request, timeout=timeout) as response:
        return response.read(MAX_RESPONSE_BYTES + 1)


def _now() -> datetime:
    return datetime.now(UTC)


def _stamp(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_stamp(text: object) -> datetime | None:
    if not isinstance(text, str):
        return None
    try:
        return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


class UpdateChecker:
    """The check, its setting and its cache, kept in ``<config folder>/updates.json``."""

    def __init__(self, state_path: Path, *, current: str = VERSION, fetch: Fetcher = _fetch,
                 now: Callable[[], datetime] = _now, interval: timedelta = CHECK_INTERVAL):
        self.state_path = Path(state_path)
        self.current = current
        self._fetch = fetch
        self._now = now
        self.interval = interval

    # state ---------------------------------------------------------------

    def _load(self) -> dict:
        try:
            data = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save(self, data: dict) -> None:
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            self.state_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        except OSError:
            return  # the cache is a convenience; a read-only folder only means more checks

    @property
    def enabled(self) -> bool:
        """Whether RUDRA checks by itself (on by default; the manual check always works)."""
        return self._load().get("automatic", True) is not False

    def set_enabled(self, enabled: bool) -> None:
        data = self._load()
        data["automatic"] = bool(enabled)
        self._save(data)

    def cached(self) -> UpdateInfo | None:
        """The last result, when it was for this version."""
        data = self._load().get("last_result")
        if not isinstance(data, dict) or data.get("current") != self.current:
            return None
        try:
            return UpdateInfo(UpdateStatus(data["status"]), self.current, data.get("latest"),
                              data.get("url") or RELEASES_PAGE, data.get("checked_at"), data.get("reason", ""))
        except (KeyError, ValueError):
            return None

    def due(self) -> bool:
        """True when an automatic check should run now."""
        if not self.enabled:
            return False
        last = _parse_stamp(self._load().get("last_attempt"))
        now = self._now()
        return last is None or last > now or now - last >= self.interval

    # checking ------------------------------------------------------------

    def check(self, *, manual: bool = False) -> UpdateInfo | None:
        """Check GitHub. Automatic checks return the cached result (or None) when not due."""
        if not manual and not self.due():
            return self.cached()
        now = self._now()
        stamp = _stamp(now)
        try:
            raw = self._fetch(build_request(), TIMEOUT_SECONDS)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ValueError("the response is too large to be a release record")
            info = evaluate(json.loads(raw.decode("utf-8")), self.current, stamp)
        except urllib.error.HTTPError as exc:
            reason = "no release has been published yet" if exc.code == 404 else f"GitHub answered {exc.code}"
            info = UpdateInfo(UpdateStatus.UNAVAILABLE, self.current, checked_at=stamp, reason=reason)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            info = UpdateInfo(UpdateStatus.UNAVAILABLE, self.current, checked_at=stamp,
                              reason=f"GitHub could not be reached ({type(exc).__name__})")
        except (ValueError, UnicodeDecodeError) as exc:
            info = UpdateInfo(UpdateStatus.UNAVAILABLE, self.current, checked_at=stamp,
                              reason=f"the response could not be read ({type(exc).__name__})")
        data = self._load()
        data["last_attempt"] = stamp
        record = asdict(info)
        record["status"] = info.status.value
        data["last_result"] = record
        self._save(data)
        return info
