"""Composition root and startup sequence.

Part 5 section 182 calls for a "basic dependency injection/service mechanism if
useful". A container framework is not useful at this size and would be the kind of
infrastructure Part 6 section 51 warns against. What is useful is a single place
where dependencies are built and passed explicitly, and an immutable context object
that carries them.

Rules this file exists to keep (Part 1 section 24):

* No global mutable state. Everything a component needs arrives as an argument.
* No service reaches for configuration or paths on its own.
* Startup order is written down once, in `start_application`.

Startup is the Phase 1 acceptance sequence (Part 5 section 183):

    load configuration -> derive paths -> create directories -> initialise logging
    -> report environment -> exit cleanly
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping

from app.config.schema import RudraConfig
from app.core import environment
from app.core.config import LoadedConfig, load_config
from app.core.environment import EnvironmentReport
from app.core.logging_setup import LoggingReport, RudraLogger, get_logger, setup_logging, shutdown_logging
from app.core.paths import DirectoryReport, PathLayout


@dataclass(frozen=True, slots=True)
class AppContext:
    """Everything a RUDRA component may need, assembled once at startup."""

    config: RudraConfig
    loaded_config: LoadedConfig
    paths: PathLayout
    logger: RudraLogger
    logging_report: LoggingReport
    directory_report: DirectoryReport
    started_at: datetime

    @property
    def config_sources(self) -> Mapping[str, str]:
        return self.loaded_config.sources


@dataclass(frozen=True, slots=True)
class StartupResult:
    """What startup did, so the caller can report facts rather than intentions."""

    context: AppContext
    report: EnvironmentReport


def start_application(
    *,
    project_root: Path,
    config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> StartupResult:
    """Run the startup sequence and return the assembled context.

    Raises `RudraError` (with a full failure report) if any step fails. Nothing is
    reported as successful unless it actually happened.
    """
    started_at = datetime.now(timezone.utc).astimezone()
    loaded = load_config(project_root=project_root, config_path=config_path, env=env)
    layout = PathLayout.from_config(project_root, loaded.config)
    directory_report = layout.ensure()
    logging_report = setup_logging(layout, loaded.config)

    logger = get_logger("core.startup")
    logger.info(
        "RUDRA starting: instance=%s config=%s",
        loaded.config.app.instance_name,
        loaded.config_file if loaded.file_present else "built-in defaults",
    )
    logger.debug(
        "Directories: %d created, %d already present",
        len(directory_report.created),
        len(directory_report.existing),
    )
    for created in directory_report.created:
        logger.debug("Created directory: %s", created)

    report = environment.collect(
        layout=layout,
        config=loaded.config,
        config_file=loaded.config_file,
        config_file_present=loaded.file_present,
    )
    for problem in report.problems():
        logger.warning(
            "Environment: %s is %s - %s", problem.component, problem.status, problem.impact
        )

    context = AppContext(
        config=loaded.config,
        loaded_config=loaded,
        paths=layout,
        logger=logger,
        logging_report=logging_report,
        directory_report=directory_report,
        started_at=started_at,
    )
    return StartupResult(context=context, report=report)


def stop_application(context: AppContext, *, exit_code: int) -> None:
    """Shut down cleanly: record the outcome, then flush and close log handlers."""
    elapsed = (datetime.now(timezone.utc).astimezone() - context.started_at).total_seconds()
    context.logger.info("RUDRA exiting with code %d after %.3f s", exit_code, elapsed)
    shutdown_logging()
