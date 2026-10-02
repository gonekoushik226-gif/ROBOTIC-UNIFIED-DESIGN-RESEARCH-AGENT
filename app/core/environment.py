"""Environment report.

Part 5 section 180 asks for an environment report with these columns:

    Component | Detected value | Required? | Already available? | Potential impact
              | Recommendation

Every value here is measured at the moment the report runs. Nothing is assumed and
nothing is cached from a previous session. When a value cannot be determined the
report says UNKNOWN and gives the reason; it never substitutes a plausible number
(Part 1 sections 5 and 6).

This is the environment report, not the health check. The health check required by
Part 6 section 36 - database, knowledge index, document store, procedure store,
backup system, providers - is NOT IMPLEMENTED, because none of those subsystems
exists yet.
"""

from __future__ import annotations

import os
import platform
import sqlite3
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from app.config.schema import RudraConfig
from app.core.paths import PathLayout, free_disk_mb

UNKNOWN = "UNKNOWN"


class Status(StrEnum):
    """How the detected value compares with what RUDRA needs."""

    OK = "OK"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    UNKNOWN = "UNKNOWN"
    NOT_AVAILABLE = "NOT_AVAILABLE"


@dataclass(frozen=True, slots=True)
class Component:
    """One row of the environment report."""

    component: str
    detected: str
    required: str
    available: bool | None
    impact: str
    recommendation: str
    status: Status = Status.OK

    def to_dict(self) -> dict[str, object]:
        return {
            "component": self.component,
            "detected": self.detected,
            "required": self.required,
            "available": self.available,
            "impact": self.impact,
            "recommendation": self.recommendation,
            "status": str(self.status),
        }


@dataclass(frozen=True, slots=True)
class EnvironmentReport:
    components: tuple[Component, ...]

    def to_dict(self) -> dict[str, object]:
        return {"components": [c.to_dict() for c in self.components]}

    def problems(self) -> tuple[Component, ...]:
        """Rows that need the user's attention."""
        return tuple(
            c
            for c in self.components
            if c.status in (Status.WARNING, Status.CRITICAL, Status.NOT_AVAILABLE)
        )

    def to_text(self) -> str:
        """Fixed-width table, readable in a terminal."""
        headers = ("Component", "Detected", "Status", "Recommendation")
        rows = [
            (c.component, c.detected, str(c.status), c.recommendation)
            for c in self.components
        ]
        widths = [
            max(len(headers[i]), *(len(r[i]) for r in rows)) for i in range(len(headers))
        ]
        line = "  ".join("-" * w for w in widths)
        out = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)), line]
        out.extend(
            "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)) for row in rows
        )
        return "\n".join(out)


def collect(
    *,
    layout: PathLayout,
    config: RudraConfig,
    config_file: Path | None,
    config_file_present: bool,
) -> EnvironmentReport:
    """Measure the environment and return the report."""
    return EnvironmentReport(
        components=(
            _operating_system(),
            _python_runtime(),
            _cpu(),
            _memory(config),
            _disk(layout, config),
            _sqlite(),
            _sqlite_fts5(),
            _tkinter(),
            _project_root(layout),
            _config_file(config_file, config_file_present),
            _data_root(layout),
            _logs(layout),
        )
    )


def operating_system_facts() -> tuple[str, str, str]:
    """(system, release, version) exactly as the `platform` module reports them.

    On a Windows 10 or 11 workstation they are read from `sys.getwindowsversion()` - the
    version fields the `platform` module's WMI query returns, and its own release table
    (build 22000 and later is "11") - because the WMI queries cost about 0.1 s at every
    startup. Anything else asks `platform` itself.
    """
    if sys.platform == "win32":
        winver = sys.getwindowsversion()
        major, minor, build = winver.major, winver.minor, winver.build
        if getattr(winver, "product_type", 1) == 1 and (major, minor) == (10, 0):
            return "Windows", ("11" if build >= 22000 else "10"), f"{major}.{minor}.{build}"
    return platform.system(), platform.release(), platform.version()


