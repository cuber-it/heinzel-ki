"""Tests für CommandAddOn II — Alias, Ketten, Makros, Persistenz."""

from __future__ import annotations

import pytest
from addons.command import CommandAddOnII, CommandContext, CommandResult
from addons.command.addon2 import AliasStore, MacroStore, _split_chain
from core.models import PipelineContext


# =============================================================================
# _split_chain
# =============================================================================


def test_split_chain_single():
    assert _split_chain("!help") == ["!help"]


def test_split_chain_two():
    assert _split_chain("!status && !history 5") == ["!status", "!history 5"]


def test_split_chain_three():
    parts = _split_chain("!a && !b && !c")
    assert parts == ["!a", "!b", "!c"]


# =============================================================================
# AliasStore
# =============================================================================


def test_alias_set_get():
    s = AliasStore()
    s.set("h", "history")
    assert s.get("h") == "history"


def test_alias_case_insensitive():
    s = AliasStore()
    s.set("H", "history")
    assert s.get("h") == "history"


def test_alias_remove():
    s = AliasStore()
    s.set("h", "history")
    assert s.remove("h") is True
    assert s.get("h") is None


def test_alias_remove_missing():
    assert AliasStore().remove("nope") is False


def test_alias_list():
    s = AliasStore()
    s.set("b", "bar")
    s.set("a", "foo")
    names = [e["name"] for e in s.list_all()]
    assert names == ["a", "b"]  # alphabetisch


# =============================================================================
# MacroStore
# =============================================================================


@pytest.mark.asyncio
async def test_macro_save_get():
    store = MacroStore()
    await store.save("morning", "!status && !history 5")
    body = await store.get("morning")
    assert body == "!status && !history 5"
    await store.close()


@pytest.mark.asyncio
async def test_macro_case_insensitive():
    store = MacroStore()
    await store.save("Morning", "!status")
    assert await store.get("morning") == "!status"
    await store.close()


@pytest.mark.asyncio
async def test_macro_delete():
    store = MacroStore()
    await store.save("x", "!foo")
    assert await store.delete("x") is True
    assert await store.get("x") is None
    await store.close()


@pytest.mark.asyncio
async def test_macro_delete_missing():
    store = MacroStore()
    assert await store.delete("nope") is False
    await store.close()


@pytest.mark.asyncio
async def test_macro_list():
    store = MacroStore()
    await store.save("b", "!bar")
    await store.save("a", "!foo")
    macros = await store.list_all()
    assert [m["name"] for m in macros] == ["a", "b"]
    await store.close()


@pytest.mark.asyncio
async def test_macro_upsert():
    store = MacroStore()
    await store.save("m", "!old")
    await store.save("m", "!new")
    assert await store.get("m") == "!new"
    await store.close()


# =============================================================================
# CommandAddOnII — Alias via !alias command
# =============================================================================


@pytest.fixture
def addon():
    return CommandAddOnII()


@pytest.mark.asyncio
async def test_alias_set_via_command(addon):
    ctx = PipelineContext(session_id="s1", parsed_input="!alias h history 10")
    result = await addon.on_input_parsed(ctx)
    assert "h" in result.modified_ctx.response or "gesetzt" in result.modified_ctx.response.lower()


@pytest.mark.asyncio
async def test_alias_expansion(addon):
    addon._aliases.set("h", "help")
    async def cmd_help(ctx): return CommandResult(message="Help-Output")
    addon.registry.add("help", cmd_help)

    ctx = PipelineContext(session_id="s1", parsed_input="!h")
    result = await addon.on_input_parsed(ctx)
    assert "Help-Output" in result.modified_ctx.response


@pytest.mark.asyncio
async def test_alias_list_command(addon):
    addon._aliases.set("h", "history")
    ctx = PipelineContext(session_id="s1", parsed_input="!alias list")
    result = await addon.on_input_parsed(ctx)
    assert "h" in result.modified_ctx.response


@pytest.mark.asyncio
async def test_alias_remove_command(addon):
    addon._aliases.set("h", "history")
    ctx = PipelineContext(session_id="s1", parsed_input="!alias remove h")
    result = await addon.on_input_parsed(ctx)
    assert addon._aliases.get("h") is None


# =============================================================================
# Ketten
# =============================================================================


@pytest.mark.asyncio
async def test_chain_two_commands(addon):
    async def cmd_a(ctx): return CommandResult(message="A")
    async def cmd_b(ctx): return CommandResult(message="B")
    addon.registry.add("a", cmd_a)
    addon.registry.add("b", cmd_b)

    ctx = PipelineContext(session_id="s1", parsed_input="!a && !b")
    result = await addon.on_input_parsed(ctx)
    resp = result.modified_ctx.response
    assert "A" in resp and "B" in resp


@pytest.mark.asyncio
async def test_chain_fail_fast(addon):
    async def cmd_ok(ctx): return CommandResult(message="OK")
    async def cmd_fail(ctx): return CommandResult(success=False, message="FAIL")
    async def cmd_never(ctx): return CommandResult(message="NEVER")
    addon.registry.add("ok", cmd_ok)
    addon.registry.add("fail", cmd_fail)
    addon.registry.add("never", cmd_never)

    ctx = PipelineContext(session_id="s1", parsed_input="!ok && !fail && !never")
    result = await addon.on_input_parsed(ctx)
    resp = result.modified_ctx.response
    assert "NEVER" not in resp


# =============================================================================
# Makros via !macro command
# =============================================================================


@pytest.mark.asyncio
async def test_macro_save_via_command(addon):
    async def cmd_help(ctx): return CommandResult(message="hilfe")
    addon.registry.add("help", cmd_help)

    ctx = PipelineContext(session_id="s1", parsed_input="!macro save morning !help")
    result = await addon.on_input_parsed(ctx)
    assert "morning" in result.modified_ctx.response.lower() or "gespeichert" in result.modified_ctx.response.lower()


@pytest.mark.asyncio
async def test_macro_execution(addon):
    async def cmd_status(ctx): return CommandResult(message="STATUS-OK")
    addon.registry.add("status", cmd_status)
    await addon._macros.save("morning", "!status")

    ctx = PipelineContext(session_id="s1", parsed_input="!morning")
    result = await addon.on_input_parsed(ctx)
    assert "STATUS-OK" in result.modified_ctx.response


@pytest.mark.asyncio
async def test_macro_list_command(addon):
    await addon._macros.save("mymacro", "!help")
    ctx = PipelineContext(session_id="s1", parsed_input="!macro list")
    result = await addon.on_input_parsed(ctx)
    assert "mymacro" in result.modified_ctx.response


@pytest.mark.asyncio
async def test_macro_delete_command(addon):
    await addon._macros.save("todel", "!help")
    ctx = PipelineContext(session_id="s1", parsed_input="!macro delete todel")
    result = await addon.on_input_parsed(ctx)
    assert await addon._macros.get("todel") is None


@pytest.mark.asyncio
async def test_macro_persistence(tmp_path):
    """Makros überleben Neustart."""
    db = str(tmp_path / "macros.db")
    addon1 = CommandAddOnII(db_path=db)
    await addon1._macros.save("mykey", "!help")
    await addon1._macros.close()

    addon2 = CommandAddOnII(db_path=db)
    body = await addon2._macros.get("mykey")
    assert body == "!help"
    await addon2._macros.close()
