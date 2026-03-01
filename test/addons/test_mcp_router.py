"""Tests fuer addons.mcp_router — ToolAddress, KnownTool, ToolCall, ToolResult,
MCPToolsRouter (ABC), NoopMCPToolsRouter, on_tool_request Hook.

Story: HNZ-002-0004
"""

import pytest

from addons.mcp_router import (
    KnownTool,
    MCPToolsRouter,
    NoopMCPToolsRouter,
    ToolAddress,
    ToolCall,
    ToolResult,
)
from core.models import PipelineContext
from core.models.base import (
    ToolCall as PipelineToolCall,
    ToolResult as PipelineToolResult,
)


# =============================================================================
# ToolAddress
# =============================================================================


class TestToolAddress:
    def test_parse_valid(self):
        addr = ToolAddress.parse("thebrain:shell-tools:cd")
        assert addr.target == "thebrain"
        assert addr.server == "shell-tools"
        assert addr.tool == "cd"

    def test_parse_ip_target(self):
        addr = ToolAddress.parse("192.168.1.5:shell-tools:file_read")
        assert addr.target == "192.168.1.5"

    def test_str_roundtrip(self):
        addr = ToolAddress.parse("thebrain:shell-tools:cd")
        assert str(addr) == "thebrain:shell-tools:cd"

    def test_parse_too_few_segments(self):
        with pytest.raises(ValueError):
            ToolAddress.parse("shell-tools:cd")

    def test_parse_too_many_segments(self):
        with pytest.raises(ValueError):
            ToolAddress.parse("a:b:c:d")

    def test_parse_empty_segment(self):
        with pytest.raises(ValueError):
            ToolAddress.parse(":shell-tools:cd")

    def test_parse_empty_string(self):
        with pytest.raises(ValueError):
            ToolAddress.parse("")

    def test_immutable(self):
        addr = ToolAddress.parse("thebrain:shell-tools:cd")
        with pytest.raises(Exception):
            addr.target = "other"  # type: ignore


# =============================================================================
# KnownTool
# =============================================================================


class TestKnownTool:
    def test_minimal(self):
        addr = ToolAddress.parse("thebrain:shell-tools:cd")
        tool = KnownTool(address=addr, endpoint_url="http://thebrain:8001")
        assert tool.description == ""
        assert tool.input_schema == {}

    def test_full(self):
        addr = ToolAddress.parse("cirrus:shell-tools:file_read")
        schema = {"type": "object", "properties": {"path": {"type": "string"}}}
        tool = KnownTool(
            address=addr,
            endpoint_url="http://cirrus:8001",
            description="Liest eine Datei",
            input_schema=schema,
        )
        assert tool.description == "Liest eine Datei"
        assert tool.input_schema["type"] == "object"

    def test_immutable(self):
        addr = ToolAddress.parse("thebrain:shell-tools:cd")
        tool = KnownTool(address=addr, endpoint_url="http://x")
        with pytest.raises(Exception):
            tool.endpoint_url = "http://y"  # type: ignore


# =============================================================================
# ToolCall / ToolResult
# =============================================================================


class TestToolCall:
    def test_minimal(self):
        tc = ToolCall(address="thebrain:shell-tools:cd")
        assert tc.args == {}
        assert tc.context == {}

    def test_parsed_address(self):
        tc = ToolCall(address="thebrain:shell-tools:cd", args={"path": "/tmp"})
        addr = tc.parsed_address()
        assert addr.target == "thebrain"
        assert addr.tool == "cd"

    def test_parsed_address_invalid(self):
        tc = ToolCall(address="kaputt")
        with pytest.raises(ValueError):
            tc.parsed_address()


