"""pytest conftest — setzt LOG_DIR auf /tmp für Tests außerhalb Docker."""
import os
import pytest

@pytest.fixture(autouse=True)
def set_log_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LOG_DIR", str(tmp_path))
    monkeypatch.delenv("DATABASE_URL", raising=False)
