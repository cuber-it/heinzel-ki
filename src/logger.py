"""
H.E.I.N.Z.E.L. Provider — Dialog-Logger

Schreibt Request/Response als JSONL mit Session-Kontext.
Jeder Eintrag enthält: timestamp, provider, type, session_id,
heinzel_id, task_id, endpoint, payload/content.

Speicherformat: JSONL, eine Zeile pro Eintrag.
Rotation: 10 MB pro Datei, 5 Backups.
Dateipfad: {log_dir}/{provider_name}.jsonl
"""
import logging
import json
import os
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from typing import Any, Optional


class RequestResponseLogger:
    def __init__(self, provider_name: str, log_dir: str = "/data", enabled: bool = True):
        self.provider_name = provider_name
        self.log_dir = log_dir
        self.enabled = enabled
        self.logger: Optional[logging.Logger] = None

        if not enabled:
            return

        os.makedirs(log_dir, exist_ok=True)
        self.logger = logging.getLogger(f"provider.{provider_name}")
        self.logger.setLevel(logging.INFO)
        if not self.logger.handlers:
            log_file = os.path.join(log_dir, f"{provider_name}.jsonl")
            handler = RotatingFileHandler(
                log_file, maxBytes=10 * 1024 * 1024, backupCount=5)
            handler.setFormatter(logging.Formatter("%(message)s"))
            self.logger.addHandler(handler)

    def _log_entry(self, entry_type: str, data: Any,
                   session_id: Optional[str] = None,
                   heinzel_id: Optional[str] = None,
                   task_id: Optional[str] = None) -> None:
        if not self.enabled or self.logger is None:
            return
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "provider": self.provider_name,
            "type": entry_type,
            "session_id": session_id,
            "heinzel_id": heinzel_id,
            "task_id": task_id,
            "data": data,
        }
        self.logger.info(json.dumps(entry, ensure_ascii=False))

    def log_request(self, endpoint: str, payload: dict,
                    session_id: Optional[str] = None,
                    heinzel_id: Optional[str] = None,
                    task_id: Optional[str] = None) -> None:
        self._log_entry("request",
                        {"endpoint": endpoint, "payload": payload},
                        session_id=session_id, heinzel_id=heinzel_id, task_id=task_id)

    def log_response(self, endpoint: str, status: int, content: Any,
                     session_id: Optional[str] = None,
                     heinzel_id: Optional[str] = None,
                     task_id: Optional[str] = None) -> None:
        self._log_entry("response",
                        {"endpoint": endpoint, "status": status, "content": content},
                        session_id=session_id, heinzel_id=heinzel_id, task_id=task_id)

    def log_error(self, endpoint: str, error: str,
                  session_id: Optional[str] = None,
                  heinzel_id: Optional[str] = None,
                  task_id: Optional[str] = None) -> None:
        self._log_entry("error",
                        {"endpoint": endpoint, "error": error},
                        session_id=session_id, heinzel_id=heinzel_id, task_id=task_id)
