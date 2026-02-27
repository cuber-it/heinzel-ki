"""
Tests für HNZ-001-0005: Technisches Metriken-Logging (DB-agnostisch)
Alle Tests laufen gegen SQLite in tmp_path.
"""
import sys, os, asyncio
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))
import pytest


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


@pytest.fixture
def db(tmp_path, monkeypatch):
    """CostLogger mit frischer SQLite-DB."""
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # config-Modul neu laden damit instance_config die neue Env sieht
    import importlib, config as cfg_mod
    importlib.reload(cfg_mod)
    import database as db_mod
    importlib.reload(db_mod)
    logger = db_mod.CostLogger()
    run(logger.connect())
    yield logger


def test_log_and_query(db):
    run(db.log_request("openai", "gpt-4o", 100, 50, 200,
                       session_id="s1", heinzel_id="h1", status="success"))
    rows = run(db.query())
    assert len(rows) == 1
    assert rows[0]["provider"] == "openai"
    assert rows[0]["input_tokens"] == 100
    assert rows[0]["session_id"] == "s1"


def test_query_filter_session(db):
    run(db.log_request("openai", "gpt-4o", 10, 5, 100, session_id="s1"))
    run(db.log_request("openai", "gpt-4o", 20, 8, 150, session_id="s2"))
    rows = run(db.query(session_id="s1"))
    assert len(rows) == 1
    assert rows[0]["session_id"] == "s1"


def test_query_filter_status(db):
    run(db.log_request("openai", "gpt-4o", 10, 5, 100, status="success"))
    run(db.log_request("openai", "gpt-4o", 0, 0, 50, status="error"))
    rows = run(db.query(status="error"))
    assert len(rows) == 1
    assert rows[0]["status"] == "error"


def test_query_limit(db):
    for i in range(5):
        run(db.log_request("openai", "gpt-4o", i, i, i * 10))
    rows = run(db.query(limit=3))
    assert len(rows) == 3


def test_summary(db):
    run(db.log_request("openai", "gpt-4o", 100, 50, 200, status="success"))
    run(db.log_request("openai", "gpt-4o", 200, 80, 300, status="success"))
    run(db.log_request("openai", "gpt-4o", 0, 0, 50, status="error"))
    s = run(db.summary())
    assert s["total_requests"] == 3
    assert s["total_input_tokens"] == 300
    assert s["total_output_tokens"] == 130
    assert s["error_count"] == 1
    assert s["avg_latency_ms"] == pytest.approx((200 + 300 + 50) / 3, rel=0.01)


def test_summary_filter_session(db):
    run(db.log_request("openai", "gpt-4o", 100, 50, 200, session_id="s1"))
    run(db.log_request("openai", "gpt-4o", 200, 80, 300, session_id="s2"))
    s = run(db.summary(session_id="s1"))
    assert s["total_requests"] == 1
    assert s["total_input_tokens"] == 100


def test_empty_db(db):
    rows = run(db.query())
    assert rows == []
    s = run(db.summary())
    assert s.get("total_requests") == 0 or s == {}
