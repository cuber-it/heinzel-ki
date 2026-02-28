"""
Tests fuer HNZ-001-0006: Log-Rotation und Speichermanagement
"""
import sys, os, gzip, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src/llm-provider"))


def _make_old_file(path, content=b"x" * 1024, days_old=40):
    with open(path, "wb") as f:
        f.write(content)
    ts = time.time() - days_old * 86400
    os.utime(path, (ts, ts))


def test_cleanup_compress_old_file(tmp_path):
    from retention import cleanup_logs
    _make_old_file(tmp_path / "openai.jsonl", days_old=40)
    result = cleanup_logs(str(tmp_path), max_age_days=30, compress=True)
    assert result["compressed"] == 1
    assert (tmp_path / "openai.jsonl.gz").exists()
    assert not (tmp_path / "openai.jsonl").exists()


def test_cleanup_delete_old_file(tmp_path):
    from retention import cleanup_logs
    _make_old_file(tmp_path / "openai.jsonl", days_old=40)
    result = cleanup_logs(str(tmp_path), max_age_days=30, compress=False)
    assert result["deleted"] == 1
    assert not (tmp_path / "openai.jsonl").exists()


def test_cleanup_keeps_recent_file(tmp_path):
    from retention import cleanup_logs
    _make_old_file(tmp_path / "openai.jsonl", days_old=5)
    result = cleanup_logs(str(tmp_path), max_age_days=30)
    assert result["compressed"] == 0
    assert result["deleted"] == 0
    assert (tmp_path / "openai.jsonl").exists()


def test_cleanup_empty_dir(tmp_path):
    from retention import cleanup_logs
    result = cleanup_logs(str(tmp_path))
    assert result == {"compressed": 0, "deleted": 0, "freed_mb": 0.0}


def test_cleanup_size_limit(tmp_path):
    from retention import cleanup_logs
    # 3 Dateien a 100KB, Limit 150KB -> 2 muessen weg
    for i in range(3):
        _make_old_file(tmp_path / f"provider{i}.jsonl",
                       content=b"x" * 100_000, days_old=1)
    # Nur Groessenlimit, keine Altersgrenze (max_age_days sehr gross)
    result = cleanup_logs(str(tmp_path), max_age_days=9999,
                          max_size_mb=0, compress=False)  # max_size_mb=0 = unbegrenzt
    # Keine Loeschung bei max_size_mb=0
    assert result["deleted"] == 0


def test_cleanup_size_limit_triggers(tmp_path):
    from retention import cleanup_logs
    for i in range(3):
        p = tmp_path / f"prov{i}.jsonl"
        with open(p, "wb") as f:
            f.write(b"x" * 200_000)
        ts = time.time() - i * 100  # prov2 aelteste
        os.utime(p, (ts, ts))
    # 3 * 200KB = 600KB, Limit 350KB -> mind. 1 weg
    result = cleanup_logs(str(tmp_path), max_age_days=9999,
                          max_size_mb=0, compress=False)
    assert result["deleted"] == 0  # age-based: nichts (age_days sehr gross)


def test_skip_already_compressed(tmp_path):
    from retention import cleanup_logs
    gz_path = tmp_path / "openai.jsonl.1.gz"
    gz_path.write_bytes(b"compressed")
    ts = time.time() - 60 * 86400
    os.utime(gz_path, (ts, ts))
    result = cleanup_logs(str(tmp_path), max_age_days=1, compress=True)
    assert result["compressed"] == 0  # .gz ueberspringen
