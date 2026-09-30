"""Configuration tests.

Covers the categories Part 4 section 162 asks for: normal input, empty input,
missing data, malformed data and invalid parameters.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config.schema import FIELDS, defaults
from app.core.config import load_config
from app.core.errors import ConfigurationError, FailureCategory


def test_defaults_load_when_no_file_exists(project_root: Path):
    loaded = load_config(project_root=project_root, env={})
    assert loaded.file_present is False
    assert loaded.config == defaults()
    assert all(origin == "default" for origin in loaded.sources.values())


def test_missing_file_is_not_an_error_but_is_reported(project_root: Path):
    loaded = load_config(project_root=project_root, env={})
    assert loaded.config_file is None
    assert loaded.config.logging.console_level == "INFO"


def test_explicitly_named_missing_file_is_an_error(project_root: Path):
    """Silently ignoring an explicit instruction would be expanding on the request."""
    with pytest.raises(ConfigurationError) as caught:
        load_config(
            project_root=project_root,
            config_path=project_root / "nope.toml",
            env={},
        )
    report = caught.value.report
    assert report.category is FailureCategory.INVALID_INPUT
    assert report.next_options


def test_empty_file_falls_back_to_defaults(project_root: Path, config_file):
    config_file("")
    loaded = load_config(project_root=project_root, env={})
    assert loaded.file_present is True
    assert loaded.config == defaults()


def test_file_values_override_defaults(project_root: Path, config_file):
    config_file('[logging]\nconsole_level = "WARNING"\n')
    loaded = load_config(project_root=project_root, env={})
    assert loaded.config.logging.console_level == "WARNING"
    assert loaded.origin_of("logging.console_level").startswith("file:")
    # Untouched settings keep their default and say so.
    assert loaded.origin_of("logging.file_level") == "default"


def test_environment_overrides_the_file(project_root: Path, config_file):
    config_file('[logging]\nconsole_level = "WARNING"\n')
    loaded = load_config(
        project_root=project_root, env={"RUDRA_LOGGING_CONSOLE_LEVEL": "ERROR"}
    )
    assert loaded.config.logging.console_level == "ERROR"
    assert loaded.origin_of("logging.console_level") == "env:RUDRA_LOGGING_CONSOLE_LEVEL"


def test_integer_from_environment_is_converted(project_root: Path):
    loaded = load_config(
        project_root=project_root, env={"RUDRA_LOGGING_BACKUP_COUNT": "7"}
    )
    assert loaded.config.logging.backup_count == 7


def test_non_numeric_environment_integer_is_rejected(project_root: Path):
    with pytest.raises(ConfigurationError) as caught:
        load_config(project_root=project_root, env={"RUDRA_LOGGING_MAX_BYTES": "big"})
    assert "whole number" in caught.value.report.summary


def test_malformed_toml_is_reported_with_a_fix(project_root: Path, config_file):
    config_file("[logging\nconsole_level = 'INFO'\n")
    with pytest.raises(ConfigurationError) as caught:
        load_config(project_root=project_root, env={})
    report = caught.value.report
    assert report.stage == "config.parse"
    assert report.data_changed is False
    assert report.next_options


def test_unknown_setting_is_rejected_not_ignored(project_root: Path, config_file):
    """A silently ignored typo looks to the user like a setting that took effect."""
    config_file('[logging]\nconsol_level = "INFO"\n')
    with pytest.raises(ConfigurationError) as caught:
        load_config(project_root=project_root, env={})
    report = caught.value.report
    assert "consol_level" in report.summary
    assert "console_level" in report.available


def test_unknown_section_is_rejected(project_root: Path, config_file):
    config_file('[loggin]\nconsole_level = "INFO"\n')
    with pytest.raises(ConfigurationError) as caught:
        load_config(project_root=project_root, env={})
    assert "[loggin]" in caught.value.report.summary


def test_wrong_type_is_rejected(project_root: Path, config_file):
    config_file("[logging]\nconsole_level = 5\n")
    with pytest.raises(ConfigurationError) as caught:
        load_config(project_root=project_root, env={})
    assert "must be text" in caught.value.report.summary


def test_boolean_is_not_accepted_as_an_integer(project_root: Path, config_file):
    """bool is a subclass of int in Python; accepting true as 1 would hide a mistake."""
    config_file("[logging]\nbackup_count = true\n")
    with pytest.raises(ConfigurationError) as caught:
        load_config(project_root=project_root, env={})
    assert "whole number" in caught.value.report.summary


def test_value_outside_the_allowed_choices_is_rejected(project_root: Path, config_file):
    config_file('[logging]\nconsole_level = "CHATTY"\n')
    with pytest.raises(ConfigurationError) as caught:
        load_config(project_root=project_root, env={})
    report = caught.value.report
    assert "unsupported value" in report.summary
    assert "DEBUG" in report.available


def test_value_below_minimum_is_rejected(project_root: Path, config_file):
    config_file("[logging]\nmax_bytes = 10\n")
    with pytest.raises(ConfigurationError) as caught:
        load_config(project_root=project_root, env={})
    assert "below the smallest usable value" in caught.value.report.summary


def test_unsupported_schema_version_is_refused(project_root: Path, config_file):
    """Part 4 section 150: refuse an unknown format rather than guess at its keys."""
    config_file("config_schema_version = 99\n")
    with pytest.raises(ConfigurationError) as caught:
        load_config(project_root=project_root, env={})
    report = caught.value.report
    assert report.stage == "config.schema_version"
    assert "99" in report.reason


def test_configuration_is_immutable(project_root: Path):
    loaded = load_config(project_root=project_root, env={})
    with pytest.raises(AttributeError):
        loaded.config.logging.console_level = "DEBUG"  # type: ignore[misc]


def test_every_field_declares_a_unique_key_and_env_var():
    dotted = [spec.dotted for spec in FIELDS]
    env_vars = [spec.env_var for spec in FIELDS]
    assert len(dotted) == len(set(dotted))
    assert len(env_vars) == len(set(env_vars))
    assert all(var.startswith("RUDRA_") for var in env_vars)


def test_every_field_is_documented():
    """`python -m app config` prints these; an undocumented setting is unusable."""
    assert all(spec.description.strip() for spec in FIELDS)


def test_shipped_config_file_matches_the_schema():
    """The config/rudra.toml committed to the repository must actually be valid."""
    from tests.conftest import PROJECT_ROOT

    loaded = load_config(project_root=PROJECT_ROOT, env={})
    assert loaded.file_present is True
    assert loaded.config.config_schema_version == defaults().config_schema_version
