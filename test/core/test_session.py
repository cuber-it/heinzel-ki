"""Tests fuer Session-Management (HNZ-002-0017).

Abdeckung:
- Session + Turn Datenmodelle
- NoopMemoryGate
- NoopWorkingMemory: capacity, add_turn, get_recent_turns, get_context_messages
- NoopWorkingMemory: estimated_tokens, compact
- NoopSessionManager: create_session, resume_session, end_session, add_turn
- NoopSessionManager: get_working_memory, list_sessions
- Integration in BaseHeinzel: ON_MEMORY_QUERY befuellt ctx, ON_STORED speichert Turn
"""

from __future__ import annotations

import pytest
from typing import AsyncGenerator
from unittest.mock import AsyncMock

from core.base import BaseHeinzel, LLMProvider
from core.exceptions import SessionNotFoundError
from core.models import PipelineContext, HookPoint
from core.session import Session, SessionStatus, Turn
from core.session_noop import NoopMemoryGate, NoopSessionManager, NoopWorkingMemory


# =============================================================================
# Hilfsfunktionen
# =============================================================================


def make_turn(session_id: str = "s1", raw_input: str = "hi", response: str = "hello") -> Turn:
    return Turn(session_id=session_id, raw_input=raw_input, final_response=response)


class MockProvider(LLMProvider):
    def __init__(self, response: str = "antwort") -> None:
        self.response = response

    async def chat(self, messages, system_prompt="", model="") -> str:
        return self.response

    async def stream(self, messages, system_prompt="", model="") -> AsyncGenerator[str, None]:
        yield self.response


def make_heinzel(response: str = "antwort") -> BaseHeinzel:
    return BaseHeinzel(provider=MockProvider(response), name="test")


# =============================================================================
# Session + Turn Datenmodelle
# =============================================================================


class TestSessionModell:
    def test_session_hat_defaults(self):
        s = Session(heinzel_id="h1")
        assert s.status == SessionStatus.active
        assert s.turn_count == 0
        assert s.id  # uuid gesetzt

    def test_session_ist_frozen(self):
        s = Session(heinzel_id="h1")
        with pytest.raises(Exception):
            s.turn_count = 5  # type: ignore

    def test_turn_hat_defaults(self):
        t = make_turn()
        assert t.id
        assert t.raw_input == "hi"
        assert t.final_response == "hello"

    def test_turn_ist_frozen(self):
        t = make_turn()
        with pytest.raises(Exception):
            t.raw_input = "changed"  # type: ignore


# =============================================================================
# NoopMemoryGate
# =============================================================================


class TestNoopMemoryGate:
    @pytest.mark.asyncio
    async def test_forget_gibt_alle_turns_zurueck(self):
        gate = NoopMemoryGate()
        turns = [make_turn(), make_turn()]
        result = await gate.forget(turns, context=None)
        assert result == turns

    @pytest.mark.asyncio
    async def test_store_gibt_immer_true(self):
        gate = NoopMemoryGate()
        assert await gate.store(make_turn(), context=None) is True

    def test_name_ist_noop(self):
        assert NoopMemoryGate().name == "noop"


# =============================================================================
# NoopWorkingMemory
# =============================================================================


