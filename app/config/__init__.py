"""Configuration schema and defaults (pure data).

This package must not import from any other RUDRA package. It describes what a
valid configuration looks like; `app.core.config` does the loading and validation.
Keeping the two apart means the schema can be inspected, printed and tested
without touching the filesystem.
"""

from app.config.schema import (
    FIELDS,
    AppSection,
    FieldSpec,
    LoggingSection,
    PathsSection,
    ResourcesSection,
    RudraConfig,
    defaults,
)

__all__ = [
    "FIELDS",
    "AppSection",
    "FieldSpec",
    "LoggingSection",
    "PathsSection",
    "ResourcesSection",
    "RudraConfig",
    "defaults",
]
