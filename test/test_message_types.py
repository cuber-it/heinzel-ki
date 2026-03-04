"""Tests fuer MessageType-Klassifikation in ctx.messages.

Prueft dass Tool-Loop und DeepReasoningStrategy Messages mit dem
richtigen MessageType in ctx.messages schreiben, und dass die
Message-Anzahl nach mehreren Reasoning-Runden nicht exponentiell waechst.
"""

from __future__ import annotations

import pytest

from core import Runner, ReActStrategy
from core.addon import AddOn, AddOnResult
from core.models import HookPoint, PipelineContext, ContextHistory
from core.models.base import Message, MessageType, ToolCall, ToolResult
from core.reasoning import DeepReasoningStrategy


# ---------------------------------------------------------------------------
# Mock-Hilfsmittel (identisch zu test_react.py — bewusst lokal gehalten)
# ---------------------------------------------------------------------------


class SingleToolProvider:
    """Gibt einmalig einen Tool-Call zurueck, danach finale Antwort."""

    def __init__(self) -> None:
        self.call_count = 0

    async def chat(self, messages, system_prompt="", model="") -> str:
        return "finale Antwort"

    async def chat_tools(self, messages, system_prompt="", model="", tools=None):
        self.call_count += 1
        if self.call_count == 1:
            content_blocks = [{
                "type": "tool_use",
                "id": "call_abc",
                "name": "thebrain:shell-tools:echo",
                "input": {"text": "hallo"},
            }]
            return "", content_blocks
        return "fertig.", []

    async def stream(self, messages, system_prompt="", model=""):
        yield "chunk"

    context_window = 128_000


class EchoToolAddOn(AddOn):
    """Fuehrt Tool-Calls aus — gibt statisches Ergebnis zurueck."""
    name = "echo_tool"

    async def on_tool_request(
        self, ctx: PipelineContext, history: ContextHistory | None = None
    ) -> AddOnResult:
        results = [
            ToolResult(call_id=req.call_id, result="echo-result")
            for req in ctx.tool_requests
        ]
        return AddOnResult(modified_ctx=ctx.evolve(
            tool_results=tuple(results),
            tool_requests=(),
        ))


class SimpleProvider:
    """Gibt immer sofort eine Antwort — kein Tool-Call."""

    async def chat(self, messages, system_prompt="", model="") -> str:
        return "direkte Antwort"

    async def chat_tools(self, messages, system_prompt="", model="", tools=None):
        return "direkte Antwort", []

    async def stream(self, messages, system_prompt="", model=""):
        yield "direkte Antwort"

    context_window = 128_000


# ---------------------------------------------------------------------------
# Tests: MessageType im Tool-Loop
# ---------------------------------------------------------------------------


class TestToolMessageTypes:

    @pytest.mark.asyncio
    async def test_tool_use_message_hat_type_tool(self):
        """tool_use-Block wird als MessageType.TOOL in ctx.messages geschrieben."""
        provider = SingleToolProvider()
        runner = Runner(provider=provider, name="type-test")
        runner.set_strategy(ReActStrategy(tools=[{"name": "thebrain:shell-tools:echo"}]))
        addon = EchoToolAddOn()
        runner.register_addon(addon, hooks={HookPoint.ON_TOOL_REQUEST})
        await runner.connect()

        _, final_ctx = await runner._run_pipeline("test", None)

        tool_msgs = [m for m in final_ctx.messages if m.message_type == MessageType.TOOL]
        assert len(tool_msgs) >= 1, "Mindestens eine TOOL-Message erwartet"

    @pytest.mark.asyncio
    async def test_tool_use_message_role_ist_assistant(self):
        """tool_use-Block kommt als assistant-Message (Anthropic-Format)."""
        provider = SingleToolProvider()
        runner = Runner(provider=provider, name="role-test")
        runner.set_strategy(ReActStrategy(tools=[{"name": "thebrain:shell-tools:echo"}]))
        addon = EchoToolAddOn()
        runner.register_addon(addon, hooks={HookPoint.ON_TOOL_REQUEST})
        await runner.connect()

        _, final_ctx = await runner._run_pipeline("test", None)

        assistant_tool_msgs = [
            m for m in final_ctx.messages
            if m.message_type == MessageType.TOOL and m.role == "assistant"
        ]
        assert len(assistant_tool_msgs) >= 1

    @pytest.mark.asyncio
    async def test_tool_result_message_role_ist_user(self):
        """tool_result-Block kommt als user-Message (Anthropic-Format)."""
        provider = SingleToolProvider()
        runner = Runner(provider=provider, name="result-role-test")
        runner.set_strategy(ReActStrategy(tools=[{"name": "thebrain:shell-tools:echo"}]))
        addon = EchoToolAddOn()
        runner.register_addon(addon, hooks={HookPoint.ON_TOOL_REQUEST})
        await runner.connect()

        _, final_ctx = await runner._run_pipeline("test", None)

        user_tool_msgs = [
            m for m in final_ctx.messages
            if m.message_type == MessageType.TOOL and m.role == "user"
        ]
        assert len(user_tool_msgs) >= 1

    @pytest.mark.asyncio
    async def test_tool_content_ist_liste(self):
        """tool_use/tool_result-Messages haben content als list (Anthropic-Bloecke)."""
        provider = SingleToolProvider()
        runner = Runner(provider=provider, name="content-test")
        runner.set_strategy(ReActStrategy(tools=[{"name": "thebrain:shell-tools:echo"}]))
        addon = EchoToolAddOn()
        runner.register_addon(addon, hooks={HookPoint.ON_TOOL_REQUEST})
        await runner.connect()

        _, final_ctx = await runner._run_pipeline("test", None)

        for msg in final_ctx.messages:
            if msg.message_type == MessageType.TOOL:
                assert isinstance(msg.content, list), (
                    f"TOOL-Message content muss list sein, war: {type(msg.content)}"
                )

    @pytest.mark.asyncio
    async def test_memory_messages_haben_typ_memory(self):
        """Messages ohne expliziten Typ defaulten auf MEMORY."""
        msg = Message(role="user", content="hallo")
        assert msg.message_type == MessageType.MEMORY