class TestNoopWorkingMemory:
    @pytest.mark.asyncio
    async def test_max_turns_begrenzt_turns(self):
        # max_turns=3 als Sicherheitsnetz
        wm = NoopWorkingMemory(max_turns=3)
        for i in range(5):
            await wm.add_turn(make_turn(raw_input=f"msg{i}", response=f"resp{i}"))
        turns = await wm.get_recent_turns(10)
        assert len(turns) == 3
        assert turns[-1].raw_input == "msg4"

    @pytest.mark.asyncio
    async def test_max_tokens_trimmt_aelteste(self):
        # max_tokens so klein dass nach 3 Turns der erste rausfliegt
        # Turn hat ca. 10+10=20 Zeichen = 5 Token
        wm = NoopWorkingMemory(max_tokens=12)  # Platz fuer ~2 Turns
        for i in range(4):
            await wm.add_turn(make_turn(raw_input="hallo", response="welt!"))
        turns = await wm.get_recent_turns(10)
        # estimated_tokens <= 12 muss eingehalten sein (mind. 1 Turn bleibt)
        assert wm.estimated_tokens() <= 12
        assert len(turns) >= 1

    @pytest.mark.asyncio
    async def test_get_recent_turns_limit(self):
        wm = NoopWorkingMemory()
        for i in range(5):
            await wm.add_turn(make_turn(raw_input=f"m{i}"))
        turns = await wm.get_recent_turns(2)
        assert len(turns) == 2
        assert turns[-1].raw_input == "m4"

    @pytest.mark.asyncio
    async def test_get_context_messages_reihenfolge(self):
        wm = NoopWorkingMemory()
        await wm.add_turn(make_turn(raw_input="erste", response="eins"))
        await wm.add_turn(make_turn(raw_input="zweite", response="zwei"))
        msgs = await wm.get_context_messages()
        assert len(msgs) == 4  # 2x user + 2x assistant
        assert msgs[0].role == "user"
        assert msgs[0].content == "erste"
        assert msgs[-1].role == "assistant"
        assert msgs[-1].content == "zwei"

    @pytest.mark.asyncio
    async def test_get_context_messages_respektiert_max_tokens(self):
        wm = NoopWorkingMemory()
        # Grosser Turn der das Budget sprengt
        await wm.add_turn(make_turn(raw_input="a" * 400, response="b" * 400))
        await wm.add_turn(make_turn(raw_input="kurz", response="ok"))
        # max_tokens=10 — nur letzter kurzer Turn passt rein
        msgs = await wm.get_context_messages(max_tokens=10)
        assert len(msgs) == 2
        assert msgs[0].content == "kurz"

    @pytest.mark.asyncio
    async def test_clear_leert_working_memory(self):
        wm = NoopWorkingMemory()
        await wm.add_turn(make_turn())
        await wm.clear()
        turns = await wm.get_recent_turns(10)
        assert turns == []

    def test_estimated_tokens_leer(self):
        wm = NoopWorkingMemory()
        assert wm.estimated_tokens() == 0

    @pytest.mark.asyncio
    async def test_estimated_tokens_nach_add(self):
        wm = NoopWorkingMemory()
        await wm.add_turn(make_turn(raw_input="a" * 40, response="b" * 40))
        # (40+40) / 4 = 20
        assert wm.estimated_tokens() == 20

    @pytest.mark.asyncio
    async def test_compact_halbiert_turns(self):
        wm = NoopWorkingMemory(max_turns=100)  # gross genug, kein token-trim
        for i in range(6):
            await wm.add_turn(make_turn(raw_input=f"m{i}"))
        await wm.compact(keep_ratio=0.5)
        turns = await wm.get_recent_turns(10)
        assert len(turns) == 3
        assert turns[0].raw_input == "m3"

    @pytest.mark.asyncio
    async def test_compact_leer_kein_fehler(self):
        wm = NoopWorkingMemory()
        await wm.compact()  # kein Exception


# =============================================================================
# NoopSessionManager
# =============================================================================