class TestToolResult:
    def test_success(self):
        r = ToolResult(address="thebrain:shell-tools:cd", result="/tmp")
        assert r.error is None
        assert r.unknown is False

    def test_error(self):
        r = ToolResult(address="thebrain:shell-tools:cd", error="timeout")
        assert r.result is None
        assert r.unknown is False

    def test_unknown(self):
        r = ToolResult(address="thebrain:shell-tools:cd", unknown=True)
        assert r.result is None
        assert r.error is None
        assert r.unknown is True


# =============================================================================
# NoopMCPToolsRouter — Registry
# =============================================================================


class TestNoopRegistry:
    def setup_method(self):
        self.router = NoopMCPToolsRouter()

    def test_list_tools_empty(self):
        assert self.router.list_tools() == []

    def test_find_tool_none(self):
        assert self.router.find_tool("thebrain:shell-tools:cd") is None

    def test_register_and_find(self):
        addr = ToolAddress.parse("thebrain:shell-tools:cd")
        tool = KnownTool(address=addr, endpoint_url="http://thebrain:8001")
        self.router.register(tool)
        found = self.router.find_tool("thebrain:shell-tools:cd")
        assert found is not None
        assert found.endpoint_url == "http://thebrain:8001"

    def test_register_multiple(self):
        for name in ["cd", "file_read", "file_write"]:
            addr = ToolAddress.parse(f"thebrain:shell-tools:{name}")
            self.router.register(KnownTool(address=addr, endpoint_url="http://x"))
        assert len(self.router.list_tools()) == 3

    def test_unregister(self):
        addr = ToolAddress.parse("thebrain:shell-tools:cd")
        self.router.register(KnownTool(address=addr, endpoint_url="http://x"))
        self.router.unregister("thebrain:shell-tools:cd")
        assert self.router.find_tool("thebrain:shell-tools:cd") is None

    def test_unregister_unknown_no_error(self):
        self.router.unregister("gibts:nicht:egal")  # kein Fehler erwartet


# =============================================================================
# NoopMCPToolsRouter — call() / chain()
# =============================================================================


class TestNoopCall:
    def setup_method(self):
        self.router = NoopMCPToolsRouter()

    @pytest.mark.asyncio
    async def test_call_unknown_tool(self):
        result = await self.router.call("thebrain:shell-tools:cd", {"path": "/tmp"})
        assert result.unknown is True
        assert result.result is None
        assert result.error is None

    @pytest.mark.asyncio
    async def test_call_known_tool_returns_noop_error(self):
        """Noop._execute() wird aufgerufen wenn Tool bekannt und ALWAYS_ALLOW."""
        from addons.mcp_router import ApprovalPolicy
        addr = ToolAddress.parse("thebrain:shell-tools:cd")
        self.router.register(KnownTool(address=addr, endpoint_url="http://x"))
        self.router.set_approval("thebrain", "shell-tools", ApprovalPolicy.ALWAYS_ALLOW, "cd")
        result = await self.router.call("thebrain:shell-tools:cd", {})
        assert result.error == "MCP not configured"
        assert result.unknown is False

    @pytest.mark.asyncio
    async def test_chain_empty(self):
        results = await self.router.chain([])
        assert results == []

    @pytest.mark.asyncio
    async def test_chain_all_unknown(self):
        calls = [
            ToolCall(address="thebrain:shell-tools:cd"),
            ToolCall(address="cirrus:shell-tools:file_read"),
        ]
        results = await self.router.chain(calls)
        assert len(results) == 2
        assert all(r.unknown is True for r in results)

    @pytest.mark.asyncio
    async def test_chain_context_propagation(self):
        """prev_result fliesst in naechsten Call als merged_args."""
        received_args: list[dict] = []

        class SpyRouter(MCPToolsRouter):
            name = "spy"

            async def _execute(self, tool, args):
                received_args.append(dict(args))
                return ToolResult(address=str(tool.address), result="output")

        from addons.mcp_router import ApprovalPolicy
        router = SpyRouter()
        addr1 = ToolAddress.parse("thebrain:shell-tools:cd")
        addr2 = ToolAddress.parse("thebrain:shell-tools:file_read")
        router.register(KnownTool(address=addr1, endpoint_url="http://x"))
        router.register(KnownTool(address=addr2, endpoint_url="http://x"))
        router.set_approval("thebrain", "shell-tools", ApprovalPolicy.ALWAYS_ALLOW)

        calls = [
            ToolCall(address="thebrain:shell-tools:cd", args={"path": "/tmp"}),
            ToolCall(address="thebrain:shell-tools:file_read"),
        ]
        await router.chain(calls)

        # Zweiter Call hat prev_result aus erstem
        assert "prev_result" in received_args[1]
        assert received_args[1]["prev_result"] == "output"

    def test_get_approval_default_ask_always(self):
        from addons.mcp_router import ApprovalPolicy
        policy = self.router.get_approval("thebrain", "shell-tools", "cd")
        assert policy == ApprovalPolicy.ASK_ALWAYS


