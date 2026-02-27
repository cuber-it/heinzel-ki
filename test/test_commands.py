"""
Tests fuer HNZ-001-0003: In-Stream Kommandoschnittstelle (Prefix: !)
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))


# ─── Protokoll-Erkennung ───────────────────────────────────────

def test_is_command_true():
    from commands import is_command
    assert is_command("!status") is True
    assert is_command("  !help  ") is True
    assert is_command("!set model=gpt-4o") is True
    assert is_command("!dlglog off") is True
    assert is_command("!get temperature") is True


def test_is_command_false():
    from commands import is_command
    assert is_command("normale Nachricht") is False
    assert is_command("/einzel-slash") is False
    assert is_command("//doppel-slash") is False
    assert is_command("") is False
    assert is_command("!") is False          # nur Prefix, kein Kommando
    assert is_command("! mit Leerzeichen") is False  # Leerzeichen nach !


def test_extract_command():
    from commands import extract_command
    assert extract_command("!status") == ("status", [])
    assert extract_command("!set model=gpt-4o") == ("set", ["model=gpt-4o"])
    assert extract_command("!set temperature=0.7") == ("set", ["temperature=0.7"])
    assert extract_command("!dlglog off") == ("dlglog", ["off"])
    assert extract_command("!get temperature") == ("get", ["temperature"])


def test_parse_kv():
    from commands import _parse_kv
    assert _parse_kv("model=gpt-4o") == ("model", "gpt-4o")
    assert _parse_kv("temperature=0.7") == ("temperature", "0.7")
    assert _parse_kv("max_tokens=512") == ("max_tokens", "512")
    assert _parse_kv("kein-gleich") is None


# ─── Kommando-Ausfuehrung ─────────────────────────────────────

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

def make(): return FakeProvider(), {}


def test_cmd_help():
    from commands import execute_command
    p, sp = make()
    r = execute_command("help", [], p, sp)
    assert "commands" in r
    assert "examples" in r
    assert any("!set" in c for c in r["commands"])


def test_cmd_status():
    from commands import execute_command
    p, sp = make()
    r = execute_command("status", [], p, sp)
    assert r["provider"] == "openai"
    assert r["connected"] is True
    assert r["default_model"] == "gpt-4o"
    assert r["dialog_logging"] is True
    assert "available_models" in r
    assert "retry_config" in r
    assert "rate_limit_hits" in r


def test_cmd_dlglog_off():
    from commands import execute_command
    p, sp = make()
    r = execute_command("dlglog", ["off"], p, sp)
    assert r["ok"] is True
    assert p.logger.enabled is False


def test_cmd_dlglog_on():
    from commands import execute_command
    p, sp = make()
    p.logger.enabled = False
    r = execute_command("dlglog", ["on"], p, sp)
    assert r["ok"] is True
    assert p.logger.enabled is True


def test_cmd_dlglog_no_arg_returns_current():
    from commands import execute_command
    p, sp = make()
    r = execute_command("dlglog", [], p, sp)
    assert "error" in r
    assert "current" in r


def test_cmd_set_temperature():
    from commands import execute_command
    p, sp = make()
    r = execute_command("set", ["temperature=0.9"], p, sp)
    assert r["ok"] is True
    assert sp["temperature"] == 0.9


def test_cmd_set_temperature_out_of_range():
    from commands import execute_command
    p, sp = make()
    r = execute_command("set", ["temperature=3.0"], p, sp)
    assert "error" in r


def test_cmd_set_max_tokens():
    from commands import execute_command
    p, sp = make()
    r = execute_command("set", ["max_tokens=512"], p, sp)
    assert r["ok"] is True
    assert sp["max_tokens"] == 512


def test_cmd_set_model_valid():
    from commands import execute_command
    p, sp = make()
    r = execute_command("set", ["model=gpt-4o-mini"], p, sp)
    assert r["ok"] is True
    assert sp["model"] == "gpt-4o-mini"


def test_cmd_set_model_invalid():
    from commands import execute_command
    p, sp = make()
    r = execute_command("set", ["model=unbekannt-xyz"], p, sp)
    assert "error" in r
    assert "available" in r


def test_cmd_set_no_equals():
    from commands import execute_command
    p, sp = make()
    r = execute_command("set", ["temperature"], p, sp)  # kein =
    assert "error" in r


def test_cmd_set_unknown_param():
    from commands import execute_command
    p, sp = make()
    r = execute_command("set", ["unbekannt=wert"], p, sp)
    assert "error" in r
    assert "settable" in r


def test_cmd_get_all():
    from commands import execute_command
    p, sp = make()
    sp["temperature"] = 0.5
    r = execute_command("get", [], p, sp)
    assert "temperature" in r
    assert "model" in r
    assert "max_tokens" in r
    assert r["temperature"] == 0.5


def test_cmd_get_specific():
    from commands import execute_command
    p, sp = make()
    sp["temperature"] = 0.3
    r = execute_command("get", ["temperature"], p, sp)
    assert r["temperature"] == 0.3


def test_cmd_get_dialog_logging():
    from commands import execute_command
    p, sp = make()
    r = execute_command("get", ["dialog_logging"], p, sp)
    assert "dialog_logging" in r


def test_cmd_get_unknown():
    from commands import execute_command
    p, sp = make()
    r = execute_command("get", ["unbekannt"], p, sp)
    assert "error" in r
    assert "gettable" in r


def test_cmd_unknown():
    from commands import execute_command
    p, sp = make()
    r = execute_command("gibberish", [], p, sp)
    assert "error" in r
    assert "hint" in r