def _operating_system() -> Component:
    system, release, version = operating_system_facts()
    detected = f"{system} {release} ({version})"
    supported = system == "Windows"
    return Component(
        component="Operating system",
        detected=detected,
        required="Windows 10 or 11",
        available=True,
        impact=(
            "Supported."
            if supported
            else "RUDRA is made for Windows; other systems are untested."
        ),
        recommendation="-" if supported else "Expect Windows-only features to be unavailable.",
        status=Status.OK if supported else Status.WARNING,
    )


def _python_runtime() -> Component:
    version = platform.python_version()
    ok = sys.version_info >= (3, 12)
    return Component(
        component="Python runtime",
        detected=f"{version} ({platform.architecture()[0]}) at {sys.executable}",
        required=">= 3.12",
        available=ok,
        impact="Runs RUDRA." if ok else "RUDRA needs Python 3.12 or newer.",
        recommendation="-" if ok else "Run RUDRA with Python 3.12 or newer.",
        status=Status.OK if ok else Status.CRITICAL,
    )


def _cpu() -> Component:
    logical = os.cpu_count()
    detected = f"{logical} logical processors" if logical else UNKNOWN
    return Component(
        component="CPU",
        detected=detected,
        required="Any; RUDRA works on the processor alone",
        available=logical is not None,
        impact="How many things RUDRA can work on at once.",
        recommendation="-",
        status=Status.OK if logical else Status.UNKNOWN,
    )


def _memory(config: RudraConfig) -> Component:
    total_mb, available_mb = _memory_status()
    if total_mb is None or available_mb is None:
        return Component(
            component="RAM",
            detected=f"{UNKNOWN} (no supported way to measure on this platform)",
            required="Enough headroom for document processing",
            available=None,
            impact="Memory pressure cannot be reported on this platform.",
            recommendation="Watch memory use manually during large operations.",
            status=Status.UNKNOWN,
        )
    threshold = config.resources.warn_available_ram_mb
    low = available_mb < threshold
    return Component(
        component="RAM",
        detected=f"{available_mb} MB available of {total_mb} MB total",
        required=f"at least {threshold} MB available (configured advisory)",
        available=True,
        impact=(
            "Little headroom; large operations may page and slow the machine."
            if low
            else "Enough free memory."
        ),
        recommendation=(
            "Close memory-heavy applications before adding large documents."
            if low
            else "-"
        ),
        status=Status.WARNING if low else Status.OK,
    )


