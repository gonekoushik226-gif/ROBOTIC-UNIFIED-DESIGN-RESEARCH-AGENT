"""Configuration schema, defaults and field descriptions.

The schema is declared once, as data (`FIELDS`). The loader in `app.core.config`
uses that table to validate a file, apply environment overrides, reject unknown
keys and report where each effective value came from. Adding a setting means
adding one `FieldSpec` and one dataclass field, nothing else.

Configuration separation (Part 6 section 22): this file holds application defaults
only. User values live in the top-level `config/rudra.toml`. Secrets live in
neither; RUDRA has no secret today, and when it needs one it will come from the
Windows credential store.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from app.version import CONFIG_SCHEMA_VERSION

LOG_LEVEL_CHOICES: Final = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL", "AUDIT")


@dataclass(frozen=True, slots=True)
class FieldSpec:
    """Description of one configuration setting."""

    #: TOML table the setting lives in. None means top level.
    section: str | None
    name: str
    kind: type
    default: object
    description: str
    #: Allowed values, for string settings that are really enumerations.
    choices: tuple[str, ...] | None = None
    #: Smallest acceptable value, for integer settings.
    minimum: int | None = None

    @property
    def dotted(self) -> str:
        """The key as a user writes it, e.g. "logging.console_level"."""
        return f"{self.section}.{self.name}" if self.section else self.name

    @property
    def env_var(self) -> str:
        """The environment variable that overrides this setting."""
        parts = ["RUDRA"]
        if self.section:
            parts.append(self.section)
        parts.append(self.name)
        return "_".join(part.upper() for part in parts)


FIELDS: Final[tuple[FieldSpec, ...]] = (
    FieldSpec(
        None,
        "config_schema_version",
        int,
        CONFIG_SCHEMA_VERSION,
        "Version of the configuration file format. RUDRA refuses a version it does "
        "not understand rather than guessing what the keys mean.",
        minimum=1,
    ),
    FieldSpec(
        "app",
        "instance_name",
        str,
        "default",
        "Label shown in reports and logs. Does not change behaviour.",
    ),
    FieldSpec(
        "paths",
        "data_root",
        str,
        "data",
        "Root of the data layers (documents, database, indexes, cache, backups). "
        "Relative paths are resolved against the project root.",
    ),
    FieldSpec(
        "paths",
        "logs_dir",
        str,
        "logs",
        "Directory for log files. Relative paths are resolved against the project root.",
    ),
    FieldSpec(
        "logging",
        "console_level",
        str,
        "INFO",
        "Lowest level printed to the terminal.",
        choices=LOG_LEVEL_CHOICES,
    ),
    FieldSpec(
        "logging",
        "file_level",
        str,
        "DEBUG",
        "Lowest level written to the log file.",
        choices=LOG_LEVEL_CHOICES,
    ),
    FieldSpec("logging", "file_name", str, "rudra.log", "Main log file name."),
    FieldSpec(
        "logging",
        "audit_file_name",
        str,
        "audit.log",
        "Audit channel file name. AUDIT records go only here, never to the main log "
        "or the console, so that ordinary logs stay free of request content.",
    ),
    FieldSpec(
        "logging",
        "max_bytes",
        int,
        5 * 1024 * 1024,
        "Size at which a log file is rotated. Keeps logs from becoming an "
        "uncontrolled storage dump.",
        minimum=4096,
    ),
    FieldSpec(
        "logging",
        "backup_count",
        int,
        3,
        "Number of rotated log files kept.",
        minimum=0,
    ),
    FieldSpec(
        "resources",
        "min_free_disk_mb",
        int,
        2048,
        "Free disk space below which RUDRA reports a critical storage shortage.",
        minimum=0,
    ),
    FieldSpec(
        "resources",
        "warn_free_disk_mb",
        int,
        10240,
        "Free disk space below which RUDRA reports a storage warning.",
        minimum=0,
    ),
    FieldSpec(
        "resources",
        "warn_available_ram_mb",
        int,
        1024,
        "Available RAM below which RUDRA reports memory pressure.",
        minimum=0,
    ),
)


@dataclass(frozen=True, slots=True)
class AppSection:
    instance_name: str


@dataclass(frozen=True, slots=True)
class PathsSection:
    data_root: str
    logs_dir: str


@dataclass(frozen=True, slots=True)
class LoggingSection:
    console_level: str
    file_level: str
    file_name: str
    audit_file_name: str
    max_bytes: int
    backup_count: int


@dataclass(frozen=True, slots=True)
class ResourcesSection:
    min_free_disk_mb: int
    warn_free_disk_mb: int
    warn_available_ram_mb: int


@dataclass(frozen=True, slots=True)
class RudraConfig:
    """The effective configuration. Immutable: no global mutable state."""

    config_schema_version: int
    app: AppSection
    paths: PathsSection
    logging: LoggingSection
    resources: ResourcesSection


_SECTION_TYPES: Final = {
    "app": AppSection,
    "paths": PathsSection,
    "logging": LoggingSection,
    "resources": ResourcesSection,
}


def section_names() -> tuple[str, ...]:
    """Names of the TOML tables the schema defines."""
    return tuple(_SECTION_TYPES)


def build(values: dict[str, object]) -> RudraConfig:
    """Assemble a RudraConfig from a flat mapping of dotted keys to values.

    The loader is responsible for making sure every key exists and every value is
    valid before calling this.
    """
    sections: dict[str, object] = {}
    for section, cls in _SECTION_TYPES.items():
        kwargs = {
            spec.name: values[spec.dotted] for spec in FIELDS if spec.section == section
        }
        sections[section] = cls(**kwargs)  # type: ignore[arg-type]
    return RudraConfig(
        config_schema_version=int(values["config_schema_version"]),  # type: ignore[call-overload]
        app=sections["app"],  # type: ignore[arg-type]
        paths=sections["paths"],  # type: ignore[arg-type]
        logging=sections["logging"],  # type: ignore[arg-type]
        resources=sections["resources"],  # type: ignore[arg-type]
    )


def defaults() -> RudraConfig:
    """The configuration RUDRA uses when no file and no overrides are present."""
    return build({spec.dotted: spec.default for spec in FIELDS})
