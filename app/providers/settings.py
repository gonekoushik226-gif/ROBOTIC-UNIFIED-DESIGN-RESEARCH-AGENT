"""Whether optional AI assistance is enabled, and with which provider and model.

Kept in ``<config folder>/ai.json``. The file holds no key (keys live in the Windows
Credential Manager) and is not part of knowledge-base backups. AI assistance is off
unless this file says it was enabled with the user's consent.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

FILE_NAME = "ai.json"


@dataclass(frozen=True)
class AiSettings:
    enabled: bool = False
    provider: str | None = None
    model: str | None = None
    #: When the user agreed that selected text may be sent to the provider.
    consented_at: str | None = None

    @property
    def active(self) -> bool:
        return self.enabled and bool(self.provider) and bool(self.model) and bool(self.consented_at)


def settings_path(config_dir: Path) -> Path:
    return Path(config_dir) / FILE_NAME


def load_settings(config_dir: Path) -> AiSettings:
    try:
        data = json.loads(settings_path(config_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return AiSettings()
    if not isinstance(data, dict):
        return AiSettings()
    return AiSettings(enabled=data.get("enabled") is True, provider=data.get("provider") or None,
                      model=data.get("model") or None, consented_at=data.get("consented_at") or None)


def save_settings(config_dir: Path, settings: AiSettings) -> None:
    path = settings_path(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")


def enable(config_dir: Path, provider: str, model: str, *, consent: bool) -> AiSettings:
    """Turn AI assistance on. Refused without the user's explicit consent."""
    if not consent:
        raise ValueError("AI assistance is enabled only with the user's explicit consent.")
    settings = AiSettings(True, provider, model, datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
    save_settings(config_dir, settings)
    return settings


def disable(config_dir: Path) -> AiSettings:
    current = load_settings(config_dir)
    settings = AiSettings(False, current.provider, current.model, None)
    save_settings(config_dir, settings)
    return settings
