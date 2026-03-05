"""Tests für HeinzelLoader — get_config()-Integration, AddOn-Reihenfolge, Provider-Fallback."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from core.startup import HeinzelLoader
from core.config import get_config, reset_config, AgentConfig
from core.provider import NoopProvider


# =============================================================================
# Hilfsfunktionen
# =============================================================================


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "heinzel.yaml"
    p.write_text(content, encoding="utf-8")
    return p


# =============================================================================
# Config-Integration
# =============================================================================


def test_get_config_reads_agent(tmp_path):
    cfg = _write_yaml(tmp_path, "agent:\n  name: riker\n  id: riker-01\n")
    reset_config()
    config = get_config(cfg)
    assert config.agent.name == "riker"
    assert config.agent.id == "riker-01"


def test_get_config_addons_field(tmp_path):
    cfg = _write_yaml(tmp_path, """
addons:
  database:
    backend: sqlite
  web_search:
    backend: duckduckgo
""")
    reset_config()
    config = get_config(cfg)
    assert "database" in config.addons
    assert "web_search" in config.addons


def test_get_config_env_override(tmp_path, monkeypatch):
    cfg = _write_yaml(tmp_path, "agent:\n  name: original\n")
    monkeypatch.setenv("AGENT_AGENT_NAME", "overridden")
    reset_config()
    config = get_config(cfg)
    assert config.agent.name == "overridden"


def test_get_config_defaults_without_file():
    reset_config()
    config = get_config("/tmp/nonexistent_heinzel_xyz.yaml")
    assert isinstance(config, AgentConfig)
    assert config.agent.name == "Agent"  # Default


# =============================================================================
# Runner bauen
# =============================================================================


def test_build_runner_uses_agent_name(tmp_path):
    cfg = _write_yaml(tmp_path, "agent:\n  name: riker\n")
    loader = HeinzelLoader(config_path=cfg)
    reset_config()
    loader._config = get_config(cfg)
    runner = loader._build_runner()
    assert runner._name == "riker"


def test_build_runner_noop_without_provider(tmp_path):
    cfg = _write_yaml(tmp_path, "agent:\n  name: test\n")
    reset_config()
    loader = HeinzelLoader(config_path=cfg)
    loader._config = get_config(cfg)
    runner = loader._build_runner()
    assert isinstance(runner._provider, NoopProvider)


# =============================================================================
# AddOns registrieren
# =============================================================================


def test_register_sqlite_addon(tmp_path):
    cfg = _write_yaml(tmp_path, """
addons:
  database:
    backend: sqlite
    path: ":memory:"
""")
    reset_config()
    loader = HeinzelLoader(config_path=cfg)
    loader._config = get_config(cfg)
    runner = loader._build_runner()
    loader._register_addons(runner)
    assert any(a.name == "database" for a in runner._addons)


def test_register_addons_order(tmp_path):
    """database muss vor dialog_logger registriert sein."""
    cfg = _write_yaml(tmp_path, """
addons:
  dialog_logger:
    log_dir: /tmp/test-logs
  database:
    backend: sqlite
""")
    reset_config()
    loader = HeinzelLoader(config_path=cfg)
    loader._config = get_config(cfg)
    runner = loader._build_runner()
    loader._register_addons(runner)
    names = [a.name for a in runner._addons]
    assert names.index("database") < names.index("dialog_logger")


def test_register_unknown_addon_skipped(tmp_path):
    cfg = _write_yaml(tmp_path, "addons:\n  nicht_existent:\n    foo: bar\n")
    reset_config()
    loader = HeinzelLoader(config_path=cfg)
    loader._config = get_config(cfg)
    runner = loader._build_runner()
    loader._register_addons(runner)
    assert len(runner._addons) == 0


def test_register_custom_factory(tmp_path):
    cfg = _write_yaml(tmp_path, "addons:\n  database: {}\n")
    reset_config()
    loader = HeinzelLoader(config_path=cfg)
    loader._config = get_config(cfg)

    mock_addon = MagicMock()
    mock_addon.name = "database"
    loader.register_addon_factory("database", lambda cfg, config: mock_addon)

    runner = loader._build_runner()
    loader._register_addons(runner)
    assert runner._addons[0] is mock_addon


# =============================================================================
# build() — Vollständiger Durchlauf
# =============================================================================


@pytest.mark.asyncio
async def test_build_returns_connected_runner(tmp_path):
    cfg = _write_yaml(tmp_path, """
agent:
  name: test-heinzel
addons:
  database:
    backend: sqlite
    path: ":memory:"
""")
    loader = HeinzelLoader(config_path=cfg)
    runner = await loader.build()
    assert runner._connected is True
    assert runner._name == "test-heinzel"
    await runner.disconnect()


@pytest.mark.asyncio
async def test_build_no_addons(tmp_path):
    cfg = _write_yaml(tmp_path, "agent:\n  name: minimal\n")
    loader = HeinzelLoader(config_path=cfg)
    runner = await loader.build()
    assert runner._connected is True
    await runner.disconnect()


@pytest.mark.asyncio
async def test_build_web_search_addon(tmp_path):
    cfg = _write_yaml(tmp_path, """
addons:
  web_search:
    backend: duckduckgo
    max_results: 3
""")
    loader = HeinzelLoader(config_path=cfg)
    runner = await loader.build()
    assert any(a.name == "web_search" for a in runner._addons)
    await runner.disconnect()


# =============================================================================
# NoopProvider
# =============================================================================


@pytest.mark.asyncio
async def test_noop_provider_returns_empty():
    result = await NoopProvider().chat([])
    assert result == ""


@pytest.mark.asyncio
async def test_noop_provider_stream_empty():
    chunks = [c async for c in NoopProvider().stream([])]
    assert chunks == []
