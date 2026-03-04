"""Tests für heinzel_core.config."""

import os
import textwrap
from pathlib import Path

import pytest

from core.config import (
    AgentConfig,
    find_config_file,
    get_config,
    reset_config,
)


@pytest.fixture(autouse=True)
def clean_config(tmp_path, monkeypatch):
    """Stellt sicher, dass der Config-Cache vor jedem Test leer ist."""
    reset_config()
    # CWD auf tmp_path setzen, damit kein versehentliches heinzel.yaml gefunden wird
    monkeypatch.chdir(tmp_path)
    yield
    reset_config()


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------


def test_defaults_without_file():
    """Ohne YAML-Datei und ohne ENV → vollständige Defaults."""
    cfg = get_config()
    assert isinstance(cfg, AgentConfig)
    assert cfg.agent.name == "Agent"
    assert cfg.provider.default == "anthropic"
    assert cfg.provider.timeout == 60
    assert cfg.provider.retries == 3
    assert cfg.session.max_history == 50
    assert cfg.session.auto_resume is True
    assert cfg.database is None
    assert cfg.skills.autoload is True


# ---------------------------------------------------------------------------
# YAML laden
# ---------------------------------------------------------------------------


def test_load_yaml(tmp_path):
    """YAML-Datei wird korrekt geladen."""
    yaml_file = tmp_path / "heinzel.yaml"
    yaml_file.write_text(
        textwrap.dedent("""\
        agent:
          id: test-01
          name: TestHeinzel
          role: tester
          goal: Teste alles.
          backstory: Ein Test-Agent.
        provider:
          default: ollama
          timeout: 30
          retries: 1
        providers:
          ollama:
            url: http://localhost:11434
            name: Ollama
        session:
          max_history: 10
          auto_resume: false
        logging:
          dialog_log_path: /tmp/dialogs
          retention_days: 7
        skills:
          skills_dir: /tmp/skills
          autoload: false
        """),
        encoding="utf-8",
    )
    cfg = get_config(path=yaml_file)

    assert cfg.agent.id == "test-01"
    assert cfg.agent.name == "TestHeinzel"
    assert cfg.provider.default == "ollama"
    assert cfg.provider.timeout == 30
    assert cfg.providers["ollama"].url == "http://localhost:11434"
    assert cfg.session.max_history == 10
    assert cfg.session.auto_resume is False
    assert cfg.logging.retention_days == 7
    assert cfg.skills.autoload is False


def test_load_yaml_with_database(tmp_path):
    """Optionale database-Sektion wird geladen."""
    yaml_file = tmp_path / "heinzel.yaml"
    yaml_file.write_text(
        textwrap.dedent("""\
        database:
          url: postgresql://user:pass@localhost/heinzel
          pool_min: 2
          pool_max: 20
        """),
        encoding="utf-8",
    )
    cfg = get_config(path=yaml_file)
    assert cfg.database is not None
    assert cfg.database.url == "postgresql://user:pass@localhost/heinzel"
    assert cfg.database.pool_min == 2
    assert cfg.database.pool_max == 20


# ---------------------------------------------------------------------------
# ENV-Override
# ---------------------------------------------------------------------------


def test_env_override_provider_default(monkeypatch):
    """AGENT_PROVIDER_DEFAULT überschreibt provider.default."""
    monkeypatch.setenv("AGENT_PROVIDER_DEFAULT", "openai")
    cfg = get_config()
    assert cfg.provider.default == "openai"


def test_env_override_session_max_history(monkeypatch):
    """AGENT_SESSION_MAX_HISTORY überschreibt session.max_history."""
    monkeypatch.setenv("AGENT_SESSION_MAX_HISTORY", "99")
    cfg = get_config()
    assert cfg.session.max_history == 99


def test_env_override_database_url(monkeypatch):
    """AGENT_DATABASE_URL setzt database.url."""
    monkeypatch.setenv("AGENT_DATABASE_URL", "sqlite:///test.db")
    cfg = get_config()
    assert cfg.database is not None
    assert cfg.database.url == "sqlite:///test.db"


def test_env_override_wins_over_yaml(tmp_path, monkeypatch):
    """ENV-Override hat Vorrang gegenüber YAML-Wert."""
    yaml_file = tmp_path / "heinzel.yaml"
    yaml_file.write_text("provider:\n  default: ollama\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_PROVIDER_DEFAULT", "anthropic")
    cfg = get_config(path=yaml_file)
    assert cfg.provider.default == "anthropic"


# ---------------------------------------------------------------------------
# Validierungsfehler
# ---------------------------------------------------------------------------


def test_validation_error_invalid_providers(tmp_path):
    """Ungültiger providers-Eintrag (fehlendes url) → ValidationError."""
    from pydantic import ValidationError

    yaml_file = tmp_path / "heinzel.yaml"
    yaml_file.write_text(
        "providers:\n  broken:\n    name: Broken\n",
        encoding="utf-8",
    )
    with pytest.raises(ValidationError):
        get_config(path=yaml_file)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def test_singleton_same_instance():
    """Zwei Aufrufe von get_config() liefern dasselbe Objekt."""
    cfg1 = get_config()
    cfg2 = get_config()
    assert cfg1 is cfg2


def test_reset_config_clears_cache():
    """reset_config() erzwingt Neu-Laden beim nächsten get_config()."""
    cfg1 = get_config()
    reset_config()
    cfg2 = get_config()
    assert cfg1 is not cfg2


# ---------------------------------------------------------------------------
# find_config_file
# ---------------------------------------------------------------------------


def test_find_config_file_finds_in_cwd(tmp_path, monkeypatch):
    """find_config_file() findet heinzel.yaml im CWD."""
    monkeypatch.chdir(tmp_path)
    yaml_file = tmp_path / "heinzel.yaml"
    yaml_file.write_text("", encoding="utf-8")
    found = find_config_file()
    assert found == yaml_file


def test_find_config_file_returns_none_when_missing(tmp_path, monkeypatch):
    """find_config_file() gibt None zurück wenn keine Datei gefunden."""
    monkeypatch.chdir(tmp_path)
    # Sicherstellen dass ~/.config/heinzel/heinzel.yaml nicht existiert
    home_cfg = Path.home() / ".config" / "heinzel" / "heinzel.yaml"
    if not home_cfg.exists():
        assert find_config_file() is None
