"""Tests für HeinzelLoader — Config-Parsing, AddOn-Reihenfolge, ENV-Substitution, Provider-Fallback."""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from core.startup import HeinzelLoader, _substitute_env, _resolve_env, _find_config
from core.provider import NoopProvider


# =============================================================================
# Hilfsfunktionen
# =============================================================================


def _write_yaml(tmp_path: Path, content: str) -> Path:
    p = tmp_path / "heinzel.yaml"
    p.write_text(content, encoding="utf-8")
    return p


# =============================================================================
# ENV-Substitution
# =============================================================================


def test_resolve_env_substitutes(monkeypatch):
    monkeypatch.setenv("MY_TOKEN", "secret-123")
    assert _resolve_env("${MY_TOKEN}") == "secret-123"


def test_resolve_env_unknown_passthrough():
    result = _resolve_env("${UNKNOWN_VAR_XYZ}")
    assert result == "${UNKNOWN_VAR_XYZ}"


def test_substitute_env_recursive(monkeypatch):
    monkeypatch.setenv("DB_PASS", "pass123")
    data = {"db": {"dsn": "postgres://admin:${DB_PASS}@host/db"}}
    result = _substitute_env(data)
    assert "pass123" in result["db"]["dsn"]


def test_substitute_env_in_list(monkeypatch):
    monkeypatch.setenv("TOKEN", "tok")
    result = _substitute_env(["${TOKEN}", "other"])
    assert result[0] == "tok"


# =============================================================================
# Config laden
# =============================================================================


def test_loader_no_config_uses_defaults(tmp_path):
    loader = HeinzelLoader(config_path=tmp_path / "nonexistent.yaml")
    raw = loader._load_yaml()
    assert isinstance(raw, dict)


def test_loader_reads_yaml(tmp_path):
    cfg = _write_yaml(tmp_path, """
heinzel:
  name: riker
  id: riker-01
""")
    loader = HeinzelLoader(config_path=cfg)
    raw = loader._load_yaml()
    assert raw["heinzel"]["name"] == "riker"


# =============================================================================
# Runner bauen
# =============================================================================


def test_build_runner_name(tmp_path):
    cfg = _write_yaml(tmp_path, """
heinzel:
  name: riker
""")
    loader = HeinzelLoader(config_path=cfg)
    loader._raw = loader._load_yaml()
    runner = loader._build_runner()
    assert runner._name == "riker"


def test_build_runner_default_name(tmp_path):
    cfg = _write_yaml(tmp_path, "")
    loader = HeinzelLoader(config_path=cfg)
    loader._raw = loader._load_yaml()
    runner = loader._build_runner()
    assert runner._name == "heinzel"


def test_build_runner_noop_provider_without_config(tmp_path):
    cfg = _write_yaml(tmp_path, "")
    loader = HeinzelLoader(config_path=cfg)
    loader._raw = loader._load_yaml()
    runner = loader._build_runner()
    assert isinstance(runner._provider, NoopProvider)


# =============================================================================
# AddOns registrieren
# =============================================================================


def test_register_addons_sqlite(tmp_path):
    cfg = _write_yaml(tmp_path, """
addons:
  database:
    backend: sqlite
    path: ":memory:"
""")
    loader = HeinzelLoader(config_path=cfg)
    loader._raw = loader._load_yaml()
    runner = loader._build_runner()
    loader._register_addons(runner)
    names = [a.name for a in runner._addons]
    assert "database" in names


def test_register_addons_order(tmp_path):
    """database muss vor dialog_logger registriert sein."""
    cfg = _write_yaml(tmp_path, """
addons:
  dialog_logger:
    log_dir: /tmp/test-logs
  database:
    backend: sqlite
""")
    loader = HeinzelLoader(config_path=cfg)
    loader._raw = loader._load_yaml()
    runner = loader._build_runner()
    loader._register_addons(runner)
    names = [a.name for a in runner._addons]
    assert names.index("database") < names.index("dialog_logger")


def test_register_unknown_addon_skipped(tmp_path):
    cfg = _write_yaml(tmp_path, """
addons:
  nicht_existent:
    foo: bar
""")
    loader = HeinzelLoader(config_path=cfg)
    loader._raw = loader._load_yaml()
    runner = loader._build_runner()
    loader._register_addons(runner)  # darf nicht crashen
    assert len(runner._addons) == 0


def test_register_custom_factory(tmp_path):
    """Eigene Factory kann Default überschreiben."""
    cfg = _write_yaml(tmp_path, """
addons:
  database: {}
""")
    loader = HeinzelLoader(config_path=cfg)
    loader._raw = loader._load_yaml()

    mock_addon = MagicMock()
    mock_addon.name = "database"
    loader.register_addon_factory("database", lambda cfg, raw: mock_addon)

    runner = loader._build_runner()
    loader._register_addons(runner)
    assert runner._addons[0] is mock_addon


# =============================================================================
# build() — Vollständiger Durchlauf
# =============================================================================


@pytest.mark.asyncio
async def test_build_returns_connected_runner(tmp_path):
    cfg = _write_yaml(tmp_path, """
heinzel:
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
    cfg = _write_yaml(tmp_path, "heinzel:\n  name: minimal\n")
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
    names = [a.name for a in runner._addons]
    assert "web_search" in names
    await runner.disconnect()


# =============================================================================
# NoopProvider
# =============================================================================


@pytest.mark.asyncio
async def test_noop_provider_returns_empty():
    provider = NoopProvider()
    result = await provider.chat([])
    assert result == ""


@pytest.mark.asyncio
async def test_noop_provider_stream_empty():
    provider = NoopProvider()
    chunks = [c async for c in provider.stream([])]
    assert chunks == []
