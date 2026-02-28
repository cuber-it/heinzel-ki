"""
Tests für HNZ-001-0004: Kommunikations-Logging (Session-basiert)
"""
import sys, os, json, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src/llm-provider"))


# ─── Logger: session_id landet im Eintrag ──────────────────────

def test_logger_writes_session_id(tmp_path):
    from logger import RequestResponseLogger
    log = RequestResponseLogger("test", str(tmp_path), enabled=True)
    log.log_request("/chat", {"msg": "hi"},
                    session_id="sess-001", heinzel_id="hzl-01", task_id="task-x")
    lines = open(tmp_path / "test.jsonl").readlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["session_id"] == "sess-001"
    assert entry["heinzel_id"] == "hzl-01"
    assert entry["task_id"] == "task-x"
    assert entry["type"] == "request"
    assert entry["provider"] == "test"
    assert "timestamp" in entry


def test_logger_writes_response(tmp_path):
    from logger import RequestResponseLogger
    log = RequestResponseLogger("test-resp", str(tmp_path), enabled=True)
    log.log_response("/chat", 200, {"content": "Antwort"},
                     session_id="sess-002")
    entry = json.loads(open(tmp_path / "test-resp.jsonl").readline())
    assert entry["type"] == "response"
    assert entry["session_id"] == "sess-002"
    assert entry["data"]["status"] == 200


def test_logger_disabled_writes_nothing(tmp_path):
    from logger import RequestResponseLogger
    log = RequestResponseLogger("test-off", str(tmp_path), enabled=False)
    log.log_request("/chat", {}, session_id="sess-x")
    assert not (tmp_path / "test-off.jsonl").exists()


def test_logger_no_context_writes_null(tmp_path):
    from logger import RequestResponseLogger
    log = RequestResponseLogger("test-noctx", str(tmp_path), enabled=True)
    log.log_request("/chat", {"msg": "no context"})
    entry = json.loads(open(tmp_path / "test-noctx.jsonl").readline())
    assert entry["session_id"] is None
    assert entry["heinzel_id"] is None


# ─── LogReader: Filterung ──────────────────────────────────────

def _write_entries(log_file, entries):
    with open(log_file, "w") as f:
        for e in entries:
            f.write(json.dumps(e) + "\n")


def test_read_logs_all(tmp_path):
    from log_reader import read_logs
    _write_entries(tmp_path / "openai.jsonl", [
        {"timestamp": "2026-02-27T10:00:00Z", "provider": "openai",
         "type": "request", "session_id": "s1", "heinzel_id": "h1",
         "task_id": None, "data": {}},
        {"timestamp": "2026-02-27T10:01:00Z", "provider": "openai",
         "type": "response", "session_id": "s1", "heinzel_id": "h1",
         "task_id": None, "data": {}},
    ])
    result = read_logs(str(tmp_path), "openai")
    assert len(result) == 2


def test_read_logs_filter_session(tmp_path):
    from log_reader import read_logs
    _write_entries(tmp_path / "openai.jsonl", [
        {"timestamp": "2026-02-27T10:00:00Z", "provider": "openai",
         "type": "request", "session_id": "s1", "heinzel_id": None,
         "task_id": None, "data": {}},
        {"timestamp": "2026-02-27T10:01:00Z", "provider": "openai",
         "type": "request", "session_id": "s2", "heinzel_id": None,
         "task_id": None, "data": {}},
    ])
    result = read_logs(str(tmp_path), "openai", session_id="s1")
    assert len(result) == 1
    assert result[0]["session_id"] == "s1"


def test_read_logs_filter_type(tmp_path):
    from log_reader import read_logs
    _write_entries(tmp_path / "openai.jsonl", [
        {"timestamp": "2026-02-27T10:00:00Z", "provider": "openai",
         "type": "request", "session_id": "s1", "heinzel_id": None,
         "task_id": None, "data": {}},
        {"timestamp": "2026-02-27T10:01:00Z", "provider": "openai",
         "type": "response", "session_id": "s1", "heinzel_id": None,
         "task_id": None, "data": {}},
        {"timestamp": "2026-02-27T10:02:00Z", "provider": "openai",
         "type": "error", "session_id": "s1", "heinzel_id": None,
         "task_id": None, "data": {}},
    ])
    result = read_logs(str(tmp_path), "openai", entry_type="error")
    assert len(result) == 1
    assert result[0]["type"] == "error"


def test_read_logs_filter_since(tmp_path):
    from log_reader import read_logs
    _write_entries(tmp_path / "openai.jsonl", [
        {"timestamp": "2026-02-27T09:00:00Z", "provider": "openai",
         "type": "request", "session_id": "s1", "heinzel_id": None,
         "task_id": None, "data": {}},
        {"timestamp": "2026-02-27T11:00:00Z", "provider": "openai",
         "type": "request", "session_id": "s2", "heinzel_id": None,
         "task_id": None, "data": {}},
    ])
    result = read_logs(str(tmp_path), "openai", since="2026-02-27T10:00:00Z")
    assert len(result) == 1
    assert result[0]["session_id"] == "s2"


def test_read_logs_limit(tmp_path):
    from log_reader import read_logs
    entries = [
        {"timestamp": f"2026-02-27T10:0{i}:00Z", "provider": "openai",
         "type": "request", "session_id": f"s{i}", "heinzel_id": None,
         "task_id": None, "data": {}}
        for i in range(5)
    ]
    _write_entries(tmp_path / "openai.jsonl", entries)
    result = read_logs(str(tmp_path), "openai", limit=3)
    assert len(result) == 3


def test_read_logs_empty(tmp_path):
    from log_reader import read_logs
    result = read_logs(str(tmp_path), "openai")
    assert result == []


def test_read_logs_filter_heinzel_id(tmp_path):
    from log_reader import read_logs
    _write_entries(tmp_path / "openai.jsonl", [
        {"timestamp": "2026-02-27T10:00:00Z", "provider": "openai",
         "type": "request", "session_id": "s1", "heinzel_id": "hzl-A",
         "task_id": None, "data": {}},
        {"timestamp": "2026-02-27T10:01:00Z", "provider": "openai",
         "type": "request", "session_id": "s2", "heinzel_id": "hzl-B",
         "task_id": None, "data": {}},
    ])
    result = read_logs(str(tmp_path), "openai", heinzel_id="hzl-A")
    assert len(result) == 1
    assert result[0]["heinzel_id"] == "hzl-A"