# =============================================================================
# on_tool_request Hook — Integration mit PipelineContext
# =============================================================================


class TestOnToolRequestHook:
    def setup_method(self):
        self.router = NoopMCPToolsRouter()

    def _ctx(self, requests=(), results=()):
        return PipelineContext(
            tool_requests=requests,
            tool_results=results,
        )

    @pytest.mark.asyncio
    async def test_no_requests_passthrough(self):
        ctx = self._ctx()
        result = await self.router.on_tool_request(ctx)
        assert result.modified_ctx is ctx  # unveraendert

    @pytest.mark.asyncio
    async def test_unknown_tool_goes_to_metadata(self):
        ctx = self._ctx(requests=(
            PipelineToolCall(call_id="1", tool_name="thebrain:shell-tools:cd", args={}),
        ))
        result = await self.router.on_tool_request(ctx)
        new_ctx = result.modified_ctx
        unknown = new_ctx.metadata.get("unknown_tool_requests", [])
        assert "thebrain:shell-tools:cd" in unknown

    @pytest.mark.asyncio
    async def test_known_tool_noop_error_in_results(self):
        from addons.mcp_router import ApprovalPolicy
        addr = ToolAddress.parse("thebrain:shell-tools:cd")
        self.router.register(KnownTool(address=addr, endpoint_url="http://x"))
        self.router.set_approval("thebrain", "shell-tools", ApprovalPolicy.ALWAYS_ALLOW, "cd")
        ctx = self._ctx(requests=(
            PipelineToolCall(call_id="1", tool_name="thebrain:shell-tools:cd", args={}),
        ))
        result = await self.router.on_tool_request(ctx)
        new_ctx = result.modified_ctx
        assert len(new_ctx.tool_results) == 1
        assert new_ctx.tool_results[0].error == "MCP not configured"

    @pytest.mark.asyncio
    async def test_mixed_known_and_unknown(self):
        from addons.mcp_router import ApprovalPolicy
        addr = ToolAddress.parse("thebrain:shell-tools:cd")
        self.router.register(KnownTool(address=addr, endpoint_url="http://x"))
        self.router.set_approval("thebrain", "shell-tools", ApprovalPolicy.ALWAYS_ALLOW, "cd")
        ctx = self._ctx(requests=(
            PipelineToolCall(call_id="1", tool_name="thebrain:shell-tools:cd", args={}),
            PipelineToolCall(call_id="2", tool_name="cirrus:shell-tools:file_read", args={}),
        ))
        result = await self.router.on_tool_request(ctx)
        new_ctx = result.modified_ctx
        # Bekannt -> tool_results, Unbekannt -> metadata
        assert len(new_ctx.tool_results) == 1
        unknown = new_ctx.metadata.get("unknown_tool_requests", [])
        assert "cirrus:shell-tools:file_read" in unknown

    @pytest.mark.asyncio
    async def test_existing_results_preserved(self):
        """Bereits vorhandene tool_results werden nicht ueberschrieben."""
        existing = PipelineToolResult(call_id="0", result="already here")
        ctx = self._ctx(
            requests=(PipelineToolCall(call_id="1", tool_name="x:y:z", args={}),),
            results=(existing,),
        )
        result = await self.router.on_tool_request(ctx)
        new_ctx = result.modified_ctx
        assert any(r.call_id == "0" for r in new_ctx.tool_results)


