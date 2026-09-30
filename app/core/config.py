"""Configuration loading, validation and provenance.

Precedence, later wins:

    built-in defaults  ->  config/rudra.toml  ->  RUDRA_* environment variables

Two deliberate design choices:

*   **Unknown keys are rejected.** A typo such as `consol_level` would otherwise
    be silently ignored and the user would believe a setting had taken effect. The
    error names the offending key and lists the valid ones.
*   **Every effective value records where it came from.** `python -m app config`
    prints the value and its origin. This is the same instinct the specification
    applies to knowledge (Part 2 section 49): a value without provenance invites
    people to guess.

A missing config file is not an error - the defaults are used and the fact is
reported. A config file the user *explicitly* pointed at with --config is an error
if it is missing, because silently ignoring an explicit instruction would be
expanding on what the user asked for.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from app.config.schema import FIELDS, FieldSpec, RudraConfig, build, section_names
from app.core.errors import ConfigurationError, FailureCategory
from app.version import CONFIG_SCHEMA_VERSION

#: Origin labels used in the provenance map.
ORIGIN_DEFAULT = "default"

DEFAULT_CONFIG_RELATIVE_PATH = Path("config") / "rudra.toml"


@dataclass(frozen=True, slots=True)
class LoadedConfig:
    """The effective configuration plus where each value came from."""

    config: RudraConfig
    #: dotted key -> origin ("default", "file:<path>", "env:<VAR>")
    sources: Mapping[str, str]
    #: The file that was consulted, if any.
    config_file: Path | None
    #: False when the file was absent and defaults were used.
    file_present: bool

    def origin_of(self, dotted_key: str) -> str:
        return self.sources[dotted_key]


def load_config(
    *,
    project_root: Path,
    config_path: Path | None = None,
    env: Mapping[str, str] | None = None,
) -> LoadedConfig:
    """Load, validate and assemble the configuration.

    Raises `ConfigurationError` with an actionable report if anything is wrong.
    """
    import os

    environ = os.environ if env is None else env
    explicit = config_path is not None
    path = config_path if explicit else project_root / DEFAULT_CONFIG_RELATIVE_PATH

    values: dict[str, object] = {spec.dotted: spec.default for spec in FIELDS}
    sources: dict[str, str] = {spec.dotted: ORIGIN_DEFAULT for spec in FIELDS}

    file_present = path.is_file()
    if explicit and not file_present:
        raise ConfigurationError.of(
            "The configuration file you specified does not exist.",
            f"No file was found at {path}.",
            stage="config.load",
            missing=(str(path),),
            data_changed=False,
            retry_safe=True,
            next_options=(
                "Check the path given to --config.",
                "Omit --config to use config/rudra.toml, or the built-in defaults.",
            ),
        )

    if file_present:
        _apply_file(path, values, sources)

    _apply_env(environ, values, sources)
    _check_schema_version(values, sources)

    return LoadedConfig(
        config=build(values),
        sources=sources,
        config_file=path if file_present else (path if explicit else None),
        file_present=file_present,
    )


def _apply_file(path: Path, values: dict[str, object], sources: dict[str, str]) -> None:
    """Read the TOML file, validate every key, and merge it into `values`."""
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigurationError.of(
            "The configuration file is not valid TOML and could not be read.",
            str(exc),
            stage="config.parse",
            data_changed=False,
            retry_safe=True,
            detail=f"file={path}",
            cause=repr(exc),
            next_options=(
                f"Fix the syntax error in {path}.",
                "Delete the file to fall back to the built-in defaults.",
            ),
        ) from exc
    except OSError as exc:
        raise ConfigurationError.of(
            "The configuration file exists but could not be read.",
            f"{type(exc).__name__}: {exc}",
            stage="config.read",
            data_changed=False,
            retry_safe=True,
            cause=repr(exc),
            next_options=(
                f"Check read permissions on {path}.",
                "Delete the file to fall back to the built-in defaults.",
            ),
        ) from exc

    known_sections = set(section_names())
    by_dotted = {spec.dotted: spec for spec in FIELDS}

    for key, value in raw.items():
        if isinstance(value, dict):
            if key not in known_sections:
                raise _unknown(f"[{key}]", "section", sorted(known_sections), path)
            for sub_key, sub_value in value.items():
                spec = by_dotted.get(f"{key}.{sub_key}")
                if spec is None:
                    valid = [s.name for s in FIELDS if s.section == key]
                    raise _unknown(f"{key}.{sub_key}", "setting", sorted(valid), path)
                values[spec.dotted] = _validate(spec, sub_value, f"file:{path}")
                sources[spec.dotted] = f"file:{path}"
        else:
            spec = by_dotted.get(key)
            if spec is None or spec.section is not None:
                valid = [s.dotted for s in FIELDS if s.section is None]
                raise _unknown(key, "top-level setting", sorted(valid), path)
            values[spec.dotted] = _validate(spec, value, f"file:{path}")
            sources[spec.dotted] = f"file:{path}"


def _apply_env(
    environ: Mapping[str, str], values: dict[str, object], sources: dict[str, str]
) -> None:
    """Apply RUDRA_* environment overrides."""
    for spec in FIELDS:
        raw = environ.get(spec.env_var)
        if raw is None:
            continue
        origin = f"env:{spec.env_var}"
        values[spec.dotted] = _validate(spec, _from_string(spec, raw, origin), origin)
        sources[spec.dotted] = origin


def _from_string(spec: FieldSpec, raw: str, origin: str) -> object:
    """Convert an environment variable string to the declared type."""
    if spec.kind is int:
        try:
            return int(raw.strip())
        except ValueError as exc:
            raise ConfigurationError.of(
                f"The setting {spec.dotted} must be a whole number.",
                f"{origin} is {raw!r}, which is not a whole number.",
                stage="config.env",
                data_changed=False,
                retry_safe=True,
                next_options=(
                    f"Set {spec.env_var} to a whole number.",
                    f"Unset {spec.env_var} to use the configured or default value.",
                ),
                cause=repr(exc),
            ) from exc
    return raw


def _validate(spec: FieldSpec, value: object, origin: str) -> object:
    """Check one value against its FieldSpec, or raise an actionable error."""
    # bool is a subclass of int in Python; accepting True as 1 would hide a mistake.
    if spec.kind is int and (isinstance(value, bool) or not isinstance(value, int)):
        raise ConfigurationError.of(
            f"The setting {spec.dotted} must be a whole number.",
            f"{origin} provides {value!r} ({type(value).__name__}).",
            stage="config.validate",
            data_changed=False,
            retry_safe=True,
            next_options=(f"Set {spec.dotted} to a whole number.",),
        )
    if spec.kind is str and not isinstance(value, str):
        raise ConfigurationError.of(
            f"The setting {spec.dotted} must be text.",
            f"{origin} provides {value!r} ({type(value).__name__}).",
            stage="config.validate",
            data_changed=False,
            retry_safe=True,
            next_options=(f"Quote the value of {spec.dotted} in the config file.",),
        )
    if spec.choices is not None and value not in spec.choices:
        raise ConfigurationError.of(
            f"The setting {spec.dotted} has an unsupported value.",
            f"{origin} provides {value!r}.",
            stage="config.validate",
            available=spec.choices,
            data_changed=False,
            retry_safe=True,
            next_options=(f"Use one of: {', '.join(spec.choices)}.",),
        )
    if spec.minimum is not None and isinstance(value, int) and value < spec.minimum:
        raise ConfigurationError.of(
            f"The setting {spec.dotted} is below the smallest usable value.",
            f"{origin} provides {value}, the minimum is {spec.minimum}.",
            stage="config.validate",
            data_changed=False,
            retry_safe=True,
            next_options=(f"Set {spec.dotted} to {spec.minimum} or more.",),
        )
    return value


def _check_schema_version(values: dict[str, object], sources: dict[str, str]) -> None:
    """Refuse a configuration format this build does not understand.

    Part 4 section 150 requires schema versioning; refusing is safer than guessing
    what unfamiliar keys were meant to do.
    """
    found = values["config_schema_version"]
    if found == CONFIG_SCHEMA_VERSION:
        return
    raise ConfigurationError.of(
        "The configuration file uses a format this version of RUDRA does not support.",
        f"The file declares config_schema_version = {found}; "
        f"this build supports {CONFIG_SCHEMA_VERSION}.",
        category=FailureCategory.INVALID_INPUT,
        stage="config.schema_version",
        available=(f"supported config_schema_version: {CONFIG_SCHEMA_VERSION}",),
        data_changed=False,
        retry_safe=True,
        detail=f"origin={sources['config_schema_version']}",
        next_options=(
            "Use a RUDRA build that supports this configuration format.",
            f"Set config_schema_version = {CONFIG_SCHEMA_VERSION} and review the file "
            "against the documented settings in config/rudra.toml before doing so.",
        ),
    )


def _unknown(
    key: str, what: str, valid: list[str], path: Path
) -> ConfigurationError:
    """Build the error raised for an unrecognised configuration key."""
    return ConfigurationError.of(
        f"The configuration file contains an unknown {what}: {key}",
        "RUDRA rejects unknown settings rather than ignoring them, because an "
        "ignored setting looks as though it took effect.",
        stage="config.validate",
        available=tuple(valid),
        data_changed=False,
        retry_safe=True,
        detail=f"file={path}",
        next_options=(
            f"Remove or correct {key} in {path}.",
            "Run 'python -m app config' to see every valid setting.",
        ),
    )
