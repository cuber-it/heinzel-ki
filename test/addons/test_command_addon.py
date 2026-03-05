"""Tests für CommandAddOn — Parsing, Dispatch, Registry, !help, Fehlerbehandlung."""

from __future__ import annotations

import pytest
from addons.command import CommandAddOn, CommandRegistry, CommandContext, CommandResult
from addons.command.addon import _parse, _dispatch
from core.models import PipelineContext


# =============================================================================
# _parse
# =============================================================================


def test_parse_simple():
    ctx = _parse("!history", "s1", None)
    assert ctx.command == "history"
    assert ctx.args == []


def test_parse_with_args():
    ctx = _parse("!history 10", "s1", None)
    assert ctx.command == "history"
    assert ctx.args == ["10"]


def test_parse_quoted_arg():
    ctx = _parse('!search "Python asyncio"', "s1", None)
    assert ctx.command == "search"
    assert ctx.args == ["Python asyncio"]


def test_parse_multiple_args():
    ctx = _parse("!set key value", "s1", None)
    assert ctx.command == "set"
    assert ctx.args == ["key", "value"]


def test_parse_uppercase_normalized():
    ctx = _parse("!HELP", "s1", None)
    assert ctx.command == "help"


def test_parse_raw_preserved():
    ctx = _parse("!history 10", "s1", None)
    assert ctx.raw == "!history 10"


# =============================================================================
# CommandRegistry
# =============================================================================


def test_registry_register_decorator():
    reg = CommandRegistry()

    @reg.register("test", description="Test-Command")
    async def handler(ctx): return CommandResult(message="ok")

    assert "test" in reg
    assert reg.get("test").description == "Test-Command"


def test_registry_add():
    reg = CommandRegistry()
    async def handler(ctx): return CommandResult()
    reg.add("foo", handler, description="foo cmd")
    assert "foo" in reg


def test_registry_case_insensitive():
    reg = CommandRegistry()
    async def handler(ctx): return CommandResult()
    reg.add("MyCmd", handler)
    assert "mycmd" in reg
    assert reg.get("MYCMD") is not None


def test_registry_list_commands():
    reg = CommandRegistry()
    async def h(ctx): return CommandResult()
    reg.add("b_cmd", h, description="B")
    reg.add("a_cmd", h, description="A")
    names = [c["name"] for c in reg.list_commands()]
    assert names == sorted(names)  # alphabetisch


# =============================================================================
# _dispatch
# =============================================================================


@pytest.mark.asyncio
async def test_dispatch_known_command():
    reg = CommandRegistry()
    async def handler(ctx): return CommandResult(message="ergebnis")
    reg.add("foo", handler)

    ctx = CommandContext(command="foo", args=[], raw="!foo", session_id="s1")
    result = await _dispatch(ctx, reg)
    assert result.success is True
    assert result.message == "ergebnis"


@pytest.mark.asyncio
async def test_dispatch_unknown_command():
    reg = CommandRegistry()
    ctx = CommandContext(command="nope", args=[], raw="!nope", session_id="s1")
    result = await _dispatch(ctx, reg)
    assert result.success is False
    assert "nope" in result.message


@pytest.mark.asyncio
async def test_dispatch_handler_exception():
    reg = CommandRegistry()
    async def broken(ctx): raise ValueError("kaputt")
    reg.add("broken", broken)

    ctx = CommandContext(command="broken", args=[], raw="!broken", session_id="s1")
    result = await _dispatch(ctx, reg)
    assert result.success is False
    assert "kaputt" in result.message


# =============================================================================
# CommandAddOn — on_input_parsed
# =============================================================================


@pytest.fixture
def addon():
    return CommandAddOn()


@pytest.mark.asyncio
async def test_non_command_passthrough(addon):
    ctx = PipelineContext(session_id="s1", parsed_input="Hallo Heinzel")
    result = await addon.on_input_parsed(ctx)
    assert result.halt is False
    assert result.modified_ctx is ctx


@pytest.mark.asyncio
async def test_command_sets_halt(addon):
    ctx = PipelineContext(session_id="s1", parsed_input="!help")
    result = await addon.on_input_parsed(ctx)
    assert result.halt is True


@pytest.mark.asyncio
async def test_command_sets_response(addon):
    ctx = PipelineContext(session_id="s1", parsed_input="!help")
    result = await addon.on_input_parsed(ctx)
    assert result.modified_ctx.response != ""


@pytest.mark.asyncio
async def test_unknown_command_error_in_response(addon):
    ctx = PipelineContext(session_id="s1", parsed_input="!nichtexistent")
    result = await addon.on_input_parsed(ctx)
    assert result.halt is True
    assert "nichtexistent" in result.modified_ctx.response.lower() or \
           "fehler" in result.modified_ctx.response.lower() or \
           "unbekannt" in result.modified_ctx.response.lower()


# =============================================================================
# !help builtin
# =============================================================================


@pytest.mark.asyncio
async def test_help_lists_commands(addon):
    async def dummy(ctx): return CommandResult(message="ok")
    addon.registry.add("mytest", dummy, description="Mein Test")

    ctx = PipelineContext(session_id="s1", parsed_input="!help")
    result = await addon.on_input_parsed(ctx)
    assert "mytest" in result.modified_ctx.response


# =============================================================================
# Custom Command via Registry
# =============================================================================


@pytest.mark.asyncio
async def test_custom_command_with_args():
    addon = CommandAddOn()

    async def cmd_echo(ctx: CommandContext) -> CommandResult:
        return CommandResult(message=" ".join(ctx.args))

    addon.registry.add("echo", cmd_echo)
    ctx = PipelineContext(session_id="s1", parsed_input="!echo hallo welt")
    result = await addon.on_input_parsed(ctx)
    assert "hallo welt" in result.modified_ctx.response
