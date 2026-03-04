"""Tests fuer ReActStrategy + Tool-Loop in der Pipeline."""

from __future__ import annotations

import pytest

from core import Runner, ReActStrategy
from core.addon import AddOn, AddOnResult
from core.models import HookPoint, PipelineContext, ContextHistory
from core.models.base import ToolCall, ToolResult


# ---------------------------------------------------------------------------
# Mock-Provider: gibt Tool-Call zurueck, dann finale Antwort
# ---------------------------------------------------------------------------

class ToolCallingMockProvider:
    """Simuliert ein LLM das zuerst ein Tool aufruft, dann antwortet."""

    def __init__(self) -> None:
        self.call_count = 0
        self.chat_calls: list[list[dict]] = []
        self.chat_tools_calls: list[tuple] = []

    async def chat(self, messages, system_prompt="", model="") -> str:
        self.call_count += 1
        self.chat_calls.append(messages)
        return "finale Antwort ohne Tool"

    async def chat_tools(self, messages, system_prompt="", model="", tools=None):
        self.call_count += 1
        self.chat_tools_calls.append((messages, tools))
        if self.call_count == 1:
            # Erster Call: Tool-Request
            content_blocks = [
                {"type": "text", "text": "Ich schaue nach..."},
                {
                    "type": "tool_use",
                    "id": "call_001",
                    "name": "thebrain:shell-tools:file_read",
                    "input": {"path": "/etc/hostname"},
                },
            ]
            return "", content_blocks
        else:
            # Zweiter Call nach Tool-Ergebnis: finale Antwort
            return "Der Hostname ist thebrain.", []

    async def stream(self, messages, system_prompt="", model=""):
        yield "stream chunk"

    context_window = 128_000


class ToolExecutorAddOn(AddOn):
    """Simuliert Tool-Ausfuehrung: gibt Hostname zurueck."""
    name = "tool_executor"

    def __init__(self) -> None:
        super().__init__()
        self.executed: list[str] = []

    async def on_tool_request(
        self, ctx: PipelineContext, history: ContextHistory | None = None
    ) -> AddOnResult:
        new_results = []
        for req in ctx.tool_requests:
            self.executed.append(req.tool_name)
            new_results.append(ToolResult(
                call_id=req.call_id,
                result="thebrain",
            ))
        new_ctx = ctx.evolve(
            tool_results=tuple(new_results),
            tool_requests=(),
        )
        return AddOnResult(modified_ctx=new_ctx)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestReActStrategy:

    def test_react_registered(self):
        from core import StrategyRegistry
        assert StrategyRegistry.get("react") is not None

    def test_react_description_shows_tools(self):
        s = ReActStrategy(tools=[{"name": "test_tool"}])
        assert "1 Tool" in s.description

    @pytest.mark.asyncio
    async def test_react_initialize_sets_tools(self):
        from core.models.context import PipelineContext, ContextHistory
        from core.models.types import HookPoint
        ctx = PipelineContext(raw_input="test", session_id="s", agent_id="a")
        history = ContextHistory()
        tools = [{"name": "my_tool", "description": "does stuff"}]
        strategy = ReActStrategy(tools=tools)
        new_ctx = await strategy.initialize(ctx, history)
        assert new_ctx.metadata.get("hnz_tools") == tools

    @pytest.mark.asyncio
    async def test_tool_loop_vollstaendig(self):
        """Kern-Test: LLM ruft Tool auf → wird ausgefuehrt → LLM antwortet."""
        provider = ToolCallingMockProvider()
        runner = Runner(
            provider=provider,
            name="react-test",
        )
        tools = [{"name": "thebrain:shell-tools:file_read", "description": "Liest Dateien"}]
        runner.set_strategy(ReActStrategy(tools=tools))

        executor = ToolExecutorAddOn()
        runner.register_addon(executor, hooks={HookPoint.ON_TOOL_REQUEST})
        await runner.connect()

        history, final_ctx = await runner._run_pipeline("Was ist der Hostname?", None)

        # Provider wurde zweimal aufgerufen: 1x Tool-Call, 1x finale Antwort
        assert provider.call_count == 2, f"Erwartet 2 Calls, got {provider.call_count}"

        # Tool wurde ausgefuehrt
        assert "thebrain:shell-tools:file_read" in executor.executed

        # Finale Antwort stimmt
        assert "thebrain" in (final_ctx.response or "")

    @pytest.mark.asyncio
    async def test_passthrough_kein_tool_loop(self):
        """PassthroughStrategy: keine Tools → kein Tool-Loop."""
        provider = ToolCallingMockProvider()
        runner = Runner(provider=provider, name="passthrough-test")
        # Passthrough: kein hnz_tools → chat() statt chat_tools()
        await runner.connect()

        _, final_ctx = await runner._run_pipeline("Hallo", None)

        assert provider.call_count == 1
        assert final_ctx.tool_requests == ()

    @pytest.mark.asyncio
    async def test_tool_result_in_message_history(self):
        """Tool-Result-Messages landen in hnz_tool_messages fuer naechsten LLM-Call."""
        provider = ToolCallingMockProvider()
        runner = Runner(provider=provider, name="history-test")
        tools = [{"name": "thebrain:shell-tools:file_read"}]
        runner.set_strategy(ReActStrategy(tools=tools))

        executor = ToolExecutorAddOn()
        runner.register_addon(executor, hooks={HookPoint.ON_TOOL_REQUEST})
        await runner.connect()

        history, final_ctx = await runner._run_pipeline("test", None)

        # Zweiter Call muss Tool-Messages in den Messages haben
        assert len(provider.chat_tools_calls) == 2
        second_messages = provider.chat_tools_calls[1][0]
        # tool_use + tool_result Blöcke müssen da sein
        has_tool_content = any(
            isinstance(m.get("content"), list)
            for m in second_messages
        )
        assert has_tool_content, "Zweiter LLM-Call muss Tool-Messages enthalten"