# =============================================================================
# MCPToolsRouter ist ABC
# =============================================================================


class TestMCPToolsRouterABC:
    def test_cannot_instantiate_directly(self):
        with pytest.raises(TypeError):
            MCPToolsRouter()  # type: ignore

    def test_noop_is_subclass(self):
        assert issubclass(NoopMCPToolsRouter, MCPToolsRouter)
        assert isinstance(NoopMCPToolsRouter(), MCPToolsRouter)

    def test_partial_implementation_fails(self):
        class PartialRouter(MCPToolsRouter):
            name = "partial"
            # _execute fehlt

        with pytest.raises(TypeError):
            PartialRouter()


# =============================================================================
# ApprovalPolicy + ServerEntry
# =============================================================================


class TestApprovalPolicy:
    def test_all_four_values(self):
        from addons.mcp_router import ApprovalPolicy
        assert ApprovalPolicy.ALWAYS_ALLOW
        assert ApprovalPolicy.ALWAYS_DENY
        assert ApprovalPolicy.ASK_ONCE
        assert ApprovalPolicy.ASK_ALWAYS

    def test_is_string_enum(self):
        from addons.mcp_router import ApprovalPolicy
        assert isinstance(ApprovalPolicy.ALWAYS_ALLOW, str)


class TestServerEntry:
    def test_get_policy_tool_specific(self):
        from addons.mcp_router import ApprovalPolicy, ServerEntry
        entry = ServerEntry(
            target="thebrain", server="shell-tools", endpoint_url="http://x",
            approval={"cd": ApprovalPolicy.ALWAYS_ALLOW, "_default": ApprovalPolicy.ASK_ALWAYS}
        )
        assert entry.get_policy("cd") == ApprovalPolicy.ALWAYS_ALLOW

    def test_get_policy_falls_back_to_default(self):
        from addons.mcp_router import ApprovalPolicy, ServerEntry
        entry = ServerEntry(
            target="thebrain", server="shell-tools", endpoint_url="http://x",
            approval={"_default": ApprovalPolicy.ALWAYS_DENY}
        )
        assert entry.get_policy("unknown_tool") == ApprovalPolicy.ALWAYS_DENY

    def test_get_policy_falls_back_to_ask_always(self):
        from addons.mcp_router import ApprovalPolicy, ServerEntry
        entry = ServerEntry(target="thebrain", server="shell-tools", endpoint_url="http://x")
        assert entry.get_policy("anything") == ApprovalPolicy.ASK_ALWAYS

    def test_set_policy_tool(self):
        from addons.mcp_router import ApprovalPolicy, ServerEntry
        entry = ServerEntry(target="thebrain", server="shell-tools", endpoint_url="http://x")
        entry.set_policy(ApprovalPolicy.ALWAYS_ALLOW, "cd")
        assert entry.get_policy("cd") == ApprovalPolicy.ALWAYS_ALLOW

    def test_set_policy_server_default(self):
        from addons.mcp_router import ApprovalPolicy, ServerEntry
        entry = ServerEntry(target="thebrain", server="shell-tools", endpoint_url="http://x")
        entry.set_policy(ApprovalPolicy.ALWAYS_DENY)
        assert entry.get_policy("anything") == ApprovalPolicy.ALWAYS_DENY

    def test_key(self):
        from addons.mcp_router import ServerEntry
        entry = ServerEntry(target="thebrain", server="shell-tools", endpoint_url="http://x")
        assert entry.key == "thebrain:shell-tools"


