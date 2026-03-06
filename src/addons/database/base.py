"""DatabaseAddOn — Abstrakte Basis für DB-Zugriff.

Kein anderes AddOn öffnet eigene Verbindungen.
Interface: execute(), fetch(), fetchrow(), migrate()

Zwei Implementierungen:
    SQLiteAddOn    — aiosqlite, :memory: für Tests
    PostgreSQLAddOn — asyncpg, Produktion
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from core.addon import AddOn

# =============================================================================
# Schema — idempotent, beide Implementierungen nutzen dasselbe DDL
# =============================================================================

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    heinzel_id  TEXT NOT NULL,
    user_id     TEXT,
    status      TEXT DEFAULT 'active',
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS exchanges (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  INTEGER REFERENCES sessions(id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS facts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    heinzel_id  TEXT NOT NULL,
    key         TEXT NOT NULL,
    value       TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(heinzel_id, key)
);
"""


# =============================================================================
# DatabaseAddOn — Interface
# =============================================================================


class DatabaseAddOn(AddOn):
    """Abstrakte Basis — alle AddOns nutzen diese Instanz für DB-Zugriff.

    Wird via heinzel.addons.get('database') geholt.
    """

    name = "database"
    version = "0.1.0"
    dependencies: list[str] = []

    @abstractmethod
    async def execute(self, sql: str, *args: Any) -> None:
        """DDL oder DML ohne Rückgabe (INSERT, UPDATE, DELETE, CREATE)."""
        ...

    @abstractmethod
    async def fetch(self, sql: str, *args: Any) -> list[dict]:
        """SELECT → Liste von Dicts. Leer wenn keine Zeilen."""
        ...

    @abstractmethod
    async def fetchrow(self, sql: str, *args: Any) -> dict | None:
        """SELECT → erste Zeile als Dict. None wenn keine Zeilen."""
        ...

    @abstractmethod
    async def migrate(self) -> None:
        """Schema anlegen/aktualisieren — idempotent."""
        ...
