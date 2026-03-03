"""heinzel_core.config.

Konfigurationssystem: YAML, ENV-Override, Singleton.
"""

from __future__ import annotations

import os
from pathlib import Path
import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, field_validator

# ---------------------------------------------------------------------------
# Sub-Models
# ---------------------------------------------------------------------------


class HeinzelIdentity(BaseModel):
    """Identität des Heinzel-Agenten."""

    id: str = "heinzel-01"
    name: str = "Heinzel"
    role: str = "assistant"
    goal: str = "Helfe dem Nutzer so gut wie möglich."
    backstory: str = ""


class ProviderDefaults(BaseModel):
    """Standard-Einstellungen für LLM-Provider."""

    default: str = "anthropic"
    timeout: int = 60
    retries: int = 3


class ProviderEntry(BaseModel):
    """Einzelner Provider-Eintrag."""

    url: str
    name: str


class DatabaseConfig(BaseModel):
    """Optionale Datenbank-Konfiguration."""

    url: str | None = None
    pool_min: int = 1
    pool_max: int = 10


class SessionConfig(BaseModel):
    """Session-Verhalten."""

    max_history: int = 50
    auto_resume: bool = True


class LoggingConfig(BaseModel):
    """Logging-Konfiguration."""

    dialog_log_path: str = "logs/dialog"
    system_log_path: str = "logs/system"
    rotation_size_mb: int = 10
    retention_days: int = 30


class SkillsConfig(BaseModel):
    """Skills-Konfiguration."""

    skills_dir: str = "skills"
    autoload: bool = True


# ---------------------------------------------------------------------------
# Haupt-Config
# ---------------------------------------------------------------------------


class HeinzelConfig(BaseModel):
    """Vollständige Heinzel-Konfiguration."""

    heinzel: HeinzelIdentity = HeinzelIdentity()
    provider: ProviderDefaults = ProviderDefaults()
    providers: dict[str, ProviderEntry] = {}
    database: DatabaseConfig | None = None
    session: SessionConfig = SessionConfig()
    logging: LoggingConfig = LoggingConfig()
    skills: SkillsConfig = SkillsConfig()

    @field_validator("providers", mode="before")
    @classmethod
    def parse_providers(cls, v: object) -> object:
        """Wandelt raw-dicts in ProviderEntry-Objekte um."""
        if isinstance(v, dict):
            return {
                k: (ProviderEntry(**val) if isinstance(val, dict) else val)
                for k, val in v.items()
            }
        return v


# ---------------------------------------------------------------------------
# ENV-Override
# ---------------------------------------------------------------------------

_ENV_PREFIX = "HEINZEL_"


def _apply_env_overrides(data: dict) -> dict:
    """Überschreibt Config-Felder per HEINZEL_SECTION_FIELD=value."""
    for key, value in os.environ.items():
        if not key.startswith(_ENV_PREFIX):
            continue
        parts = key[len(_ENV_PREFIX):].lower().split("_", 1)
        if len(parts) != 2:
            continue
        section, field = parts
        if section in data and isinstance(data[section], dict):
            data[section][field] = value
        else:
            # Sektion fehlt oder kein dict — neu anlegen
            data[section] = {field: value}
    return data


# ---------------------------------------------------------------------------
# Standardpfade
# ---------------------------------------------------------------------------


def find_config_file() -> Path | None:
    """Sucht heinzel.yaml in Standardpfaden. Gibt ersten Fund zurück."""
    search_paths = [
        Path.cwd() / "heinzel.yaml",
        Path.cwd() / "config" / "heinzel.yaml",
        Path.home() / ".config" / "heinzel" / "heinzel.yaml",
        Path("/etc/heinzel/heinzel.yaml"),
    ]
    for path in search_paths:
        if path.exists():
            return path
    return None


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_config_cache: HeinzelConfig | None = None


def get_config(path: str | Path | None = None) -> HeinzelConfig:
    """Lädt und cached die Konfiguration.

    Args:
        path: Optionaler Pfad zu einer YAML-Datei. Ohne Angabe sucht
              find_config_file() in Standardpfaden. Ohne Datei: Defaults.

    Returns:
        HeinzelConfig-Singleton.
    """
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    load_dotenv()

    data: dict = {}

    config_path = Path(path) if path else find_config_file()
    if config_path and config_path.exists():
        with config_path.open("r", encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
            if loaded and isinstance(loaded, dict):
                data = loaded

    data = _apply_env_overrides(data)

    _config_cache = HeinzelConfig(**data)
    return _config_cache


def reset_config() -> None:
    """Setzt den Config-Cache zurück (für Tests)."""
    global _config_cache
    _config_cache = None