# =============================================================================
# Router — Server-Registry + Approval-Interface
# =============================================================================


class TestRouterApprovalInterface:
    def setup_method(self):
        self.router = NoopMCPToolsRouter()

    def test_register_server(self):
        from addons.mcp_router import ApprovalPolicy, ServerEntry
        entry = ServerEntry(
            target="thebrain", server="shell-tools", endpoint_url="http://x",
            approval={"_default": ApprovalPolicy.ALWAYS_ALLOW}
        )
        self.router.register_server(entry)
        assert self.router.get_server_entry("thebrain", "shell-tools") is not None

    def test_list_servers_empty(self):
        assert self.router.list_servers() == []

    def test_list_servers(self):
        from addons.mcp_router import ServerEntry
        self.router.register_server(ServerEntry(target="a", server="s", endpoint_url="http://x"))
        self.router.register_server(ServerEntry(target="b", server="s", endpoint_url="http://y"))
        assert len(self.router.list_servers()) == 2

    def test_get_approval_no_server_returns_ask_always(self):
        from addons.mcp_router import ApprovalPolicy
        policy = self.router.get_approval("thebrain", "shell-tools", "cd")
        assert policy == ApprovalPolicy.ASK_ALWAYS

    def test_set_approval_creates_server_entry(self):
        from addons.mcp_router import ApprovalPolicy
        self.router.set_approval("thebrain", "shell-tools", ApprovalPolicy.ALWAYS_ALLOW, "cd")
        policy = self.router.get_approval("thebrain", "shell-tools", "cd")
        assert policy == ApprovalPolicy.ALWAYS_ALLOW

    def test_set_approval_server_default(self):
        from addons.mcp_router import ApprovalPolicy
        self.router.set_approval("thebrain", "shell-tools", ApprovalPolicy.ALWAYS_DENY)
        policy = self.router.get_approval("thebrain", "shell-tools", "anything")
        assert policy == ApprovalPolicy.ALWAYS_DENY

    def test_set_approval_tool_overrides_default(self):
        from addons.mcp_router import ApprovalPolicy
        self.router.set_approval("thebrain", "shell-tools", ApprovalPolicy.ALWAYS_DENY)
        self.router.set_approval("thebrain", "shell-tools", ApprovalPolicy.ALWAYS_ALLOW, "cd")
        assert self.router.get_approval("thebrain", "shell-tools", "cd") == ApprovalPolicy.ALWAYS_ALLOW
        assert self.router.get_approval("thebrain", "shell-tools", "rm") == ApprovalPolicy.ALWAYS_DENY


# =============================================================================
# Router — Approval-Flow in call()
# =============================================================================


