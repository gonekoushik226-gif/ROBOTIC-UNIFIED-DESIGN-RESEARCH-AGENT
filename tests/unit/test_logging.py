"""Logging tests.

The point of interest is the AUDIT channel: it must exist, it must survive any
level threshold, and it must stay out of the ordinary log and the console
(Part 4 section 133).
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from app.config.schema import defaults
from app.core.logging_setup import (
    AUDIT,
    ROOT_LOGGER_NAME,
    get_logger,
    setup_logging,
    shutdown_logging,
)
from app.core.paths import PathLayout

SPEC_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "AUDIT")


def _prepare(root: Path, **logging_overrides):
    config = defaults()
    if logging_overrides:
        config = replace(config, logging=replace(config.logging, **logging_overrides))
    layout = PathLayout.from_config(root, config)
    layout.ensure()
    return layout, config


def test_every_specified_level_is_available():
    """Part 4 section 133 lists six levels; all must resolve to a number."""
    mapping = logging.getLevelNamesMapping()
    for name in SPEC_LEVELS:
        assert name in mapping, name


def test_audit_outranks_critical_so_no_threshold_can_hide_it():
    assert AUDIT > logging.CRITICAL


def test_audit_records_go_only_to_the_audit_file(project_root: Path):
    layout, config = _prepare(project_root)
    report = setup_logging(layout, config)

    logger = get_logger("test.audit")
    logger.audit("user requested OPEN_APPLICATION Chrome")
    logger.info("ordinary progress message")
    shutdown_logging()

    audit_text = report.audit_file.read_text(encoding="utf-8")
    main_text = report.log_file.read_text(encoding="utf-8")

    assert "OPEN_APPLICATION Chrome" in audit_text
    assert "AUDIT" in audit_text
    # Ordinary logs must not carry audit content.
    assert "OPEN_APPLICATION Chrome" not in main_text
    assert "ordinary progress message" in main_text
    assert "ordinary progress message" not in audit_text


def test_file_level_is_honoured(project_root: Path):
    layout, config = _prepare(project_root, file_level="WARNING")
    report = setup_logging(layout, config)

    logger = get_logger("test.levels")
    logger.debug("debug detail")
    logger.warning("a warning")
    shutdown_logging()

    text = report.log_file.read_text(encoding="utf-8")
    assert "a warning" in text
    assert "debug detail" not in text


def test_setup_is_idempotent_and_does_not_duplicate_records(project_root: Path):
    layout, config = _prepare(project_root)
    setup_logging(layout, config)
    report = setup_logging(layout, config)

    logger = get_logger("test.repeat")
    logger.info("written once")
    shutdown_logging()

    text = report.log_file.read_text(encoding="utf-8")
    assert text.count("written once") == 1


def test_rudra_logging_does_not_leak_into_the_python_root_logger(project_root: Path):
    """RUDRA must not capture or reformat another library's logging."""
    layout, config = _prepare(project_root)
    setup_logging(layout, config)
    try:
        assert logging.getLogger(ROOT_LOGGER_NAME).propagate is False
        assert not any(
            getattr(handler, "_rudra_owned_handler", False)
            for handler in logging.getLogger().handlers
        )
    finally:
        shutdown_logging()


def test_shutdown_releases_the_files(project_root: Path):
    layout, config = _prepare(project_root)
    setup_logging(layout, config)
    get_logger("test.close").info("hello")
    shutdown_logging()
    assert not logging.getLogger(ROOT_LOGGER_NAME).handlers


def test_timestamps_carry_a_timezone(project_root: Path):
    """An audit trail with ambiguous local times is not much of an audit trail."""
    layout, config = _prepare(project_root)
    report = setup_logging(layout, config)
    get_logger("test.time").info("stamped")
    shutdown_logging()

    first_line = report.log_file.read_text(encoding="utf-8").splitlines()[0]
    stamp = first_line.split()[0]
    assert "T" in stamp
    assert ("+" in stamp) or stamp.endswith("Z") or ("-" in stamp.split("T")[1])


def test_rotation_settings_are_applied(project_root: Path):
    layout, config = _prepare(project_root, max_bytes=4096, backup_count=1)
    setup_logging(layout, config)
    root = logging.getLogger(ROOT_LOGGER_NAME)
    rotating = [h for h in root.handlers if hasattr(h, "maxBytes")]
    shutdown_logging()
    assert rotating, "expected rotating file handlers"
    assert all(h.maxBytes == 4096 for h in rotating)  # type: ignore[attr-defined]
    assert all(h.backupCount == 1 for h in rotating)  # type: ignore[attr-defined]


def test_get_logger_namespaces_under_the_rudra_tree():
    assert get_logger("core.thing").name == f"{ROOT_LOGGER_NAME}.core.thing"
    assert get_logger(f"{ROOT_LOGGER_NAME}.already").name == f"{ROOT_LOGGER_NAME}.already"
