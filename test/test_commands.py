"""
Tests für Provider-Kommandoschnittstelle (stateless, Prefix: !)
Kommandos: !help, !status, !dlglog
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src/llm-provider"))


# ─── Hilfsobjekte ─────────────────────────────────────────────

class FakeLogger:
    def __init__(self): self.enabled = True

class FakeProvider:
    provider_name = "openai"
    _connected = True
    logger = FakeLogger()
    config = {"retry": {"max_retries": 3}}
    _rate_limit_hits = []
    def get_default_model(self): return "gpt-4o"
    def get_models(self): return ["gpt-4o", "gpt-4o-mini", "o1"]

def make(): return FakeProvider()


# ─── Protokoll-Erkennung ──────────────────────────────────────

def test_is_command_true():
    from commands import is_command
    assert is_command("!status") is True
    assert is_command("  !help  ") is True
    assert is_command("!dlglog off") is True

def test_is_command_false():
    from commands import is_command
    assert is_command("normale Nachricht") is False
    assert is_command("") is False
    assert is_command("!") is False
    assert is_command("! mit Leerzeichen") is False

def test_extract_command():
    from commands import extract_command
    assert extract_command("!status") == ("status", [])
    assert extract_command("!dlglog off") == ("dlglog", ["off"])
    assert extract_command("!help") == ("help", [])


# ─── !help ────────────────────────────────────────────────────

def test_cmd_help():
    from commands import execute_command
    r = execute_command("help", [], make())
    assert "commands" in r
    assert "note" in r
    assert any("!status" in c for c in r["commands"])
    assert any("!dlglog" in c for c in r["commands"])


# ─── !status ──────────────────────────────────────────────────

def test_cmd_status():
    from commands import execute_command
    r = execute_command("status", [], make())
    assert r["provider"] == "openai"
    assert r["connected"] is True
    assert r["default_model"] == "gpt-4o"
    assert r["dialog_logging"] is True
    assert "available_models" in r
    assert "retry_config" in r
    assert "rate_limit_hits" in r


# ─── !dlglog ──────────────────────────────────────────────────

def test_cmd_dlglog_off():
    from commands import execute_command
    p = make()
    r = execute_command("dlglog", ["off"], p)
    assert r["ok"] is True
    assert p.logger.enabled is False

def test_cmd_dlglog_on():
    from commands import execute_command
    p = make()
    p.logger.enabled = False
    r = execute_command("dlglog", ["on"], p)
    assert r["ok"] is True
    assert p.logger.enabled is True

def test_cmd_dlglog_no_arg():
    from commands import execute_command
    r = execute_command("dlglog", [], make())
    assert "error" in r
    assert "current" in r

def test_cmd_dlglog_invalid():
    from commands import execute_command
    r = execute_command("dlglog", ["maybe"], make())
    assert "error" in r


# ─── Unbekanntes Kommando ─────────────────────────────────────

def test_cmd_unknown():
    from commands import execute_command
    r = execute_command("set", ["model=gpt-4o"], make())
    assert "error" in r
    assert "hint" in r

def test_cmd_unknown_gibberish():
    from commands import execute_command
    r = execute_command("gibberish", [], make())
    assert "error" in r
    assert "hint" in r
