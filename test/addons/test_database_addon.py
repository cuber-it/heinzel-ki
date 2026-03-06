"""Tests für DatabaseAddOn — SQLite (in-memory), Schema-Migration, Interface."""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from addons.database import SQLiteAddOn, DatabaseAddOn, SCHEMA_SQL
from addons.database.postgres import _adapt_schema_for_postgres


class _FakeHeinzel:
    pass


# =============================================================================
# SQLiteAddOn — Lifecycle
# =============================================================================


@pytest.mark.asyncio
async def test_on_attach_opens_connection():
    addon = SQLiteAddOn(path=":memory:")
    await addon.on_attach(_FakeHeinzel())
    assert addon._conn is not None
    await addon.on_detach(_FakeHeinzel())


@pytest.mark.asyncio
async def test_on_detach_closes_connection():
    addon = SQLiteAddOn(path=":memory:")
    await addon.on_attach(_FakeHeinzel())
    await addon.on_detach(_FakeHeinzel())
    assert addon._conn is None


# =============================================================================
# Migration — idempotent
# =============================================================================


@pytest.mark.asyncio
async def test_migrate_creates_tables():
    addon = SQLiteAddOn(path=":memory:")
    await addon.on_attach(_FakeHeinzel())

    tables = await addon.fetch(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    names = {r["name"] for r in tables}
    assert "sessions" in names
    assert "exchanges" in names
    assert "facts" in names

    await addon.on_detach(_FakeHeinzel())


@pytest.mark.asyncio
async def test_migrate_idempotent():
    """Zweimalige Migration darf keinen Fehler werfen."""
    addon = SQLiteAddOn(path=":memory:")
    await addon.on_attach(_FakeHeinzel())
    await addon.migrate()  # zweites Mal
    await addon.migrate()  # drittes Mal
    await addon.on_detach(_FakeHeinzel())


# =============================================================================
# execute / fetch / fetchrow
# =============================================================================


@pytest.fixture
async def db():
    addon = SQLiteAddOn(path=":memory:")
    await addon.on_attach(_FakeHeinzel())
    yield addon
    await addon.on_detach(_FakeHeinzel())


@pytest.mark.asyncio
async def test_execute_insert(db):
    await db.execute(
        "INSERT INTO sessions (heinzel_id, user_id) VALUES (?, ?)",
        "riker", "user-1"
    )
    rows = await db.fetch("SELECT * FROM sessions")
    assert len(rows) == 1
    assert rows[0]["heinzel_id"] == "riker"


@pytest.mark.asyncio
async def test_fetch_multiple_rows(db):
    await db.execute("INSERT INTO sessions (heinzel_id) VALUES (?)", "riker")
    await db.execute("INSERT INTO sessions (heinzel_id) VALUES (?)", "riker")
    rows = await db.fetch("SELECT * FROM sessions WHERE heinzel_id = ?", "riker")
    assert len(rows) == 2


@pytest.mark.asyncio
async def test_fetch_empty(db):
    rows = await db.fetch("SELECT * FROM sessions")
    assert rows == []


@pytest.mark.asyncio
async def test_fetchrow_returns_first(db):
    await db.execute("INSERT INTO sessions (heinzel_id) VALUES (?)", "riker")
    await db.execute("INSERT INTO sessions (heinzel_id) VALUES (?)", "riker-2")
    row = await db.fetchrow("SELECT * FROM sessions ORDER BY id LIMIT 1")
    assert row is not None
    assert row["heinzel_id"] == "riker"


@pytest.mark.asyncio
async def test_fetchrow_none_when_empty(db):
    row = await db.fetchrow("SELECT * FROM sessions WHERE id = ?", 9999)
    assert row is None


# =============================================================================
# Schema — sessions / exchanges / facts
# =============================================================================


@pytest.mark.asyncio
async def test_insert_exchange(db):
    await db.execute("INSERT INTO sessions (heinzel_id) VALUES (?)", "riker")
    session_id = (await db.fetchrow("SELECT id FROM sessions"))["id"]
    await db.execute(
        "INSERT INTO exchanges (session_id, role, content) VALUES (?, ?, ?)",
        session_id, "user", "Hallo!"
    )
    rows = await db.fetch("SELECT * FROM exchanges WHERE session_id = ?", session_id)
    assert len(rows) == 1
    assert rows[0]["role"] == "user"
    assert rows[0]["content"] == "Hallo!"


@pytest.mark.asyncio
async def test_insert_fact(db):
    await db.execute(
        "INSERT INTO facts (heinzel_id, key, value) VALUES (?, ?, ?)",
        "riker", "sprache", "deutsch"
    )
    row = await db.fetchrow(
        "SELECT * FROM facts WHERE heinzel_id = ? AND key = ?", "riker", "sprache"
    )
    assert row is not None
    assert row["value"] == "deutsch"


@pytest.mark.asyncio
async def test_facts_unique_constraint(db):
    """(heinzel_id, key) ist UNIQUE — zweites INSERT muss scheitern."""
    await db.execute(
        "INSERT INTO facts (heinzel_id, key, value) VALUES (?, ?, ?)",
        "riker", "sprache", "deutsch"
    )
    with pytest.raises(Exception):
        await db.execute(
            "INSERT INTO facts (heinzel_id, key, value) VALUES (?, ?, ?)",
            "riker", "sprache", "englisch"
        )


@pytest.mark.asyncio
async def test_facts_upsert(db):
    """INSERT OR REPLACE als Upsert-Pattern."""
    await db.execute(
        "INSERT INTO facts (heinzel_id, key, value) VALUES (?, ?, ?)",
        "riker", "sprache", "deutsch"
    )
    await db.execute(
        "INSERT OR REPLACE INTO facts (heinzel_id, key, value) VALUES (?, ?, ?)",
        "riker", "sprache", "englisch"
    )
    row = await db.fetchrow(
        "SELECT value FROM facts WHERE heinzel_id = ? AND key = ?", "riker", "sprache"
    )
    assert row["value"] == "englisch"


@pytest.mark.asyncio
async def test_last_insert_id(db):
    await db.execute("INSERT INTO sessions (heinzel_id) VALUES (?)", "riker")
    last_id = await db.last_insert_id()
    assert last_id is not None
    assert last_id > 0


# =============================================================================
# ABC — nicht direkt instanziierbar
# =============================================================================


def test_database_addon_is_abstract():
    with pytest.raises(TypeError):
        DatabaseAddOn()


# =============================================================================
# PostgreSQL Schema-Adaptation
# =============================================================================


def test_adapt_schema_replaces_autoincrement():
    adapted = _adapt_schema_for_postgres(SCHEMA_SQL)
    assert "SERIAL PRIMARY KEY" in adapted
    assert "AUTOINCREMENT" not in adapted
