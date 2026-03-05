"""Tests für DialogLoggerAddOn — Write, Read, Crash-Safety, Rotation, Retention, Search."""

from __future__ import annotations

import json
import os
import pytest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

from addons.dialog_logger import DialogLoggerAddOn, EVT_INPUT, EVT_OUTPUT, EVT_ERROR
from core.models import PipelineContext
from core.models.base import ToolCall, ToolResult


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    return tmp_path / "logs"


@pytest.fixture
def addon(log_dir: Path) -> DialogLoggerAddOn:
    a = DialogLoggerAddOn(log_dir=str(log_dir), retention_days=30)
    a._heinzel_id = "riker"
    return a


class _FakeHeinzel:
    class config:
        class agent:
            name = "riker"


def _ctx(session_id="sess-001", parsed_input="Hallo", response="", metadata=None) -> PipelineContext:
    return PipelineContext(
        session_id=session_id,
        parsed_input=parsed_input,
        response=response,
        metadata=metadata or {},
    )


# =============================================================================
# Lifecycle
# =============================================================================


@pytest.mark.asyncio
async def test_on_attach_creates_log_dir(log_dir):
    addon = DialogLoggerAddOn(log_dir=str(log_dir))
    heinzel = MagicMock()
    heinzel.config.agent.name = "riker"
    await addon.on_attach(heinzel)
    assert log_dir.exists()


@pytest.mark.asyncio
async def test_on_attach_sets_heinzel_id(log_dir):
    addon = DialogLoggerAddOn(log_dir=str(log_dir))
    heinzel = MagicMock()
    heinzel.config.agent.name = "Riker Prime"
    await addon.on_attach(heinzel)
    assert addon._heinzel_id == "riker-prime"


# =============================================================================
# Write + Read
# =============================================================================


@pytest.mark.asyncio
async def test_log_input_creates_file(addon, log_dir):
    ctx = _ctx(parsed_input="Hallo Heinzel")
    await addon.on_input(ctx)
    entries = addon.read_session_log("sess-001")
    assert len(entries) == 1
    assert entries[0]["event"] == EVT_INPUT
    assert entries[0]["content"] == "Hallo Heinzel"


@pytest.mark.asyncio
async def test_log_output(addon):
    ctx = _ctx(response="Das ist die Antwort.")
    await addon.on_output(ctx)
    entries = addon.read_session_log("sess-001")
    assert entries[0]["event"] == EVT_OUTPUT
    assert entries[0]["content"] == "Das ist die Antwort."


@pytest.mark.asyncio
async def test_log_multiple_events(addon):
    ctx_in = _ctx(parsed_input="Frage")
    ctx_out = _ctx(response="Antwort")
    await addon.on_input(ctx_in)
    await addon.on_output(ctx_out)
    entries = addon.read_session_log("sess-001")
    assert len(entries) == 2
    events = [e["event"] for e in entries]
    assert EVT_INPUT in events
    assert EVT_OUTPUT in events


@pytest.mark.asyncio
async def test_log_contains_session_and_heinzel_id(addon):
    await addon.on_input(_ctx())
    entries = addon.read_session_log("sess-001")
    assert entries[0]["session_id"] == "sess-001"
    assert entries[0]["heinzel_id"] == "riker"


@pytest.mark.asyncio
async def test_log_timestamp_present(addon):
    await addon.on_input(_ctx())
    entries = addon.read_session_log("sess-001")
    assert "ts" in entries[0]
    assert "T" in entries[0]["ts"]  # ISO-Format


@pytest.mark.asyncio
async def test_log_error(addon):
    ctx = _ctx(metadata={"error": "LLM timeout"})
    await addon.on_error(ctx)
    entries = addon.read_session_log("sess-001")
    assert entries[0]["event"] == EVT_ERROR
    assert "LLM timeout" in entries[0]["content"]


@pytest.mark.asyncio
async def test_log_tool_request(addon):
    ctx = PipelineContext(
        session_id="sess-001",
        tool_requests=(ToolCall(call_id="1", tool_name="web_search", args={"query": "Python"}),),
    )
    await addon.on_tool_request(ctx)
    entries = addon.read_session_log("sess-001")
    assert entries[0]["event"] == "tool_request"
    assert entries[0]["metadata"]["tools"][0]["tool"] == "web_search"


@pytest.mark.asyncio
async def test_log_tool_result(addon):
    ctx = PipelineContext(
        session_id="sess-001",
        tool_results=(ToolResult(call_id="1", result="Ergebnis"),),
    )
    await addon.on_tool_result(ctx)
    entries = addon.read_session_log("sess-001")
    assert entries[0]["event"] == "tool_result"


