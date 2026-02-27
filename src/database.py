"""
H.E.I.N.Z.E.L. Provider Gateway – Cost Logger

Unterstützt PostgreSQL und SQLite.
Auswahl über DATABASE_URL:
  - Nicht gesetzt oder leer → SQLite unter /data/costs.db
  - postgresql://...        → PostgreSQL via asyncpg
  - sqlite:///path/to/db   → SQLite an explizitem Pfad

Die costs-Tabelle wird beim Start automatisch angelegt falls nicht vorhanden.
"""

import os
import sys
from typing import Optional

from config import instance_config


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS costs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    provider    TEXT NOT NULL,
    model       TEXT NOT NULL,
    input_tokens  INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    latency_ms  INTEGER DEFAULT 0,
    heinzel_id  TEXT,
    session_id  TEXT,
    task_id     TEXT,
    status      TEXT DEFAULT 'success',
    error_message TEXT
)
"""

INSERT_SQL = """
INSERT INTO costs (
    provider, model, input_tokens, output_tokens,
    latency_ms, heinzel_id, session_id, task_id,
    status, error_message
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

# PostgreSQL nutzt $1..$N statt ?
INSERT_SQL_PG = INSERT_SQL.replace("?", "{}").format(
    "$1","$2","$3","$4","$5","$6","$7","$8","$9","$10"
)


def _resolve_db_url() -> tuple[str, str]:
    """
    Gibt (db_type, url/path) zurück via instance_config.
    db_type: 'sqlite' oder 'postgresql'
    """
    data_dir = os.environ.get("LOG_DIR", "/data")
    url = instance_config.database_url(default_data_dir=data_dir)
    if url.startswith("postgresql"):
        return "postgresql", url
    # sqlite
    path = url[len("sqlite:///"):] if url.startswith("sqlite:///") else url
    return "sqlite", path


class CostLogger:
    """
    Async Cost-Logger. Unterstützt SQLite (Default) und PostgreSQL.
    Fällt bei DB-Fehler still zurück — Provider-Betrieb wird nie blockiert.
    """

    def __init__(self):
        self._db_type, self._db_url = _resolve_db_url()
        self._pool = None        # asyncpg pool (PostgreSQL)
        self._sqlite_path = None # Pfad (SQLite)

    async def connect(self):
        if self._db_type == "postgresql":
            await self._connect_pg()
        else:
            await self._connect_sqlite()

    async def _connect_pg(self):
        try:
            import asyncpg
            self._pool = await asyncpg.create_pool(
                self._db_url, min_size=2, max_size=10)
            # Tabelle anlegen (PostgreSQL-Syntax leicht abweichend)
            async with self._pool.acquire() as conn:
                await conn.execute(CREATE_TABLE_SQL.replace(
                    "INTEGER PRIMARY KEY AUTOINCREMENT",
                    "SERIAL PRIMARY KEY"
                ))
            print(f"CostLogger: PostgreSQL verbunden ({self._db_url[:30]}...)", file=sys.stderr)
        except Exception as e:
            print(f"CostLogger: PostgreSQL Fehler, deaktiviert: {e}", file=sys.stderr)
            self._pool = None

    async def _connect_sqlite(self):
        try:
            import aiosqlite
            self._sqlite_path = self._db_url
            # Tabelle anlegen
            async with aiosqlite.connect(self._sqlite_path) as db:
                await db.execute(CREATE_TABLE_SQL)
                await db.commit()
            print(f"CostLogger: SQLite verbunden ({self._sqlite_path})", file=sys.stderr)
        except Exception as e:
            print(f"CostLogger: SQLite Fehler, deaktiviert: {e}", file=sys.stderr)
            self._sqlite_path = None

    async def disconnect(self):
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def log_request(
        self,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        heinzel_id: Optional[str] = None,
        session_id: Optional[str] = None,
        task_id: Optional[str] = None,
        status: str = "success",
        error_message: Optional[str] = None,
    ):
        args = (provider, model, input_tokens, output_tokens,
                latency_ms, heinzel_id, session_id, task_id,
                status, error_message)
        try:
            if self._db_type == "postgresql" and self._pool:
                async with self._pool.acquire() as conn:
                    await conn.execute(INSERT_SQL_PG, *args)
            elif self._db_type == "sqlite" and self._sqlite_path:
                import aiosqlite
                async with aiosqlite.connect(self._sqlite_path) as db:
                    await db.execute(INSERT_SQL, args)
                    await db.commit()
        except Exception as e:
            print(f"CostLogger: Log-Fehler (nicht kritisch): {e}", file=sys.stderr)

    async def query(
        self,
        session_id: Optional[str] = None,
        heinzel_id: Optional[str] = None,
        provider:   Optional[str] = None,
        model:      Optional[str] = None,
        since:      Optional[str] = None,
        until:      Optional[str] = None,
        status:     Optional[str] = None,
        limit:      int = 100,
    ) -> list[dict]:
        """Metriken filtern. Neueste zuerst."""
        conditions, params = [], []
        if session_id: conditions.append("session_id = ?"); params.append(session_id)
        if heinzel_id: conditions.append("heinzel_id = ?"); params.append(heinzel_id)
        if provider:   conditions.append("provider = ?");   params.append(provider)
        if model:      conditions.append("model = ?");      params.append(model)
        if status:     conditions.append("status = ?");     params.append(status)
        if since:      conditions.append("ts >= ?");        params.append(since)
        if until:      conditions.append("ts <= ?");        params.append(until)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"SELECT * FROM costs {where} ORDER BY ts DESC LIMIT ?"
        params.append(min(limit, 1000))
        try:
            if self._db_type == "postgresql" and self._pool:
                pg_sql = sql
                for i in range(len(params)):
                    pg_sql = pg_sql.replace("?", f"${i+1}", 1)
                async with self._pool.acquire() as conn:
                    rows = await conn.fetch(pg_sql, *params)
                    return [dict(r) for r in rows]
            elif self._db_type == "sqlite" and self._sqlite_path:
                import aiosqlite
                async with aiosqlite.connect(self._sqlite_path) as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute(sql, params) as cur:
                        return [dict(r) for r in await cur.fetchall()]
        except Exception as e:
            print(f"CostLogger: Query-Fehler: {e}", file=sys.stderr)
        return []

    async def summary(
        self,
        session_id: Optional[str] = None,
        heinzel_id: Optional[str] = None,
        since:      Optional[str] = None,
        until:      Optional[str] = None,
    ) -> dict:
        """Aggregierte Metriken: Tokens gesamt, Latenz-Avg, Fehleranzahl."""
        conditions, params = [], []
        if session_id: conditions.append("session_id = ?"); params.append(session_id)
        if heinzel_id: conditions.append("heinzel_id = ?"); params.append(heinzel_id)
        if since:      conditions.append("ts >= ?");        params.append(since)
        if until:      conditions.append("ts <= ?");        params.append(until)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
            SELECT COUNT(*) as total_requests,
                   SUM(input_tokens) as total_input_tokens,
                   SUM(output_tokens) as total_output_tokens,
                   AVG(latency_ms) as avg_latency_ms,
                   SUM(CASE WHEN status='error' THEN 1 ELSE 0 END) as error_count
            FROM costs {where}
        """
        try:
            if self._db_type == "postgresql" and self._pool:
                pg_sql = sql
                for i in range(len(params)):
                    pg_sql = pg_sql.replace("?", f"${i+1}", 1)
                async with self._pool.acquire() as conn:
                    row = await conn.fetchrow(pg_sql, *params)
                    return dict(row) if row else {}
            elif self._db_type == "sqlite" and self._sqlite_path:
                import aiosqlite
                async with aiosqlite.connect(self._sqlite_path) as db:
                    db.row_factory = aiosqlite.Row
                    async with db.execute(sql, params) as cur:
                        row = await cur.fetchone()
                        return dict(row) if row else {}
        except Exception as e:
            print(f"CostLogger: Summary-Fehler: {e}", file=sys.stderr)
        return {}


# Singleton
cost_logger = CostLogger()
