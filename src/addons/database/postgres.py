"""PostgreSQLAddOn — asyncpg Implementierung.

Produktion. Nutzt Connection Pool.
Identisches Interface wie SQLiteAddOn.

asyncpg verwendet $1, $2, ... als Platzhalter (nicht ?).
Das Interface nimmt *args — intern korrekt übergeben.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import DatabaseAddOn, SCHEMA_SQL

logger = logging.getLogger(__name__)

# asyncpg ist optional — kein Pflichtimport auf Entwicklungsmaschinen ohne PG
try:
    import asyncpg
    _ASYNCPG_AVAILABLE = True
except ImportError:
    _ASYNCPG_AVAILABLE = False


class PostgreSQLAddOn(DatabaseAddOn):
    """PostgreSQL-Backend via asyncpg Connection Pool.

    Konfiguration (heinzel.yaml):
        addons:
          database:
            backend: postgres
            dsn: postgresql://admin:pass@services:5432/heinzel_db
            min_size: 2
            max_size: 10
    """

    name = "database"

    def __init__(
        self,
        dsn: str,
        min_size: int = 2,
        max_size: int = 10,
    ) -> None:
        if not _ASYNCPG_AVAILABLE:
            raise ImportError(
                "asyncpg nicht installiert — "
                "'pip install asyncpg' für PostgreSQL-Support"
            )
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool = None

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def on_attach(self, heinzel) -> None:
        self._pool = await asyncpg.create_pool(
            self._dsn,
            min_size=self._min_size,
            max_size=self._max_size,
        )
        await self.migrate()
        logger.info(
            f"[PostgreSQLAddOn] Pool geöffnet — "
            f"min={self._min_size}, max={self._max_size}"
        )

    async def on_detach(self, heinzel) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
        logger.info("[PostgreSQLAddOn] Pool geschlossen")

    # -------------------------------------------------------------------------
    # Interface
    # -------------------------------------------------------------------------

    async def execute(self, sql: str, *args: Any) -> None:
        assert self._pool, "PostgreSQLAddOn nicht initialisiert"
        async with self._pool.acquire() as conn:
            await conn.execute(sql, *args)

    async def fetch(self, sql: str, *args: Any) -> list[dict]:
        assert self._pool, "PostgreSQLAddOn nicht initialisiert"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *args)
            return [dict(row) for row in rows]

    async def fetchrow(self, sql: str, *args: Any) -> dict | None:
        assert self._pool, "PostgreSQLAddOn nicht initialisiert"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(sql, *args)
            return dict(row) if row else None

    async def migrate(self) -> None:
        assert self._pool, "PostgreSQLAddOn nicht initialisiert"
        # PostgreSQL: AUTOINCREMENT → SERIAL, ? → $n (asyncpg nutzt $1..$n)
        schema = _adapt_schema_for_postgres(SCHEMA_SQL)
        async with self._pool.acquire() as conn:
            await conn.execute(schema)
        logger.debug("[PostgreSQLAddOn] Migration abgeschlossen")


# =============================================================================
# Schema-Anpassung für PostgreSQL
# =============================================================================


def _adapt_schema_for_postgres(sql: str) -> str:
    """SQLite-Schema für PostgreSQL anpassen.

    - INTEGER PRIMARY KEY AUTOINCREMENT → SERIAL PRIMARY KEY
    - TIMESTAMP DEFAULT CURRENT_TIMESTAMP bleibt
    - UNIQUE bleibt
    """
    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    return sql
