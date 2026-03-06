"""Tests für BuiltinCommandsAddOn — !history, !fact, !status, !addons, !sessions, !provider, !quit."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from addons.command import CommandAddOn, BuiltinCommandsAddOn, CommandContext
from addons.command.builtins import _runner, _redo
from core.models import PipelineContext
from core.session import Turn, Session, SessionStatus
from core.provider import NoopProvider
from core.runner import Runner


# =============================================================================
# Fixtures
# =============================================================================


def _make_turn(raw="Hallo", response="Hi"):
    from datetime import datetime, timezone
    return Turn(
        session_id="s1",
        raw_input=raw,
        final_response=response,
        timestamp=datetime.now(timezone.utc),
    )


def _make_session(sid="sess-001", turns=3):
    from datetime import datetime, timezone
    return Session(
        id=sid,
        agent_id="riker",
        turn_count=turns,
        started_at=datetime.now(timezone.utc),
        last_active_at=datetime.now(timezone.utc),
    )


@pytest.fixture
async def runner_with_commands():
    """Runner mit CommandAddOn + BuiltinCommandsAddOn."""
    r = Runner(provider=NoopProvider(), name="riker")

    cmd = CommandAddOn()
    builtins = BuiltinCommandsAddOn()

    from core.models import HookPoint
    r.register_addon(cmd, hooks={HookPoint.ON_INPUT_PARSED})
    r._addons.append(builtins)

    await r.connect()

    # BuiltinCommandsAddOn manuell attachen
    await builtins.on_attach(r)

    yield r, cmd
    await r.disconnect()


def _ctx(runner, cmd, input_text):
    """Simuliert CommandContext wie CommandAddOn ihn erzeugt."""
    from addons.command.addon import _parse
    return _parse(input_text, "s1", runner)


# =============================================================================
# _runner Hilfsfunktion
# =============================================================================


def test_runner_from_ctx_none():
    ctx = CommandContext(command="x", args=[], raw="!x", heinzel=None)
    assert _runner(ctx) is None


def test_runner_from_ctx_direct():
    mock = MagicMock()
    mock.runner = mock
    ctx = CommandContext(command="x", args=[], raw="!x", heinzel=mock)
    assert _runner(ctx) is mock


# =============================================================================
# !status
# =============================================================================


@pytest.mark.asyncio
async def test_status_no_runner():
    builtins = BuiltinCommandsAddOn()
    cmd = CommandAddOn()
    await builtins.on_attach(MagicMock(addons=MagicMock(get=lambda n: cmd if n == "command" else None)))

    ctx = PipelineContext(session_id="s1", parsed_input="!status")
    result = await cmd.on_input_parsed(ctx)
    # Kein Runner → Fehlermeldung
    assert result.halt is True


@pytest.mark.asyncio
async def test_status_with_runner(runner_with_commands):
    r, cmd = runner_with_commands
    ctx_cmd = _ctx(r, cmd, "!status")
    entry = cmd.registry.get("status")
    result = await entry.handler(ctx_cmd)
    assert "riker" in result.message or "Provider" in result.message


# =============================================================================
# !addons
# =============================================================================


@pytest.mark.asyncio
async def test_addons_lists_loaded(runner_with_commands):
    r, cmd = runner_with_commands
    ctx_cmd = _ctx(r, cmd, "!addons")
    entry = cmd.registry.get("addons")
    result = await entry.handler(ctx_cmd)
    assert result.success is True
    assert "command" in result.message


# =============================================================================
# !history
# =============================================================================


@pytest.mark.asyncio
async def test_history_no_session(runner_with_commands):
    r, cmd = runner_with_commands
    ctx_cmd = _ctx(r, cmd, "!history")
    entry = cmd.registry.get("history")
    result = await entry.handler(ctx_cmd)
    # NoopSessionManager hat keine Session
    assert result.message  # irgendeine Antwort


@pytest.mark.asyncio
async def test_history_with_mocked_session(runner_with_commands):
    r, cmd = runner_with_commands
    session = _make_session()
    turns = [_make_turn("Frage 1", "Antwort 1"), _make_turn("Frage 2", "Antwort 2")]

    r._session_manager = MagicMock()
    r._session_manager.active_session = session
    r._session_manager.get_turns = AsyncMock(return_value=turns)
    r._session_manager.get_working_memory = AsyncMock(return_value=MagicMock(estimated_tokens=1000))

    ctx_cmd = _ctx(r, cmd, "!history 2")
    entry = cmd.registry.get("history")
    result = await entry.handler(ctx_cmd)
    assert "Frage 1" in result.message
    assert "Frage 2" in result.message


# =============================================================================
# !sessions
# =============================================================================


@pytest.mark.asyncio
async def test_sessions_with_mock(runner_with_commands):
    r, cmd = runner_with_commands
    sessions = [_make_session("aaa", 5), _make_session("bbb", 2)]
    r._session_manager = MagicMock()
    r._session_manager.active_session = sessions[0]
    r._session_manager.list_sessions = AsyncMock(return_value=sessions)

    ctx_cmd = _ctx(r, cmd, "!sessions")
    entry = cmd.registry.get("sessions")
    result = await entry.handler(ctx_cmd)
    assert "aaa" in result.message or "5" in result.message


# =============================================================================
# !new / !end
# =============================================================================


@pytest.mark.asyncio
async def test_new_session(runner_with_commands):
    r, cmd = runner_with_commands
    new_session = _make_session("new-sess")
    r._session_manager = MagicMock()
    r._session_manager.create_session = AsyncMock(return_value=new_session)

    ctx_cmd = _ctx(r, cmd, "!new")
    entry = cmd.registry.get("new")
    result = await entry.handler(ctx_cmd)
    assert result.success is True
    assert "new-sess"[:8] in result.message


@pytest.mark.asyncio
async def test_end_session(runner_with_commands):
    r, cmd = runner_with_commands
    session = _make_session()
    r._session_manager = MagicMock()
    r._session_manager.active_session = session
    r._session_manager.end_session = AsyncMock()

    ctx_cmd = _ctx(r, cmd, "!end")
    entry = cmd.registry.get("end")
    result = await entry.handler(ctx_cmd)
    assert result.success is True
    r._session_manager.end_session.assert_called_once_with(session.id)


# =============================================================================
# !fact
# =============================================================================


@pytest.mark.asyncio
async def test_fact_set_get(runner_with_commands):
    r, cmd = runner_with_commands
    r._addons.append(MagicMock(name="database"))  # kein DB
    r.addons  # trigger property

    ctx_set = _ctx(r, cmd, "!fact set sprache deutsch")
    entry = cmd.registry.get("fact")
    result = await entry.handler(ctx_set)
    assert result.success is True

    ctx_get = _ctx(r, cmd, "!fact get sprache")
    result = await entry.handler(ctx_get)
    assert "deutsch" in result.message


@pytest.mark.asyncio
async def test_fact_list(runner_with_commands):
    r, cmd = runner_with_commands
    entry = cmd.registry.get("fact")
    ctx = _ctx(r, cmd, "!fact set city Hamburg")
    await entry.handler(ctx)
    ctx_list = _ctx(r, cmd, "!fact list")
    result = await entry.handler(ctx_list)
    assert "Hamburg" in result.message


@pytest.mark.asyncio
async def test_fact_delete(runner_with_commands):
    r, cmd = runner_with_commands
    entry = cmd.registry.get("fact")
    await entry.handler(_ctx(r, cmd, "!fact set x 42"))
    result = await entry.handler(_ctx(r, cmd, "!fact delete x"))
    assert result.success is True
    result = await entry.handler(_ctx(r, cmd, "!fact get x"))
    assert result.success is False


@pytest.mark.asyncio
async def test_fact_clear(runner_with_commands):
    r, cmd = runner_with_commands
    entry = cmd.registry.get("fact")
    await entry.handler(_ctx(r, cmd, "!fact set a 1"))
    await entry.handler(_ctx(r, cmd, "!fact set b 2"))
    result = await entry.handler(_ctx(r, cmd, "!fact clear"))
    assert result.success is True
    result = await entry.handler(_ctx(r, cmd, "!fact list"))
    assert "Keine" in result.message


@pytest.mark.asyncio
async def test_facts_alias(runner_with_commands):
    r, cmd = runner_with_commands
    entry = cmd.registry.get("facts")
    assert entry is not None  # Alias registriert


# =============================================================================
# !provider
# =============================================================================


@pytest.mark.asyncio
async def test_provider_status(runner_with_commands):
    r, cmd = runner_with_commands
    ctx_cmd = _ctx(r, cmd, "!provider")
    entry = cmd.registry.get("provider")
    result = await entry.handler(ctx_cmd)
    assert result.success is True
    assert "Provider" in result.message


# =============================================================================
# !model
# =============================================================================


@pytest.mark.asyncio
async def test_model_current(runner_with_commands):
    r, cmd = runner_with_commands
    ctx_cmd = _ctx(r, cmd, "!model")
    entry = cmd.registry.get("model")
    result = await entry.handler(ctx_cmd)
    assert result.success is True


# =============================================================================
# !quit / !exit
# =============================================================================


@pytest.mark.asyncio
async def test_quit_returns_quit_data(runner_with_commands):
    r, cmd = runner_with_commands
    r._session_manager = MagicMock()
    r._session_manager.active_session = None

    ctx_cmd = _ctx(r, cmd, "!quit")
    entry = cmd.registry.get("quit")
    result = await entry.handler(ctx_cmd)
    assert result.data == {"quit": True}


@pytest.mark.asyncio
async def test_exit_alias(runner_with_commands):
    r, cmd = runner_with_commands
    assert cmd.registry.get("exit") is not None


# =============================================================================
# via on_input_parsed — Endpunkt
# =============================================================================


@pytest.mark.asyncio
async def test_full_dispatch_status(runner_with_commands):
    r, cmd = runner_with_commands
    ctx = PipelineContext(session_id="s1", parsed_input="!status")
    result = await cmd.on_input_parsed(ctx)
    assert result.halt is True
    assert result.modified_ctx.response != ""
