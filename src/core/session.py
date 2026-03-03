"""Session-Management fuer das Heinzel-System.

Definiert die Datenmodelle und ABCs fuer:
- Session: Klammer um alle Turns einer Konversation
- Turn: Einzelner Eingabe/Ausgabe-Zyklus mit vollstaendiger History
- WorkingMemory: Kurzfristiges Gedaechtnis (letzte N Turns -> PipelineContext)
- SessionManager: Verwaltung von Sessions und Turns
- MemoryGateInterface: Platzhalter fuer LSTM-inspiriertes Memory (HNZ-00x)

Memory-Schichtung:
    Working Memory   = letzte N Turns dieser Session (Core, hier implementiert)
    Episodic Memory  = alle vergangenen Sessions, querybar (HNZ-003)
    Semantic Memory  = destillierte Fakten (HNZ-003)
    Procedural Memory = gelernte Strategien, Gate-System (HNZ-00x)
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from .models.base import Message


# =============================================================================
# Hilfsfunktionen
# =============================================================================


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _uuid() -> str:
    return str(uuid4())


# =============================================================================
# Enums
# =============================================================================


class SessionStatus(str, enum.Enum):
    """Status einer Session."""

    active = "active"
    paused = "paused"
    ended = "ended"


# =============================================================================
# Datenmodelle
# =============================================================================


class Session(BaseModel, frozen=True):
    """Klammer um alle Turns einer Konversation.

    Frozen - Zustandsaenderungen nur via model_copy(update=...).
    """

    id: str = Field(default_factory=_uuid)
    heinzel_id: str
    user_id: str | None = None
    status: SessionStatus = SessionStatus.active
    started_at: datetime = Field(default_factory=_utcnow)
    last_active_at: datetime = Field(default_factory=_utcnow)
    turn_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class Turn(BaseModel, frozen=True):
    """Einzelner Eingabe/Ausgabe-Zyklus mit vollstaendiger Context-Referenz.

    snapshot_ids verweist auf alle PipelineContext-Snapshots dieses Turns.
    Frozen - unveraenderlich nach Erstellung.
    """

    id: str = Field(default_factory=_uuid)
    session_id: str
    timestamp: datetime = Field(default_factory=_utcnow)
    raw_input: str
    final_response: str
    strategy_used: str = ""
    complexity_level: str = ""
    history_depth: int = 0
    snapshot_ids: tuple[str, ...] = ()
    duration_ms: int = 0
    tokens_used: int = 0


# =============================================================================
# ABCs
# =============================================================================


class WorkingMemory(ABC):
    """Kurzfristiges Gedaechtnis: letzte N Turns -> PipelineContext.messages.

    Wird bei ON_MEMORY_QUERY in den PipelineContext eingespeist.

    Grenzen (beide aktiv):
        max_tokens — Token-Budget (Hauptgrenze, token-basiert)
        max_turns  — Sicherheitsnetz (verhindert endlose Akkumulation)
    """

    @property
    @abstractmethod
    def max_tokens(self) -> int:
        """Token-Budget fuer das Working Memory.

        Hauptgrenze: add_turn() trimmt die aeltesten Turns bis
        estimated_tokens() wieder unter diesem Wert liegt.
        """

    @property
    @abstractmethod
    def max_turns(self) -> int:
        """Maximale Turns als Sicherheitsnetz (unabhaengig von Tokens).

        Verhindert endlose Akkumulation bei sehr kurzen Turns.
        Typischerweise gross gewaehlt (z.B. 10_000).
        """

    @abstractmethod
    async def get_recent_turns(self, n: int) -> list[Turn]:
        """Letzte n Turns zurueckgeben."""

    @abstractmethod
    async def add_turn(self, turn: Turn) -> None:
        """Turn ins Working Memory aufnehmen."""

    @abstractmethod
    async def get_context_messages(
        self, max_tokens: int | None = None
    ) -> tuple[Message, ...]:
        """Turn-History als Messages fuer den LLM aufbereiten.

        Neueste zuerst - aelteste fallen raus wenn max_tokens erschoepft.
        None bedeutet kein Token-Limit.
        """

    @abstractmethod
    async def clear(self) -> None:
        """Working Memory leeren."""

    @abstractmethod
    def estimated_tokens(self) -> int:
        """Schaetzt den Token-Verbrauch aller gespeicherten Turns.

        Grobe Schaetzung (len(text) / 4) — reicht fuer Budgetentscheidungen.
        Kein API-Call, muss synchron und schnell sein.
        """

    @abstractmethod
    async def compact(self, keep_ratio: float = 0.5) -> None:
        """Aelteste Turns entfernen um Kontextfenster zu entlasten.

        keep_ratio=0.5 bedeutet: die juengsten 50% der Turns behalten,
        den Rest verwerfen. Eine echte Impl koennte die verworfenen Turns
        vorher via LLM zusammenfassen (Kompaktifizierung).

        Wird automatisch von _call_provider() ausgeloest wenn der Provider
        einen ContextLengthExceededError wirft.
        """


class SessionManager(ABC):
    """Verwaltung von Sessions und Turns."""

    @property
    @abstractmethod
    def active_session(self) -> Session | None:
        """Aktuell aktive Session oder None."""

    @abstractmethod
    async def create_session(
        self,
        heinzel_id: str,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> Session:
        """Neue Session anlegen und als aktiv setzen.

        session_id: optionale ID — wenn None wird eine UUID generiert.
        Wird benutzt um eine explizit uebergebene session_id zu erhalten.
        """

    @abstractmethod
    async def get_session(self, session_id: str) -> Session | None:
        """Session per ID laden. None wenn nicht gefunden."""

    @abstractmethod
    async def resume_session(self, session_id: str) -> Session:
        """Vorhandene Session fortsetzen und als aktiv setzen.

        Raises:
            SessionNotFoundError: wenn session_id unbekannt.
        """

    @abstractmethod
    async def end_session(self, session_id: str) -> None:
        """Session beenden (status=ended), active_session auf None."""

    @abstractmethod
    async def add_turn(self, session_id: str, turn: Turn) -> None:
        """Turn zur Session hinzufuegen, Metadaten aktualisieren."""

    @abstractmethod
    async def get_turns(self, session_id: str, limit: int = 10) -> list[Turn]:
        """Letzte limit Turns einer Session."""

    @abstractmethod
    async def list_sessions(
        self, heinzel_id: str, limit: int = 20
    ) -> list[Session]:
        """Alle Sessions eines Heinzel, neueste zuerst."""

    @abstractmethod
    async def get_working_memory(self, session_id: str) -> WorkingMemory:
        """WorkingMemory fuer eine Session zurueckgeben."""


class MemoryGateInterface(ABC):
    """LSTM-inspiriertes Gate-System fuer Procedural Memory.

    PLATZHALTER - wird in HNZ-00x implementiert.

    Drei Gates analog zu LSTM:
        Forget Gate  (forget):   Welche Turns nicht ins Working Memory?
        Input Gate   (store):    Ist dieser Turn es wert gespeichert zu werden?
        Output Gate  (retrieve): Welche Turns sind jetzt relevant?
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Bezeichner dieser Gate-Implementierung."""

    @abstractmethod
    async def forget(self, turns: list[Turn], context: Any) -> list[Turn]:
        """Forget Gate: unrelevante Turns herausfiltern."""

    @abstractmethod
    async def store(self, turn: Turn, context: Any) -> bool:
        """Input Gate: True wenn Turn gespeichert werden soll."""

    @abstractmethod
    async def retrieve(self, context: Any, capacity: int) -> list[Turn]:
        """Output Gate: relevante Turns fuer den aktuellen Context."""
