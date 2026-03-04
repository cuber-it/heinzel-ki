"""FeedbackStore — loggt Strategy-Selektionen für spätere Auswertung.

ABC: FeedbackStore
  SqliteFeedbackStore  — default, kein Infrastruktur-Aufwand
  PostgresFeedbackStore — kommt wenn Postgres-Infra bereit (kein Code-Aufriss)

Austausch: Runner bekommt andere Impl injiziert, fertig.
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


# =============================================================================
# SelectionEvent
# =============================================================================

@dataclass
class SelectionEvent:
    input_preview: str
    final_strategy: str
    heuristic_result: str | None = None
    llm_result: str | None = None
    user_override: str | None = None      # gesetzt wenn User !strategy tippt
    session_id: str = ""
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict:
        return {
            "input_preview": self.input_preview,
            "heuristic_result": self.heuristic_result,
            "llm_result": self.llm_result,
            "final_strategy": self.final_strategy,
            "user_override": self.user_override,
            "session_id": self.session_id,
            "ts": self.ts,
        }


# =============================================================================
# FeedbackEvent
# =============================================================================

@dataclass
class FeedbackEvent:
    """Bewertung eines Turns durch den User."""
    turn_id: str                          # Turn-ID (session_id + iteration reicht)
    session_id: str
    rating: int                           # 1-5 (1=schlecht, 5=sehr gut)
    comment: str = ""                     # optionaler Freitext
    strategy_used: str = ""              # welche Strategy aktiv war
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict:
        return {
            "turn_id": self.turn_id,
            "session_id": self.session_id,
            "rating": self.rating,
            "comment": self.comment,
            "strategy_used": self.strategy_used,
            "ts": self.ts,
        }


# =============================================================================
# ABC
# =============================================================================

class FeedbackStore(ABC):

    @abstractmethod
    async def log(self, event: SelectionEvent) -> None:
        """Event persistent speichern."""

    @abstractmethod
    async def log_override(self, session_id: str, chosen_strategy: str) -> None:
        """User-Override nachträglich zum letzten Event der Session eintragen."""

    @abstractmethod
    async def get_stats(self) -> list[dict]:
        """Aggregierte Strategie-Selektions-Statistik."""

    @abstractmethod
    async def log_feedback(self, event: FeedbackEvent) -> None:
        """User-Bewertung eines Turns speichern (Rating + optionaler Kommentar)."""

    @abstractmethod
    async def get_feedback_stats(self) -> list[dict]:
        """Aggregierte Bewertungs-Statistik (Durchschnitt pro Strategy etc.)."""


# =============================================================================
# NoopFeedbackStore — für Tests
# =============================================================================

class NoopFeedbackStore(FeedbackStore):
    """Loggt nichts. Default in Tests."""

    def __init__(self) -> None:
        self.events: list[SelectionEvent] = []
        self.feedback_events: list[FeedbackEvent] = []

    async def log(self, event: SelectionEvent) -> None:
        self.events.append(event)

    async def log_override(self, session_id: str, chosen_strategy: str) -> None:
        pass

    async def get_stats(self) -> list[dict]:
        return []

    async def log_feedback(self, event: FeedbackEvent) -> None:
        self.feedback_events.append(event)

    async def get_feedback_stats(self) -> list[dict]:
        return []


# =============================================================================
# SqliteFeedbackStore
# =============================================================================

class SqliteFeedbackStore(FeedbackStore):
    """SQLite-Implementierung. Läuft ohne Infrastruktur."""

    _CREATE = """
    CREATE TABLE IF NOT EXISTS selection_events (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        ts               TEXT NOT NULL,
        session_id       TEXT,
        input_preview    TEXT,
        heuristic_result TEXT,
        llm_result       TEXT,
        final_strategy   TEXT,
        user_override    TEXT
    );
    CREATE TABLE IF NOT EXISTS feedback_events (
        id               INTEGER PRIMARY KEY AUTOINCREMENT,
        ts               TEXT NOT NULL,
        turn_id          TEXT,
        session_id       TEXT,
        rating           INTEGER,
        comment          TEXT,
        strategy_used    TEXT
    )
    """

    def __init__(self, db_path: Path | str = "logs/selector_feedback.db") -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self._path) as con:
            con.executescript(self._CREATE)


    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._path)

    async def log(self, event: SelectionEvent) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_log, event)

    def _sync_log(self, event: SelectionEvent) -> None:
        with self._connect() as con:
            con.execute(
                """INSERT INTO selection_events
                   (ts, session_id, input_preview, heuristic_result,
                    llm_result, final_strategy, user_override)
                   VALUES (?,?,?,?,?,?,?)""",
                (event.ts, event.session_id, event.input_preview,
                 event.heuristic_result, event.llm_result,
                 event.final_strategy, event.user_override),
            )
            con.commit()

    async def log_override(self, session_id: str, chosen_strategy: str) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_override, session_id, chosen_strategy)

    def _sync_override(self, session_id: str, chosen_strategy: str) -> None:
        with self._connect() as con:
            con.execute(
                """UPDATE selection_events SET user_override = ?
                   WHERE session_id = ?
                   AND id = (SELECT MAX(id) FROM selection_events WHERE session_id = ?)""",
                (chosen_strategy, session_id, session_id),
            )
            con.commit()

    async def get_stats(self) -> list[dict]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_stats)

    def _sync_stats(self) -> list[dict]:
        with self._connect() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("""
                SELECT
                    final_strategy,
                    COUNT(*) as total,
                    SUM(CASE WHEN heuristic_result IS NOT NULL THEN 1 ELSE 0 END) as via_heuristic,
                    SUM(CASE WHEN llm_result IS NOT NULL THEN 1 ELSE 0 END) as via_llm,
                    SUM(CASE WHEN user_override IS NOT NULL THEN 1 ELSE 0 END) as overridden
                FROM selection_events
                GROUP BY final_strategy
                ORDER BY total DESC
            """).fetchall()
            return [dict(r) for r in rows]

    async def log_feedback(self, event: FeedbackEvent) -> None:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._sync_log_feedback, event)

    def _sync_log_feedback(self, event: FeedbackEvent) -> None:
        with self._connect() as con:
            con.execute(
                """INSERT INTO feedback_events
                   (ts, turn_id, session_id, rating, comment, strategy_used)
                   VALUES (?,?,?,?,?,?)""",
                (event.ts, event.turn_id, event.session_id,
                 event.rating, event.comment, event.strategy_used),
            )
            con.commit()

    async def get_feedback_stats(self) -> list[dict]:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._sync_feedback_stats)

    def _sync_feedback_stats(self) -> list[dict]:
        with self._connect() as con:
            con.row_factory = sqlite3.Row
            rows = con.execute("""
                SELECT
                    strategy_used,
                    COUNT(*) as total,
                    ROUND(AVG(rating), 2) as avg_rating,
                    SUM(CASE WHEN comment != '' THEN 1 ELSE 0 END) as with_comment
                FROM feedback_events
                GROUP BY strategy_used
                ORDER BY avg_rating DESC
            """).fetchall()
            return [dict(r) for r in rows]


__all__ = [
    "SelectionEvent",
    "FeedbackEvent",
    "FeedbackStore",
    "NoopFeedbackStore",
    "SqliteFeedbackStore",
]
