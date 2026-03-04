"""Noop-Implementierungen fuer Session-Management.

NoopSessionManager und NoopWorkingMemory sind die Defaults im Runner.
Sie halten alles im RAM - kein Persist, nach Restart weg.
Fuer produktiven Einsatz werden diese in HNZ-003 durch persistente
Implementierungen ersetzt.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .compaction import CompactionRegistry, RollingSessionRegistry
from .exceptions import SessionNotFoundError
from .models.base import Message, MessageType
from .models.placeholders import HandoverContext, ResourceBudget
from .session import (
    _uuid,
    MemoryGateInterface,
    Session,
    SessionManager,
    Turn,
    WorkingMemory,
)


# =============================================================================
# NoopMemoryGate
# =============================================================================


class NoopMemoryGate(MemoryGateInterface):
    """Triviales Gate: laesst alles durch, vergisst nichts.

    Default in NoopWorkingMemory. Platzhalter fuer HNZ-00x.
    """

    @property
    def name(self) -> str:
        return "noop"

    async def forget(self, turns: list[Turn], context: Any) -> list[Turn]:
        """Forget Gate: nichts vergessen — alle Turns unveraendert."""
        return turns

    async def store(self, turn: Turn, context: Any) -> bool:
        """Input Gate: immer speichern."""
        return True

    async def retrieve(self, context: Any, capacity: int) -> list[Turn]:
        """Output Gate: nicht verwendet (NoopWorkingMemory greift direkt)."""
        return []


# =============================================================================
# NoopWorkingMemory
# =============================================================================


class NoopWorkingMemory(WorkingMemory):
    """In-memory Working Memory ohne Persist.

    Token-basiertes Budget: nach jedem add_turn() werden die aeltesten
    Turns entfernt bis estimated_tokens() < max_tokens.
    max_turns ist ein Sicherheitsnetz fuer sehr kurze Turns.

    Defaults:
        max_tokens = 128_000  (passt zu den meisten modernen Modellen)
        max_turns  = 10_000   (praktisch unbegrenzt)
    """

    def __init__(
        self,
        max_tokens: int = 128_000,
        max_turns: int = 10_000,
        gate: MemoryGateInterface | None = None,
    ) -> None:
        self._max_tokens = max_tokens
        self._max_turns = max_turns
        self._gate = gate or NoopMemoryGate()
        self._turns: list[Turn] = []

    @property
    def max_tokens(self) -> int:
        return self._max_tokens

    @property
    def max_turns(self) -> int:
        return self._max_turns

    @property
    def compaction_strategy(self):
        """Aktive CompactionStrategy aus CompactionRegistry."""
        return CompactionRegistry.get_default()

    async def add_turn(self, turn: Turn) -> None:
        """Turn aufnehmen wenn Gate es erlaubt, dann token-basiert trimmen."""
        if not await self._gate.store(turn, context=None):
            return
        self._turns.append(turn)
        while len(self._turns) > self._max_turns:
            self._turns.pop(0)
        while (
            len(self._turns) > 1
            and self.estimated_tokens() > self._max_tokens
        ):
            self._turns.pop(0)

    async def get_recent_turns(self, n: int) -> list[Turn]:
        """Letzte n Turns zurueckgeben."""
        return (
            self._turns[-n:] if n < len(self._turns) else list(self._turns)
        )

    async def get_context_messages(
        self, max_tokens: int | None = None
    ) -> tuple[Message, ...]:
        """Turns als user/assistant Message-Paare aufbereiten.

        Neueste zuerst einsammeln, aelteste fallen raus wenn Budget erschoepft.
        Ergebnis chronologisch (aelteste zuerst).
        Token-Schaetzung: 1 Zeichen ~ 0.25 Tokens (grob).
        """
        messages: list[Message] = []
        tokens_used = 0

        for turn in reversed(self._turns):
            user_msg = Message(
                role="user",
                content=turn.raw_input,
                message_type=MessageType.MEMORY,
            )
            assistant_msg = Message(
                role="assistant",
                content=turn.final_response,
                message_type=MessageType.MEMORY,
            )
            turn_tokens = (
                len(turn.raw_input) + len(turn.final_response)
            ) // 4

            budget_hit = max_tokens is not None and (
                tokens_used + turn_tokens > max_tokens
            )
            if budget_hit:
                break

            messages.insert(0, assistant_msg)
            messages.insert(0, user_msg)
            tokens_used += turn_tokens

        return tuple(messages)

    async def clear(self) -> None:
        """Working Memory leeren."""
        self._turns = []

    def estimated_tokens(self) -> int:
        """Grobe Token-Schaetzung aller gespeicherten Turns (len/4)."""
        return sum(
            len(t.raw_input) + len(t.final_response)
            for t in self._turns
        ) // 4

    async def compact(self, keep_ratio: float = 0.5) -> None:
        """Kompaktiert via CompactionStrategy (keep_ratio wird ignoriert).

        Nutzt die aktive Strategie aus CompactionRegistry.
        """
        if not self._turns:
            return
        budget = ResourceBudget(max_tokens=self._max_tokens)
        result = await self.compaction_strategy.compact(self._turns, budget)
        self._turns = list(result.kept_turns)


# =============================================================================
# NoopSessionManager
# =============================================================================


class NoopSessionManager(SessionManager):
    """In-memory Session-Verwaltung ohne Persist.

    Haelt Sessions und Turns im RAM. Nach Restart weg.
    Default im Runner.
    """

    def __init__(
        self,
        max_tokens: int = 128_000,
        max_turns: int = 10_000,
    ) -> None:
        self._max_tokens = max_tokens
        self._max_turns = max_turns
        self._sessions: dict[str, Session] = {}
        self._turns: dict[str, list[Turn]] = {}
        self._working_memories: dict[str, NoopWorkingMemory] = {}
        self._active: Session | None = None

    @property
    def active_session(self) -> Session | None:
        return self._active

    async def create_session(
        self,
        agent_id: str,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> Session:
        """Neue Session anlegen und als aktiv setzen."""
        session = Session(
            id=session_id or _uuid(),
            agent_id=agent_id,
            user_id=user_id,
        )
        self._sessions[session.id] = session
        self._turns[session.id] = []
        self._active = session
        return session

    async def get_session(self, session_id: str) -> Session | None:
        """Session per ID laden. None wenn nicht gefunden."""
        return self._sessions.get(session_id)

    async def resume_session(self, session_id: str) -> Session:
        """Vorhandene Session fortsetzen.

        Raises:
            SessionNotFoundError: wenn session_id unbekannt.
        """
        session = self._sessions.get(session_id)
        if session is None:
            raise SessionNotFoundError(
                "Session nicht gefunden", session_id=session_id
            )
        self._active = session
        return session

    async def end_session(self, session_id: str) -> None:
        """Session beenden: status=ended, active_session=None."""
        session = self._sessions.get(session_id)
        if session is None:
            return
        self._sessions[session_id] = session.model_copy(
            update={"status": "ended"}
        )
        if self._active and self._active.id == session_id:
            self._active = None

    async def add_turn(self, session_id: str, turn: Turn) -> None:
        """Turn zur Session hinzufuegen, Metadaten aktualisieren."""
        if session_id not in self._sessions:
            return
        self._turns.setdefault(session_id, []).append(turn)
        session = self._sessions[session_id]
        self._sessions[session_id] = session.model_copy(update={
            "turn_count": session.turn_count + 1,
            "last_active_at": datetime.now(timezone.utc),
        })
        if self._active and self._active.id == session_id:
            self._active = self._sessions[session_id]

    async def get_turns(self, session_id: str, limit: int = 10) -> list[Turn]:
        """Letzte limit Turns einer Session."""
        turns = self._turns.get(session_id, [])
        return turns[-limit:] if limit < len(turns) else list(turns)

    async def list_sessions(
        self, agent_id: str, limit: int = 20
    ) -> list[Session]:
        """Alle Sessions eines Heinzel, neueste zuerst."""
        sessions = [
            s for s in self._sessions.values()
            if s.agent_id == agent_id
        ]
        sessions.sort(key=lambda s: s.started_at, reverse=True)
        return sessions[:limit]

    async def get_working_memory(self, session_id: str) -> WorkingMemory:
        """Working Memory pro Session — gleiche Instanz bei jedem Aufruf."""
        if session_id not in self._working_memories:
            self._working_memories[session_id] = NoopWorkingMemory(
                max_tokens=self._max_tokens,
                max_turns=self._max_turns,
            )
        return self._working_memories[session_id]

    async def maybe_roll(
        self,
        budget: ResourceBudget,
    ) -> HandoverContext | None:
        """Prueft ob die aktive Session gerollt werden soll.

        Nutzt RollingSessionRegistry.get_default() als Policy.
        Gibt HandoverContext zurueck wenn gerollt, sonst None.
        Der Aufrufer ist verantwortlich fuer ON_SESSION_ROLL zu feuern.
        """
        session = self._active
        if session is None:
            return None

        policy = RollingSessionRegistry.get_default()
        if not policy.should_roll(session, budget):
            return None

        # Turns kompaktieren
        wm = await self.get_working_memory(session.id)
        n = len(self._turns.get(session.id, []))
        turns = await wm.get_recent_turns(n)
        compaction_result = await wm.compaction_strategy.compact(
            turns, budget
        )

        # HandoverContext erstellen
        handover = await policy.create_handover(session, compaction_result)

        # Alte Session beenden
        await self.end_session(session.id)

        # Neue Session mit Handover in Metadata
        new_session = await self.create_session(
            agent_id=session.agent_id,
            user_id=session.user_id,
        )
        self._sessions[new_session.id] = new_session.model_copy(
            update={"metadata": {"handover": handover}}
        )
        self._active = self._sessions[new_session.id]
        return handover
