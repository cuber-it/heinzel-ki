"""SQLiteAddOn — aiosqlite Implementierung.

Für lokale Entwicklung und Tests (auch :memory:).
Identisches Interface wie PostgreSQLAddOn.
"""

from __future__ import annotations

import logging
from typing import Any

import aiosqlite

from .base import DatabaseAddOn, SCHEMA_SQL

logger = logging.getLogger(__name__)


class SQLiteAddOn(DatabaseAddOn):
    """SQLite-Backend via aiosqlite.

    Konfiguration (heinzel.yaml):
        addons:
          database:
            backend: sqlite
            path: data/heinzel.db   # oder :memory:

    :memory: ist ideal für Tests — keine Datei, kein Cleanup.
    """

    name = "database"

    def __init__(self, path: str = ":memory:") -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def on_attach(self, heinzel) -> None:
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        # FK-Constraints aktivieren
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await migrate_sqlite(self._conn)
        logger.info(f"[SQLiteAddOn] verbunden: '{self._path}'")

    async def on_detach(self, heinzel) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
        logger.info("[SQLiteAddOn] getrennt")

    # -------------------------------------------------------------------------
    # Interface
    # -------------------------------------------------------------------------

    async def execute(self, sql: str, *args: Any) -> None:
        assert self._conn, "SQLiteAddOn nicht initialisiert"
        await self._conn.execute(sql, args)
        await self._conn.commit()

    async def fetch(self, sql: str, *args: Any) -> list[dict]:
        assert self._conn, "SQLiteAddOn nicht initialisiert"
        async with self._conn.execute(sql, args) as cur:
            rows = await cur.fetchall()
            return [dict(row) for row in rows]

    async def fetchrow(self, sql: str, *args: Any) -> dict | None:
        assert self._conn, "SQLiteAddOn nicht initialisiert"
        async with self._conn.execute(sql, args) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def migrate(self) -> None:
        assert self._conn, "SQLiteAddOn nicht initialisiert"
        await migrate_sqlite(self._conn)

    # -------------------------------------------------------------------------
    # SQLite-spezifisch
    # -------------------------------------------------------------------------

    async def last_insert_id(self) -> int | None:
        """Letzte INSERT-ID holen (SQLite-spezifisch)."""
        row = await self.fetchrow("SELECT last_insert_rowid() AS id")
        return row["id"] if row else None


# =============================================================================
# Migration
# =============================================================================


async def migrate_sqlite(conn: aiosqlite.Connection) -> None:
    """Schema anlegen — idempotent dank IF NOT EXISTS."""
    # SQLite versteht kein REFERENCES in AUTOINCREMENT-Tabellen ohne FK-Pragma
    # und kein TIMESTAMP als eigenen Typ — wir nutzen TEXT, kompatibel genug
    schema = SCHEMA_SQL.replace(
        "INTEGER REFERENCES sessions(id) ON DELETE CASCADE",
        "INTEGER",  # FK-Enforcement via PRAGMA foreign_keys
    )
    await conn.executescript(schema)
    await conn.commit()
    logger.debug("[SQLiteAddOn] Migration abgeschlossen")
