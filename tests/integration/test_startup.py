"""Phase 1 acceptance test.

Part 5 section 183 states the acceptance criteria:

    Start successfully.
    Load configuration.
    Create required directories.
    Initialize logging.
    Report environment information.
    Exit cleanly.

Each is checked below against a real subprocess, in a temporary project root, so
the evidence is the behaviour of the shipped entry point rather than a function
call arranged to succeed. Part 4 section 137 forbids reporting success without
verifying it; the same standard applies to the tests that certify a phase.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.version import EDITION
from tests.conftest import PROJECT_ROOT


def run(project_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run `python -m app` against a temporary project root."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env["PYTHONIOENCODING"] = "utf-8"
    # Remove any RUDRA_* override inherited from the developer's shell, so the
    # test measures the application rather than the environment it ran in.
    for key in [k for k in env if k.startswith("RUDRA_")]:
        del env[key]
    return subprocess.run(
        [sys.executable, "-m", "app", *args, "--project-root", str(project_root)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(project_root),
        timeout=120,
    )


@pytest.fixture
def started(project_root: Path):
    result = run(project_root)
    assert result.returncode == 0, result.stderr
    return result


def test_starts_successfully_and_exits_cleanly(started, project_root: Path):
    """Criteria 1 and 6."""
    assert started.returncode == 0
    assert "Startup complete" in started.stdout


def test_loads_configuration(project_root: Path):
    """Criterion 2, including that a user-supplied value actually takes effect."""
    config_dir = project_root / "config"
    config_dir.mkdir()
    (config_dir / "rudra.toml").write_text(
        '[app]\ninstance_name = "acceptance"\n', encoding="utf-8"
    )

    result = run(project_root, "config", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)

    assert payload["settings"]["app.instance_name"]["value"] == "acceptance"
    assert payload["settings"]["app.instance_name"]["origin"].startswith("file:")
    assert payload["settings"]["logging.console_level"]["origin"] == "default"


def test_creates_required_directories(started, project_root: Path):
    """Criterion 3, checked on disk rather than in the report."""
    expected = [
        project_root / "config",
        project_root / "logs",
        project_root / "data",
        project_root / "data" / "documents",
        project_root / "data" / "extracted",
        project_root / "data" / "knowledge",
        project_root / "data" / "database",
        project_root / "data" / "indexes",
        project_root / "data" / "cache",
        project_root / "data" / "procedures",
        project_root / "data" / "sources",
        project_root / "data" / "backups",
    ]
    missing = [str(path) for path in expected if not path.is_dir()]
    assert not missing, f"not created: {missing}"


def test_initialises_logging_and_writes_a_record(started, project_root: Path):
    """Criterion 4: the log file must exist and contain the startup entry."""
    log_file = project_root / "logs" / "rudra.log"
    assert log_file.is_file()
    text = log_file.read_text(encoding="utf-8")
    assert "RUDRA starting" in text
    assert "RUDRA exiting with code 0" in text
    # The audit channel is created but stays empty: nothing audits anything yet.
    assert (project_root / "logs" / "audit.log").is_file()


def test_reports_environment_information(started):
    """Criterion 5."""
    for expected in ("Python runtime", "SQLite", "Disk (data volume)", "Data root"):
        assert expected in started.stdout, expected


def test_environment_report_is_machine_readable(project_root: Path):
    result = run(project_root, "env", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    components = {row["component"] for row in payload["components"]}
    assert "SQLite FTS5" in components
    for row in payload["components"]:
        assert row["status"] in {"OK", "WARNING", "CRITICAL", "UNKNOWN", "NOT_AVAILABLE"}


def test_startup_is_repeatable(project_root: Path):
    """Running twice must not fail and must not recreate existing directories."""
    first = run(project_root)
    second = run(project_root, "--json")
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr

    payload = json.loads(second.stdout)
    assert payload["directories_created"] == []
    assert payload["directories_existing"]


def test_existing_files_survive_a_restart(project_root: Path):
    """Startup must never delete or overwrite anything the user put there."""
    run(project_root)
    marker = project_root / "data" / "documents" / "keep-me.txt"
    marker.write_text("original document", encoding="utf-8")

    result = run(project_root)

    assert result.returncode == 0, result.stderr
    assert marker.read_text(encoding="utf-8") == "original document"


def test_paths_command_states_storage_classes(project_root: Path):
    result = run(project_root, "paths", "--json")
    assert result.returncode == 0, result.stderr
    by_key = {row["key"]: row for row in json.loads(result.stdout)}
    assert by_key["documents"]["storage_class"] == "SOURCE_DATA"
    assert by_key["indexes"]["storage_class"] == "REBUILDABLE"
    assert all(row["exists"] for row in by_key.values())


def test_version_command(project_root: Path):
    result = run(project_root, "version", "--json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["version"]
    assert payload["edition"] == EDITION


def test_bad_configuration_fails_with_an_actionable_message(project_root: Path):
    """A failure must explain itself and must not look like a success."""
    config_dir = project_root / "config"
    config_dir.mkdir()
    (config_dir / "rudra.toml").write_text(
        '[logging]\nconsole_level = "CHATTY"\n', encoding="utf-8"
    )

    result = run(project_root)

    assert result.returncode != 0
    assert result.returncode == 2, f"expected the INVALID_INPUT exit code, got {result.returncode}"
    assert "INVALID_INPUT" in result.stderr
    assert "console_level" in result.stderr
    assert "DEBUG" in result.stderr  # the valid choices are offered
    assert "Startup complete" not in result.stdout


def test_missing_explicit_config_file_is_refused(project_root: Path):
    result = run(project_root, "--config", str(project_root / "absent.toml"))
    assert result.returncode == 2
    assert "does not exist" in result.stderr


def test_environment_variable_overrides_the_file(project_root: Path):
    config_dir = project_root / "config"
    config_dir.mkdir()
    (config_dir / "rudra.toml").write_text(
        '[app]\ninstance_name = "from-file"\n', encoding="utf-8"
    )

    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env["RUDRA_APP_INSTANCE_NAME"] = "from-environment"
    result = subprocess.run(
        [
            sys.executable, "-m", "app", "config", "--json",
            "--project-root", str(project_root),
        ],
        capture_output=True, text=True, env=env, cwd=str(project_root), timeout=120,
    )

    assert result.returncode == 0, result.stderr
    setting = json.loads(result.stdout)["settings"]["app.instance_name"]
    assert setting["value"] == "from-environment"
    assert setting["origin"] == "env:RUDRA_APP_INSTANCE_NAME"


def test_startup_states_what_exists_and_what_does_not(started):
    """Part 5 section 242: the build must not imply features it does not have.

    Changed in Phase 5: the sentence used to deny a knowledge subsystem, which
    became false once extraction existed. Changed after Phase 23: it denied
    reasoning and action, which Phases 10 and 14-15 had made false; and then it
    denied a GUI, which ADR 0057's desktop window made false. The intent is
    unchanged - what exists is stated rather than hidden, what does not is stated
    too, and the full account is pointed to rather than implied.
    """
    assert "RUDRA is ready: add documents, ask questions, calculate, check sources," in started.stdout
    assert "DEVELOPMENT_STATE" not in started.stdout and "Phase" not in started.stdout
    assert "Some features are only partly complete" in started.stdout and "docs/LIMITATIONS.md" in started.stdout
    assert "no OCR" not in started.stdout  # OCR exists; the old line said otherwise
    assert "docs/LIMITATIONS.md" in started.stdout
    assert "no reasoning or action subsystem" not in started.stdout
    assert "GUI" not in started.stdout.split("Startup complete.")[1]