# ---------------------------------------------------------------------------
# Tests: MessageType im DeepReasoning
# ---------------------------------------------------------------------------


class TestReasoningMessageTypes:

    @pytest.fixture
    def ctx(self) -> PipelineContext:
        return PipelineContext(user_input="Was ist 2+2?")

    @pytest.fixture
    def history(self, ctx) -> ContextHistory:
        h = ContextHistory()
        h.push(ctx)
        return h

    @pytest.mark.asyncio
    async def test_initialize_schreibt_reasoning_message(self, ctx, history):
        """initialize() schreibt erste Phase-Frage als REASONING in ctx.messages."""
        strategy = DeepReasoningStrategy()
        ctx_after = await strategy.initialize(ctx, history)

        reasoning_msgs = [
            m for m in ctx_after.messages if m.message_type == MessageType.REASONING
        ]
        assert len(reasoning_msgs) == 1
        assert reasoning_msgs[0].role == "user"

    @pytest.mark.asyncio
    async def test_reflect_schreibt_zwei_reasoning_messages(self, ctx, history):
        """reflect() haengt assistant-Antwort + naechste user-Frage an."""
        strategy = DeepReasoningStrategy()
        ctx_after = await strategy.initialize(ctx, history)
        ctx_after = ctx_after.evolve(response="Meine Analyse: ...")

        _, ctx_reflected = await strategy.reflect(ctx_after, history)

        reasoning_msgs = [
            m for m in ctx_reflected.messages if m.message_type == MessageType.REASONING
        ]
        # 1 aus initialize + 2 aus reflect (assistant-Antwort + naechste Frage)
        assert len(reasoning_msgs) == 3

    @pytest.mark.asyncio
    async def test_reasoning_messages_wachsen_nicht_exponentiell(self, ctx, history):
        """Nach N reflect()-Aufrufen: genau 1 + 2*N REASONING-Messages."""
        strategy = DeepReasoningStrategy(max_iterations=4)
        ctx_cur = await strategy.initialize(ctx, history)

        n_reflects = 3
        for _ in range(n_reflects):
            ctx_cur = ctx_cur.evolve(response="Antwort auf Phase.")
            _, ctx_cur = await strategy.reflect(ctx_cur, history)

        reasoning_msgs = [
            m for m in ctx_cur.messages if m.message_type == MessageType.REASONING
        ]
        expected = 1 + 2 * n_reflects  # 1 init + 2 pro reflect
        assert len(reasoning_msgs) == expected, (
            f"Erwartet {expected} REASONING-Messages, war: {len(reasoning_msgs)}"
        )

    @pytest.mark.asyncio
    async def test_keine_memory_messages_durch_reasoning(self, ctx, history):
        """initialize + reflect schreiben keine MEMORY-Messages."""
        strategy = DeepReasoningStrategy()
        ctx_cur = await strategy.initialize(ctx, history)
        ctx_cur = ctx_cur.evolve(response="Phase-Antwort.")
        _, ctx_cur = await strategy.reflect(ctx_cur, history)

        memory_msgs = [
            m for m in ctx_cur.messages if m.message_type == MessageType.MEMORY
        ]
        # ctx startet ohne messages → MEMORY-Count muss 0 bleiben
        assert len(memory_msgs) == 0