class TestNoopSessionManager:
    @pytest.mark.asyncio
    async def test_create_session_setzt_active(self):
        sm = NoopSessionManager()
        session = await sm.create_session("h1")
        assert sm.active_session is not None
        assert sm.active_session.id == session.id

    @pytest.mark.asyncio
    async def test_create_session_mit_expliziter_id(self):
        sm = NoopSessionManager()
        session = await sm.create_session("h1", session_id="meine-id")
        assert session.id == "meine-id"

    @pytest.mark.asyncio
    async def test_resume_session_nicht_gefunden(self):
        sm = NoopSessionManager()
        with pytest.raises(SessionNotFoundError):
            await sm.resume_session("gibts-nicht")

    @pytest.mark.asyncio
    async def test_end_session_setzt_status(self):
        sm = NoopSessionManager()
        session = await sm.create_session("h1")
        await sm.end_session(session.id)
        assert sm.active_session is None
        updated = await sm.get_session(session.id)
        assert updated.status == "ended"

    @pytest.mark.asyncio
    async def test_add_turn_erhoeht_turn_count(self):
        sm = NoopSessionManager()
        session = await sm.create_session("h1")
        await sm.add_turn(session.id, make_turn(session_id=session.id))
        await sm.add_turn(session.id, make_turn(session_id=session.id))
        updated = await sm.get_session(session.id)
        assert updated.turn_count == 2

    @pytest.mark.asyncio
    async def test_get_turns_limit(self):
        sm = NoopSessionManager()
        session = await sm.create_session("h1")
        for i in range(5):
            await sm.add_turn(session.id, make_turn(session_id=session.id, raw_input=f"m{i}"))
        turns = await sm.get_turns(session.id, limit=3)
        assert len(turns) == 3

    @pytest.mark.asyncio
    async def test_get_working_memory_gibt_instanz(self):
        sm = NoopSessionManager()
        session = await sm.create_session("h1")
        wm = await sm.get_working_memory(session.id)
        assert isinstance(wm, NoopWorkingMemory)

    @pytest.mark.asyncio
    async def test_list_sessions_neueste_zuerst(self):
        sm = NoopSessionManager()
        s1 = await sm.create_session("h1")
        s2 = await sm.create_session("h1")
        sessions = await sm.list_sessions("h1")
        assert sessions[0].id == s2.id

    @pytest.mark.asyncio
    async def test_resume_session_gibt_leere_history(self):
        """Noop-SessionManager hat kein Persist — resume gibt leeres Working Memory."""
        sm = NoopSessionManager()
        session = await sm.create_session("h1")
        # Turns hinzufuegen
        await sm.add_turn(session.id, make_turn(session_id=session.id))
        # Neuen Manager — simuliert Restart (kein Persist)
        sm2 = NoopSessionManager()
        sm2._sessions[session.id] = session  # Session manuell eintragen
        sm2._turns[session.id] = []
        await sm2.resume_session(session.id)
        wm = await sm2.get_working_memory(session.id)
        turns = await wm.get_recent_turns(10)
        assert turns == []  # kein Persist, Working Memory leer


# =============================================================================
# Integration BaseHeinzel
# =============================================================================


class TestBaseHeinzelSessionIntegration:
    @pytest.mark.asyncio
    async def test_working_memory_befuellt_ctx_nach_zweitem_turn(self):
        """Nach dem ersten Turn ist Working Memory leer.
        Nach dem zweiten Turn muss die History im ctx.messages stehen."""
        heinzel = make_heinzel("antwort")
        await heinzel.connect()
        # Erster Turn
        await heinzel.chat("erste frage")
        # Zweiter Turn — jetzt muss Working Memory befuellt sein
        _, ctx = await heinzel._run_pipeline("zweite frage", None)
        # ctx.working_memory_turns muss > 0 sein
        assert ctx.working_memory_turns >= 1

    @pytest.mark.asyncio
    async def test_on_stored_speichert_turn_im_session_manager(self):
        heinzel = make_heinzel("antwort")
        await heinzel.connect()
        await heinzel.chat("test nachricht")
        session = heinzel.session_manager.active_session
        assert session is not None
        assert session.turn_count == 1

    @pytest.mark.asyncio
    async def test_session_id_konsistent_ueber_mehrere_turns(self):
        heinzel = make_heinzel()
        await heinzel.connect()
        _, ctx1 = await heinzel._run_pipeline("turn1", None)
        _, ctx2 = await heinzel._run_pipeline("turn2", None)
        assert ctx1.session_id == ctx2.session_id

    @pytest.mark.asyncio
    async def test_memory_tokens_used_in_ctx(self):
        heinzel = make_heinzel()
        await heinzel.connect()
        await heinzel.chat("erster turn")
        _, ctx = await heinzel._run_pipeline("zweiter turn", None)
        assert ctx.memory_tokens_used >= 0
