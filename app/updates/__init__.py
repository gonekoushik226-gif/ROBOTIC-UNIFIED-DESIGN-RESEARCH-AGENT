"""Optional notice of a newer RUDRA release.

RUDRA works entirely offline; this package only tells the user when a newer stable
release has been published, and where to download it. It never downloads or installs
anything. See `app.updates.checker`.
"""

from app.updates.checker import (
    CHECK_INTERVAL,
    UpdateChecker,
    UpdateInfo,
    UpdateStatus,
    build_request,
    evaluate,
    parse_version,
)

__all__ = [
    "CHECK_INTERVAL",
    "UpdateChecker",
    "UpdateInfo",
    "UpdateStatus",
    "build_request",
    "evaluate",
    "parse_version",
]