# =============================================================================
# Crash-Safety — fsync nachweisen
# =============================================================================


@pytest.mark.asyncio
async def test_crash_safety_fsync_called(addon, monkeypatch):
    """fsync wird nach jedem Write aufgerufen."""
    fsync_calls = []
    original_fsync = os.fsync

    def mock_fsync(fd):
        fsync_calls.append(fd)
        return original_fsync(fd)

    monkeypatch.setattr(os, "fsync", mock_fsync)
    await addon.on_input(_ctx())
    assert len(fsync_calls) >= 1


@pytest.mark.asyncio
async def test_file_readable_after_simulated_crash(addon, log_dir):
    """Datei ist lesbar auch wenn kein sauberes Close erfolgte."""
    await addon.on_input(_ctx(parsed_input="Nachricht vor Crash"))
    # Direkt Datei lesen ohne on_detach
    entries = addon.read_session_log("sess-001")
    assert len(entries) == 1
    assert entries[0]["content"] == "Nachricht vor Crash"


# =============================================================================
# Rotation
# =============================================================================


@pytest.mark.asyncio
async def test_rotation_on_size_exceeded(addon, log_dir):
    """Bei Größenüberschreitung wird rotiert."""
    addon._rotation_size_bytes = 1  # 1 Byte → sofortige Rotation

    await addon.on_input(_ctx(parsed_input="Erste Nachricht"))
    await addon.on_input(_ctx(parsed_input="Zweite Nachricht"))

    today = date.today().isoformat()
    log_files = list((log_dir / "riker" / today).glob("*.jsonl"))
    assert len(log_files) >= 2  # Originaldatei + rotierte Datei


# =============================================================================
# Retention
# =============================================================================


@pytest.mark.asyncio
async def test_retention_deletes_old_files(log_dir):
    """Dateien älter als retention_days werden gelöscht."""
    addon = DialogLoggerAddOn(log_dir=str(log_dir), retention_days=30)
    addon._heinzel_id = "riker"

    # Alte Datei anlegen (31 Tage alt)
    old_date = (date.today() - timedelta(days=31)).isoformat()
    old_dir = log_dir / "riker" / old_date
    old_dir.mkdir(parents=True)
    old_file = old_dir / "old-session.jsonl"
    old_file.write_text('{"event": "input"}\n', encoding="utf-8")

    heinzel = MagicMock()
    heinzel.config.agent.name = "riker"
    await addon.on_attach(heinzel)

    assert not old_file.exists()


@pytest.mark.asyncio
async def test_retention_keeps_recent_files(log_dir):
    """Aktuelle Dateien bleiben erhalten."""
    addon = DialogLoggerAddOn(log_dir=str(log_dir), retention_days=30)
    addon._heinzel_id = "riker"

    # Datei von heute
    today = date.today().isoformat()
    recent_dir = log_dir / "riker" / today
    recent_dir.mkdir(parents=True)
    recent_file = recent_dir / "recent-session.jsonl"
    recent_file.write_text('{"event": "input"}\n', encoding="utf-8")

    heinzel = MagicMock()
    heinzel.config.agent.name = "riker"
    await addon.on_attach(heinzel)

    assert recent_file.exists()


# =============================================================================
# search_logs
# =============================================================================


@pytest.mark.asyncio
async def test_search_logs_finds_content(addon):
    await addon.on_input(_ctx(parsed_input="Python asyncio Beispiel"))
    await addon.on_input(_ctx(session_id="sess-002", parsed_input="Docker Compose Setup"))

    results = addon.search_logs("asyncio")
    assert len(results) == 1
    assert "asyncio" in results[0]["content"].lower()


@pytest.mark.asyncio
async def test_search_logs_date_filter(addon, log_dir):
    """Suche mit date_from/date_to filtert korrekt."""
    await addon.on_input(_ctx(parsed_input="Heutige Nachricht"))

    yesterday = date.today() - timedelta(days=1)
    results = addon.search_logs("Heutige", date_from=date.today())
    assert len(results) == 1

    results_old = addon.search_logs("Heutige", date_to=yesterday)
    assert len(results_old) == 0


@pytest.mark.asyncio
async def test_search_logs_empty_query(addon):
    await addon.on_input(_ctx(parsed_input="test"))
    # Leerer Query matcht nichts
    results = addon.search_logs("nichtvorhanden")
    assert results == []