def _memory_status() -> tuple[int | None, int | None]:
    """Total and available RAM in MB, or (None, None) if it cannot be measured.

    Uses the Windows API directly, so no third-party package is needed.
    """
    if sys.platform != "win32":
        return (None, None)
    import ctypes
    from ctypes import wintypes

    class _MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    try:
        status = _MemoryStatusEx()
        status.dwLength = ctypes.sizeof(_MemoryStatusEx)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            return (None, None)
    except (OSError, AttributeError, ValueError):
        # Measurement failed. Report unknown rather than invent a number.
        return (None, None)
    megabyte = 1024 * 1024
    return (status.ullTotalPhys // megabyte, status.ullAvailPhys // megabyte)


def _disk(layout: PathLayout, config: RudraConfig) -> Component:
    free_mb = free_disk_mb(layout.data_root)
    if free_mb is None:
        return Component(
            component="Disk (data volume)",
            detected=f"{UNKNOWN} (free space could not be read)",
            required=f"at least {config.resources.min_free_disk_mb} MB free",
            available=None,
            impact="Free space cannot be checked before large operations.",
            recommendation="Check free space yourself before adding documents.",
            status=Status.UNKNOWN,
        )
    if free_mb < config.resources.min_free_disk_mb:
        status, impact, action = (
            Status.CRITICAL,
            "Too little space for safe operation.",
            "Free some disk space before adding anything.",
        )
    elif free_mb < config.resources.warn_free_disk_mb:
        status, impact, action = (
            Status.WARNING,
            "Space is limited; very large documents may not fit.",
            "Keep an eye on free space, or keep RUDRA's data on a larger drive.",
        )
    else:
        status, impact, action = (Status.OK, "Enough free space.", "-")
    return Component(
        component="Disk (data volume)",
        detected=f"{free_mb} MB free at {layout.data_root}",
        required=f"at least {config.resources.min_free_disk_mb} MB free",
        available=True,
        impact=impact,
        recommendation=action,
        status=status,
    )


def _sqlite() -> Component:
    return Component(
        component="SQLite",
        detected=f"library {sqlite3.sqlite_version} (Python standard library sqlite3)",
        required="Built into RUDRA",
        available=True,
        impact="Keeps your knowledge on this computer.",
        recommendation="-",
    )


def _sqlite_fts5() -> Component:
    available, detail = _probe_fts5()
    return Component(
        component="SQLite FTS5",
        detected="available" if available else f"not available ({detail})",
        required="Needed for searching your documents by word",
        available=available,
        impact=(
            "Searching by word works."
            if available
            else "Searching by word will not work; asking about concepts still does."
        ),
        recommendation="-" if available else "Use the packaged RUDRA, which includes it.",
        status=Status.OK if available else Status.NOT_AVAILABLE,
    )


def _probe_fts5() -> tuple[bool, str]:
    """Actually create an FTS5 table rather than assuming support."""
    try:
        with sqlite3.connect(":memory:") as connection:
            connection.execute("CREATE VIRTUAL TABLE probe USING fts5(body)")
        return (True, "")
    except sqlite3.Error as exc:
        return (False, f"{type(exc).__name__}: {exc}")


def _tkinter() -> Component:
    available, detail = _probe_tkinter()
    return Component(
        component="tkinter (GUI toolkit)",
        detected=detail,
        required="Needed for RUDRA's window",
        available=available,
        impact=(
            "RUDRA's window can open."
            if available
            else "RUDRA's window cannot open; the command line still works."
        ),
        recommendation="-" if available else "Use the packaged RUDRA, which includes it.",
        status=Status.OK if available else Status.NOT_AVAILABLE,
    )


def _probe_tkinter() -> tuple[bool, str]:
    """Import tkinter and read its Tk version. Does not create a window."""
    try:
        import tkinter
    except ImportError as exc:
        return (False, f"not importable ({exc})")
    try:
        return (True, f"Tk {tkinter.TkVersion} / Tcl {tkinter.TclVersion}")
    except (AttributeError, OSError) as exc:  # pragma: no cover - platform dependent
        return (False, f"imported but unusable ({exc})")


def _project_root(layout: PathLayout) -> Component:
    return Component(
        component="Project root",
        detected=str(layout.project_root),
        required="Must exist and be writable",
        available=True,
        impact="Everything RUDRA keeps - documents, knowledge, settings - lives in this folder.",
        recommendation="-",
    )


def _config_file(config_file: Path | None, present: bool) -> Component:
    if present and config_file is not None:
        detected = str(config_file)
        impact = "Settings come from this file, then RUDRA_* environment variables."
        action = "-"
    else:
        detected = "not present; built-in defaults in use"
        impact = "RUDRA runs on its built-in defaults."
        action = "Create config/rudra.toml only if you need to change a default."
    return Component(
        component="Configuration file",
        detected=detected,
        required="Optional",
        available=present,
        impact=impact,
        recommendation=action,
    )


def _data_root(layout: PathLayout) -> Component:
    return Component(
        component="Data root",
        detected=str(layout.data_root),
        required="Created at startup",
        available=layout.data_root.is_dir(),
        impact="Holds your documents, knowledge, search index, caches and backups, each in its own folder.",
        recommendation="-",
        status=Status.OK if layout.data_root.is_dir() else Status.CRITICAL,
    )


def _logs(layout: PathLayout) -> Component:
    return Component(
        component="Logs directory",
        detected=str(layout.logs_dir),
        required="Created at startup",
        available=layout.logs_dir.is_dir(),
        impact="Holds the application log and the audit channel file.",
        recommendation="-",
        status=Status.OK if layout.logs_dir.is_dir() else Status.CRITICAL,
    )
