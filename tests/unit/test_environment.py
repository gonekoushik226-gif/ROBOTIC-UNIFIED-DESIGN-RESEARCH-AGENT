"""Environment report tests.

The report is the first place RUDRA states facts about the world, so these tests
are mostly about honesty: measured values, and UNKNOWN when a value cannot be read.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.config.schema import defaults
from app.core import environment
from app.core.environment import Status
from app.core.paths import PathLayout

REQUIRED_ROWS = {
    "Operating system",
    "Python runtime",
    "CPU",
    "RAM",
    "Disk (data volume)",
    "SQLite",
    "SQLite FTS5",
    "tkinter (GUI toolkit)",
    "Project root",
    "Configuration file",
    "Data root",
    "Logs directory",
}


def _report(root: Path, *, config_file: Path | None = None, present: bool = False):
    config = defaults()
    layout = PathLayout.from_config(root, config)
    layout.ensure()
    return environment.collect(
        layout=layout,
        config=config,
        config_file=config_file,
        config_file_present=present,
    )


def test_report_covers_every_expected_component(project_root: Path):
    names = {c.component for c in _report(project_root).components}
    assert REQUIRED_ROWS <= names, REQUIRED_ROWS - names


def test_every_row_has_all_six_specified_columns(project_root: Path):
    """Part 5 section 180 fixes the columns of the environment report."""
    for row in _report(project_root).components:
        assert row.component.strip()
        assert row.detected.strip()
        assert row.required.strip()
        assert row.available in (True, False, None)
        assert row.impact.strip()
        assert row.recommendation.strip()


def test_fts5_row_matches_a_direct_probe(project_root: Path):
    """The report must reflect a real measurement, not an assumption."""
    try:
        with sqlite3.connect(":memory:") as connection:
            connection.execute("CREATE VIRTUAL TABLE probe USING fts5(body)")
        actually_available = True
    except sqlite3.Error:
        actually_available = False

    row = next(
        c for c in _report(project_root).components if c.component == "SQLite FTS5"
    )
    assert row.available is actually_available


def test_python_row_reports_the_running_interpreter(project_root: Path):
    import platform
    import sys

    row = next(
        c for c in _report(project_root).components if c.component == "Python runtime"
    )
    assert platform.python_version() in row.detected
    assert sys.executable in row.detected


def test_unmeasurable_values_are_unknown_not_invented(project_root: Path, monkeypatch):
    """If RAM cannot be read, the report says UNKNOWN rather than guessing."""
    monkeypatch.setattr(environment, "_memory_status", lambda: (None, None))
    row = next(c for c in _report(project_root).components if c.component == "RAM")
    assert row.status is Status.UNKNOWN
    assert "UNKNOWN" in row.detected
    assert row.available is None


def test_low_disk_space_is_escalated(project_root: Path, monkeypatch):
    """Part 4 section 158: storage limits must be surfaced before they bite."""
    monkeypatch.setattr(environment, "free_disk_mb", lambda path: 10)
    row = next(
        c for c in _report(project_root).components if c.component == "Disk (data volume)"
    )
    assert row.status is Status.CRITICAL
    assert row.recommendation != "-"


def test_missing_config_file_is_stated_plainly(project_root: Path):
    row = next(
        c for c in _report(project_root).components if c.component == "Configuration file"
    )
    assert row.available is False
    assert "defaults" in row.detected


def test_problems_lists_only_rows_needing_attention(project_root: Path, monkeypatch):
    monkeypatch.setattr(environment, "free_disk_mb", lambda path: 10)
    report = _report(project_root)
    problems = report.problems()
    assert problems
    assert all(
        row.status in (Status.WARNING, Status.CRITICAL, Status.NOT_AVAILABLE)
        for row in problems
    )


def test_report_serialises_to_json(project_root: Path):
    payload = json.loads(json.dumps(_report(project_root).to_dict()))
    assert payload["components"]
    assert "status" in payload["components"][0]


def test_text_table_contains_every_component(project_root: Path):
    report = _report(project_root)
    text = report.to_text()
    for row in report.components:
        assert row.component in text