class TestApprovalFlow:
    def setup_method(self):
        self.router = NoopMCPToolsRouter()
        addr = ToolAddress.parse("thebrain:shell-tools:cd")
        self.router.register(KnownTool(address=addr, endpoint_url="http://x"))

    @pytest.mark.asyncio
    async def test_always_allow_executes(self):
        from addons.mcp_router import ApprovalPolicy
        self.router.set_approval("thebrain", "shell-tools", ApprovalPolicy.ALWAYS_ALLOW, "cd")
        result = await self.router.call("thebrain:shell-tools:cd", {})
        # Noop._execute -> error='MCP not configured' aber KEIN unknown, KEIN pending
        assert result.error == "MCP not configured"
        assert result.unknown is False

    @pytest.mark.asyncio
    async def test_always_deny_rejects(self):
        from addons.mcp_router import ApprovalPolicy
        self.router.set_approval("thebrain", "shell-tools", ApprovalPolicy.ALWAYS_DENY, "cd")
        result = await self.router.call("thebrain:shell-tools:cd", {})
        assert result.error == "abgelehnt"

    @pytest.mark.asyncio
    async def test_ask_always_returns_pending(self):
        from addons.mcp_router import ApprovalPolicy
        self.router.set_approval("thebrain", "shell-tools", ApprovalPolicy.ASK_ALWAYS, "cd")
        result = await self.router.call("thebrain:shell-tools:cd", {})
        assert result.error == "approval_pending"

    @pytest.mark.asyncio
    async def test_ask_once_first_call_pending(self):
        from addons.mcp_router import ApprovalPolicy
        self.router.set_approval("thebrain", "shell-tools", ApprovalPolicy.ASK_ONCE, "cd")
        result = await self.router.call("thebrain:shell-tools:cd", {})
        assert result.error == "approval_pending"

    @pytest.mark.asyncio
    async def test_ask_once_after_approval_executes(self):
        from addons.mcp_router import ApprovalPolicy
        self.router.set_approval("thebrain", "shell-tools", ApprovalPolicy.ASK_ONCE, "cd")
        self.router.record_ask_once_answer("thebrain:shell-tools:cd", True)
        result = await self.router.call("thebrain:shell-tools:cd", {})
        assert result.error == "MCP not configured"  # Noop ausgefuehrt
        assert result.unknown is False

    @pytest.mark.asyncio
    async def test_ask_once_after_denial_rejects(self):
        from addons.mcp_router import ApprovalPolicy
        self.router.set_approval("thebrain", "shell-tools", ApprovalPolicy.ASK_ONCE, "cd")
        self.router.record_ask_once_answer("thebrain:shell-tools:cd", False)
        result = await self.router.call("thebrain:shell-tools:cd", {})
        assert result.error == "abgelehnt"

    @pytest.mark.asyncio
    async def test_clear_ask_once_cache(self):
        from addons.mcp_router import ApprovalPolicy
        self.router.set_approval("thebrain", "shell-tools", ApprovalPolicy.ASK_ONCE, "cd")
        self.router.record_ask_once_answer("thebrain:shell-tools:cd", True)
        self.router.clear_ask_once_cache()
        result = await self.router.call("thebrain:shell-tools:cd", {})
        assert result.error == "approval_pending"  # wieder pending nach cache-clear

    @pytest.mark.asyncio
    async def test_no_server_entry_defaults_to_ask_always(self):
        """Kein ServerEntry -> ASK_ALWAYS -> pending."""
        result = await self.router.call("thebrain:shell-tools:cd", {})
        assert result.error == "approval_pending"


# =============================================================================
# on_tool_request — approval_pending in metadata
# =============================================================================


class TestApprovalInHook:
    def setup_method(self):
        self.router = NoopMCPToolsRouter()
        addr = ToolAddress.parse("thebrain:shell-tools:cd")
        self.router.register(KnownTool(address=addr, endpoint_url="http://x"))

    @pytest.mark.asyncio
    async def test_pending_goes_to_metadata(self):
        from addons.mcp_router import ApprovalPolicy
        self.router.set_approval("thebrain", "shell-tools", ApprovalPolicy.ASK_ALWAYS, "cd")
        ctx = PipelineContext(tool_requests=(
            PipelineToolCall(call_id="1", tool_name="thebrain:shell-tools:cd", args={}),
        ))
        result = await self.router.on_tool_request(ctx)
        pending = result.modified_ctx.metadata.get("approval_pending", [])
        assert "thebrain:shell-tools:cd" in pending
        assert len(result.modified_ctx.tool_results) == 0

    @pytest.mark.asyncio
    async def test_allowed_tool_executes(self):
        from addons.mcp_router import ApprovalPolicy
        self.router.set_approval("thebrain", "shell-tools", ApprovalPolicy.ALWAYS_ALLOW, "cd")
        ctx = PipelineContext(tool_requests=(
            PipelineToolCall(call_id="1", tool_name="thebrain:shell-tools:cd", args={}),
        ))
        result = await self.router.on_tool_request(ctx)
        assert len(result.modified_ctx.tool_results) == 1
        assert result.modified_ctx.tool_results[0].error == "MCP not configured"
