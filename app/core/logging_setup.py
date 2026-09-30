"""Logging foundation.

Implements the log levels required by Part 4 section 133: DEBUG, INFO, WARNING,
ERROR, CRITICAL and AUDIT.

About AUDIT
-----------
AUDIT is not a severity, it is a separate channel. It is given a numeric value
above CRITICAL so that no level threshold can filter it out by accident, and it is
routed to its own file. AUDIT records are deliberately excluded from the console
and from the main log, because audit entries carry request content and Part 4
section 133 forbids putting sensitive information into ordinary logs.

What this module is NOT
-----------------------
This is the logging foundation only. The audit *service* required by Part 4
sections 131-132 - structured, append-only, queryable audit events in their own
database - is NOT IMPLEMENTED. `audit.log` is a text channel that later phases will
complement, not the audit store itself. No component writes audit records yet.

Log files rotate, so logs cannot become an uncontrolled storage dump.
"""

from __future__ import annotations

import logging
import logging.handlers
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.config.schema import RudraConfig
from app.core.errors import StorageError
from app.core.paths import PathLayout

#: Numeric value of the AUDIT channel. Above CRITICAL (50) so no threshold hides it.
AUDIT: int = 55

#: Name of the RUDRA logger tree. Handlers attach here, not to the Python root
#: logger, so RUDRA never captures or reformats another library's logging.
ROOT_LOGGER_NAME = "rudra"

#: Marks handlers this module owns, so setup can be repeated safely.
_OWNED = "_rudra_owned_handler"

logging.addLevelName(AUDIT, "AUDIT")


class RudraLogger(logging.Logger):
    """Logger with an `audit()` method for the AUDIT channel."""

    def audit(self, msg: str, *args: object, **kwargs: object) -> None:
        """Record an auditable event. Goes to the audit file only."""
        if self.isEnabledFor(AUDIT):
            self._log(AUDIT, msg, args, **kwargs)  # type: ignore[arg-type]


logging.setLoggerClass(RudraLogger)


class _IsoFormatter(logging.Formatter):
    """Timestamps as ISO-8601 with a timezone offset, so logs are unambiguous."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        moment = datetime.fromtimestamp(record.created, tz=timezone.utc).astimezone()
        return moment.isoformat(timespec="milliseconds")


class _AuditOnly(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno == AUDIT


class _ExcludeAudit(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno != AUDIT


@dataclass(frozen=True, slots=True)
class LoggingReport:
    """Where logging actually went, so reports can state facts rather than intent."""

    console_level: str
    file_level: str
    log_file: Path
    audit_file: Path
    max_bytes: int
    backup_count: int


def get_logger(name: str) -> RudraLogger:
    """Return a RUDRA logger. The only supported way to obtain one."""
    full_name = name if name.startswith(ROOT_LOGGER_NAME) else f"{ROOT_LOGGER_NAME}.{name}"
    logger = logging.getLogger(full_name)
    if not isinstance(logger, RudraLogger):  # pragma: no cover - defensive
        raise TypeError(
            f"Logger {full_name!r} was created before app.core.logging_setup was "
            "imported, so it lacks the AUDIT channel."
        )
    return logger


def setup_logging(layout: PathLayout, config: RudraConfig) -> LoggingReport:
    """Configure console, file and audit handlers. Safe to call more than once.

    The logs directory must already exist; `PathLayout.ensure()` creates it.
    """
    log_file = layout.logs_dir / config.logging.file_name
    audit_file = layout.logs_dir / config.logging.audit_file_name

    root = logging.getLogger(ROOT_LOGGER_NAME)
    _remove_owned_handlers(root)

    # The logger passes everything through; the handlers decide what is kept.
    root.setLevel(logging.DEBUG)
    root.propagate = False

    console_level = _level_number(config.logging.console_level)
    file_level = _level_number(config.logging.file_level)

    console = logging.StreamHandler()
    console.setLevel(console_level)
    console.addFilter(_ExcludeAudit())
    console.setFormatter(_IsoFormatter("%(asctime)s  %(levelname)-8s %(message)s"))
    _adopt(root, console)

    file_handler = _rotating(
        log_file, config.logging.max_bytes, config.logging.backup_count
    )
    file_handler.setLevel(file_level)
    file_handler.addFilter(_ExcludeAudit())
    file_handler.setFormatter(
        _IsoFormatter("%(asctime)s  %(levelname)-8s %(name)s  %(message)s")
    )
    _adopt(root, file_handler)

    audit_handler = _rotating(
        audit_file, config.logging.max_bytes, config.logging.backup_count
    )
    audit_handler.setLevel(AUDIT)
    audit_handler.addFilter(_AuditOnly())
    audit_handler.setFormatter(_IsoFormatter("%(asctime)s  AUDIT  %(message)s"))
    _adopt(root, audit_handler)

    return LoggingReport(
        console_level=config.logging.console_level,
        file_level=config.logging.file_level,
        log_file=log_file,
        audit_file=audit_file,
        max_bytes=config.logging.max_bytes,
        backup_count=config.logging.backup_count,
    )


def shutdown_logging() -> None:
    """Flush and close the handlers this module installed."""
    root = logging.getLogger(ROOT_LOGGER_NAME)
    _remove_owned_handlers(root)


def _rotating(
    path: Path, max_bytes: int, backup_count: int
) -> logging.handlers.RotatingFileHandler:
    try:
        return logging.handlers.RotatingFileHandler(
            path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=False,
        )
    except OSError as exc:
        raise StorageError.of(
            "RUDRA could not open a log file.",
            f"{type(exc).__name__}: {exc}",
            stage="startup.logging",
            missing=(str(path),),
            data_changed=False,
            retry_safe=True,
            cause=repr(exc),
            next_options=(
                f"Check that {path.parent} exists and is writable.",
                "Set paths.logs_dir in config/rudra.toml to a writable location.",
            ),
        ) from exc


def _adopt(logger: logging.Logger, handler: logging.Handler) -> None:
    setattr(handler, _OWNED, True)
    logger.addHandler(handler)


def _remove_owned_handlers(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        if getattr(handler, _OWNED, False):
            logger.removeHandler(handler)
            handler.close()


def _level_number(name: str) -> int:
    """Translate a configured level name to its number.

    The configuration layer has already checked the name against the allowed
    choices, so an unknown name here is a programming error, not user input.
    """
    if name == "AUDIT":
        return AUDIT
    number = logging.getLevelNamesMapping().get(name)
    if number is None:  # pragma: no cover - unreachable via validated config
        raise ValueError(f"Unknown log level: {name!r}")
    return number
