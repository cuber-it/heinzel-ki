"""Noop-Implementierungen fuer Session-Management.

NoopSessionManager und NoopWorkingMemory sind die Defaults in BaseHeinzel.
Sie halten alles im RAM - kein Persist, nach Restart weg.
Fuer produktiven Einsatz werden diese in HNZ-003 durch persistente
Implementierungen ersetzt.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .exceptions import SessionNotFoundError
from .models.base import Message
from .session import (
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
        """Forget Gate: nichts vergessen — alle Turns unveraendert zurueck."""
        return turns

    async def store(self, turn: Turn, context: Any) -> bool:
        """Input Gate: immer speichern."""
        return True

    async def retrieve(self, context: Any, capacity: int) -> list[Turn]:
        """Output Gate: nicht verwendet in NoopWorkingMemory (direkt auf _turns)."""
        return []


# =============================================================================
# NoopWorkingMemory
# =============================================================================


class NoopWorkingMemory(WorkingMemory):
    """In-memory Working Memory ohne Persist.

    Haelt die letzten `capacity` Turns im RAM.
    Konvertiert Turns in user/assistant Message-Paare fuer den LLM.
    """

    def __init__(
        self,
        capacity: int = 10,
        gate: MemoryGateInterface | None = None,
    ) -> None:
        self._capacity = capacity
        self._gate = gate or NoopMemoryGate()
        self._turns: list[Turn] = []

    @property
    def capacity(self) -> int:
        return self._capacity

    async def add_turn(self, turn: Turn) -> None:
        """Turn aufnehmen wenn Gate es erlaubt, dann auf capacity trimmen."""
        if await self._gate.store(turn, context=None):
            self._turns.append(turn)
            if len(self._turns) > self._capacity:
                self._turns = self._turns[-self._capacity:]

    async def get_recent_turns(self, n: int) -> list[Turn]:
        """Letzte n Turns zurueckgeben."""
        return self._turns[-n:] if n < len(self._turns) else list(self._turns)

    async def get_context_messages(
        self, max_tokens: int | None = None
    ) -> tuple[Message, ...]:
        """Turns als user/assistant Message-Paare aufbereiten.

        Neueste zuerst einsammeln, aelteste fallen raus wenn Budget erschoepft.
        Ergebnis wird chronologisch (aelteste zuerst) zurueckgegeben.
        Token-Schaetzung: 1 Zeichen ~ 0.25 Tokens (grob).
        """
        messages: list[Message] = []
        tokens_used = 0

        for turn in reversed(self._turns):
            user_msg = Message(role="user", content=turn.raw_input)
            assistant_msg = Message(role="assistant", content=turn.final_response)
            turn_tokens = int((len(turn.raw_input) + len(turn.final_response)) / 4)

            if max_tokens is not None and tokens_used + turn_tokens > max_tokens:
                break

            messages.insert(0, assistant_msg)
            messages.insert(0, user_msg)
            tokens_used += turn_tokens

        return tuple(messages)

    async def clear(self) -> None:
        """Working Memory leeren."""
        self._turns = []


# =============================================================================
# NoopSessionManager
# =============================================================================


class NoopSessionManager(SessionManager):
    """In-memory Session-Verwaltung ohne Persist.

    Haelt Sessions und Turns im RAM. Nach Restart weg.
    Default in BaseHeinzel.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}
        self._turns: dict[str, list[Turn]] = {}
        self._active: Session | None = None

    @property
    def active_session(self) -> Session | None:
        return self._active

    async def create_session(
        self, heinzel_id: str, user_id: str | None = None
    ) -> Session:
        """Neue Session anlegen und als aktiv setzen."""
        session = Session(heinzel_id=heinzel_id, user_id=user_id)
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
                f"Session nicht gefunden", session_id=session_id
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
        """Turn zur Session hinzufuegen, turn_count und last_active_at aktualisieren."""
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
        self, heinzel_id: str, limit: int = 20
    ) -> list[Session]:
        """Alle Sessions eines Heinzel, neueste zuerst."""
        sessions = [
            s for s in self._sessions.values()
            if s.heinzel_id == heinzel_id
        ]
        sessions.sort(key=lambda s: s.started_at, reverse=True)
        return sessions[:limit]

    async def get_working_memory(self, session_id: str) -> WorkingMemory:
        """Immer neue NoopWorkingMemory — kein Persist."""
        return NoopWorkingMemory()
