"""_DialogLogger — internes Dialoglogging fuer Runner.

Schreibt den kompletten Dialog eines Heinzel in eine Textdatei.
Package-intern: nicht in __init__.py exportiert.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path


class _DialogLogger:
    """Schreibt den kompletten Dialog eines Heinzel in eine Textdatei.

    Immer aktiv: USER-Eingaben und HEINZEL-Antworten.
    Optional: AddOn-Aufrufe (log_addons) und MCP-Nutzung (log_mcp).

    Dateiname: {log_dir}/{agent_id}.log
    Format:    [ISO-Timestamp] ROLE: Text
    """

    def __init__(self, agent_id: str, cfg: dict) -> None:
        log_cfg = cfg.get("logging", {})
        log_dir = Path(log_cfg.get("log_dir", "./logs"))
        self.log_addons: bool = bool(log_cfg.get("log_addons", False))
        self.log_mcp: bool = bool(log_cfg.get("log_mcp", False))
        self._turn_nr: int = 0    # Laufende Nummer: USER+HEINZEL teilen sich eine Nr.
        self._path: Path | None = None
        self._file = None

        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            self._path = log_dir / f"{agent_id}.log"
            self._file = open(self._path, "a", encoding="utf-8", buffering=1)
            self._write(f"=== Session Start -- Agent {agent_id} ===")
        except Exception as exc:
            logging.getLogger(__name__).error(
                "DialogLogger: Datei konnte nicht geoeffnet werden: %s", exc
            )

    @property
    def log_path(self) -> Path | None:
        """Pfad zur Logdatei — fuer CLI-Ausgabe und !history."""
        return self._path

    def _write(self, line: str) -> None:
        if self._file is None:
            return
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        try:
            self._file.write(f"[{ts}] {line}\n")
        except Exception as exc:
            logging.getLogger(__name__).error("DialogLogger Schreibfehler: %s", exc)

    def log_user(self, message: str) -> None:
        self._turn_nr += 1
        self._write(f"#{self._turn_nr:04d} USER: {message}")

    def log_heinzel(self, response: str) -> None:
        self._write(f"#{self._turn_nr:04d} HEINZEL: {response}")

    def log_addon(self, addon_name: str, hook: str, had_changes: bool) -> None:
        if not self.log_addons:
            return
        marker = "*" if had_changes else " "
        self._write(f"  [{marker}ADDON] {addon_name} @ {hook}")

    def log_mcp_request(self, tool_name: str, args: dict) -> None:
        if not self.log_mcp:
            return
        self._write(f"  [MCP>] {tool_name}({args})")

    def log_mcp_result(self, tool_name: str, ok: bool) -> None:
        if not self.log_mcp:
            return
        status = "OK" if ok else "ERR"
        self._write(f"  [MCP<] {tool_name} [{status}]")

    def close(self) -> None:
        if self._file is not None:
            try:
                self._write("=== Session End ===")
                self._file.close()
            except Exception:
                pass
            self._file = None
