"""DialogLoggerAddOn — JSONL-Logging aller Dialog-Events.

Jeden Dialog vollständig loggen — unabhängig von DB.
Crash-Safety: flush() nach jedem Write.
Nie wieder einen Dialog verlieren.

Pfad: {log_dir}/{heinzel_id}/{YYYY-MM-DD}/{session_id}.jsonl

Format pro Zeile:
    {
        "ts": "2026-03-05T14:23:01.123456",
        "event": "input",
        "session_id": "...",
        "heinzel_id": "riker",
        "content": "...",
        "metadata": {}
    }

Konfiguration (heinzel.yaml):
    addons:
      dialog_logger:
        log_dir: logs/dialogs
        rotation_size_mb: 10
        retention_days: 90

Importpfad:
    from addons.dialog_logger import DialogLoggerAddOn
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, date, timedelta, timezone
from pathlib import Path
from typing import Any

from core.addon import AddOn
from core.models import AddOnResult, PipelineContext, ContextHistory

logger = logging.getLogger(__name__)

# Event-Typen
EVT_INPUT = "input"
EVT_OUTPUT = "output"
EVT_THINKING = "thinking"
EVT_TOOL_REQUEST = "tool_request"
EVT_TOOL_RESULT = "tool_result"
EVT_TOOL_ERROR = "tool_error"
EVT_ERROR = "error"


class DialogLoggerAddOn(AddOn):
    """JSONL-Logger für alle Dialog-Events.

    Hooks (alle passthrough — verändern Context nicht):
        on_input          → User-Nachricht sofort loggen
        on_output         → Antwort loggen
        on_thinking_step  → Reasoning-Schritt loggen
        on_tool_request   → Tool-Call loggen
        on_tool_result    → Tool-Ergebnis loggen
        on_error          → Fehler loggen
    """

    name = "dialog_logger"
    version = "0.1.0"
    dependencies: list[str] = []

    def __init__(
        self,
        log_dir: str = "logs/dialogs",
        rotation_size_mb: float = 10.0,
        retention_days: int = 90,
    ) -> None:
        self._log_dir = Path(log_dir)
        self._rotation_size_bytes = int(rotation_size_mb * 1024 * 1024)
        self._retention_days = retention_days
        self._heinzel_id: str = "heinzel"
        self._write_lock = asyncio.Lock()

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def on_attach(self, heinzel) -> None:
        try:
            self._heinzel_id = heinzel.config.agent.name.lower().replace(" ", "-")
        except Exception:
            pass
        self._log_dir.mkdir(parents=True, exist_ok=True)
        await self._cleanup_old_logs()
        logger.info(
            f"[DialogLoggerAddOn] bereit — "
            f"log_dir='{self._log_dir}', retention={self._retention_days}d"
        )

    async def on_detach(self, heinzel) -> None:
        pass

    # -------------------------------------------------------------------------
    # Hooks — alle Passthrough
    # -------------------------------------------------------------------------

    async def on_input(
        self, ctx: PipelineContext, history: ContextHistory | None = None
    ) -> AddOnResult:
        await self._log(
            event=EVT_INPUT,
            session_id=ctx.session_id,
            content=ctx.parsed_input or "",
            metadata={},
        )
        return AddOnResult(modified_ctx=ctx)

    async def on_output(
        self, ctx: PipelineContext, history: ContextHistory | None = None
    ) -> AddOnResult:
        await self._log(
            event=EVT_OUTPUT,
            session_id=ctx.session_id,
            content=ctx.response or "",
            metadata={},
        )
        return AddOnResult(modified_ctx=ctx)

    async def on_thinking_step(
        self, ctx: PipelineContext, history: ContextHistory | None = None
    ) -> AddOnResult:
        thinking = ""
        if ctx.metadata:
            thinking = ctx.metadata.get("thinking_step", "")
        await self._log(
            event=EVT_THINKING,
            session_id=ctx.session_id,
            content=thinking,
            metadata={},
        )
        return AddOnResult(modified_ctx=ctx)

    async def on_tool_request(
        self, ctx: PipelineContext, history: ContextHistory | None = None
    ) -> AddOnResult:
        tools = [
            {"tool": tr.tool_name, "args": tr.args}
            for tr in (ctx.tool_requests or [])
        ]
        await self._log(
            event=EVT_TOOL_REQUEST,
            session_id=ctx.session_id,
            content="",
            metadata={"tools": tools},
        )
        return AddOnResult(modified_ctx=ctx)

    async def on_tool_result(
        self, ctx: PipelineContext, history: ContextHistory | None = None
    ) -> AddOnResult:
        results = [
            {"call_id": tr.call_id, "result": str(tr.result), "error": tr.error}
            for tr in (ctx.tool_results or [])
        ]
        await self._log(
            event=EVT_TOOL_RESULT,
            session_id=ctx.session_id,
            content="",
            metadata={"results": results},
        )
        return AddOnResult(modified_ctx=ctx)

    async def on_error(
        self, ctx: PipelineContext, history: ContextHistory | None = None
    ) -> AddOnResult:
        error = ctx.metadata.get("error", "") if ctx.metadata else ""
        await self._log(
            event=EVT_ERROR,
            session_id=ctx.session_id,
            content=str(error),
            metadata={},
        )
        return AddOnResult(modified_ctx=ctx)

    # -------------------------------------------------------------------------
    # Öffentliche API
    # -------------------------------------------------------------------------

    def read_session_log(self, session_id: str) -> list[dict]:
        """Alle Log-Einträge einer Session lesen."""
        results = []
        for jsonl_path in self._log_dir.rglob(f"{session_id}.jsonl"):
            results.extend(_read_jsonl(jsonl_path))
        return results

    def search_logs(
        self,
        query: str,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[dict]:
        """Logs nach Freitext und Datumsbereich durchsuchen."""
        results = []
        query_lower = query.lower()

        for jsonl_path in sorted(self._log_dir.rglob("*.jsonl")):
            # Datum aus Pfad-Struktur prüfen: .../YYYY-MM-DD/session.jsonl
            try:
                day_str = jsonl_path.parent.name
                day = date.fromisoformat(day_str)
            except ValueError:
                day = None

            if day and date_from and day < date_from:
                continue
            if day and date_to and day > date_to:
                continue

            for entry in _read_jsonl(jsonl_path):
                content = entry.get("content", "").lower()
                if query_lower in content:
                    results.append(entry)

        return results

    # -------------------------------------------------------------------------
    # Interna
    # -------------------------------------------------------------------------

    async def _log(
        self,
        event: str,
        session_id: str,
        content: str,
        metadata: dict,
    ) -> None:
        """Event in JSONL-Datei schreiben — crash-safe via flush + fsync."""
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "session_id": session_id,
            "heinzel_id": self._heinzel_id,
            "content": content,
            "metadata": metadata,
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"
        path = self._log_path(session_id)

        async with self._write_lock:
            path.parent.mkdir(parents=True, exist_ok=True)
            # Rotation prüfen
            if path.exists() and path.stat().st_size >= self._rotation_size_bytes:
                path = self._rotate(path, session_id)

            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
                os.fsync(f.fileno())  # Crash-Safety

    def _log_path(self, session_id: str) -> Path:
        today = date.today().isoformat()
        return self._log_dir / self._heinzel_id / today / f"{session_id}.jsonl"

    def _rotate(self, path: Path, session_id: str) -> Path:
        """Aktuelle Datei umbenennen, neue zurückgeben."""
        ts = datetime.now(timezone.utc).strftime("%H%M%S")
        rotated = path.with_name(f"{session_id}.{ts}.jsonl")
        path.rename(rotated)
        logger.info(f"[DialogLoggerAddOn] rotiert: {rotated.name}")
        return path  # Neue leere Datei

    async def _cleanup_old_logs(self) -> None:
        """Dateien älter als retention_days löschen."""
        if self._retention_days <= 0:
            return
        cutoff = date.today() - timedelta(days=self._retention_days)
        removed = 0
        for jsonl_path in self._log_dir.rglob("*.jsonl"):
            try:
                day_str = jsonl_path.parent.name
                day = date.fromisoformat(day_str)
                if day < cutoff:
                    jsonl_path.unlink()
                    removed += 1
            except (ValueError, OSError):
                pass
        if removed:
            logger.info(f"[DialogLoggerAddOn] {removed} alte Log-Datei(en) gelöscht")


# =============================================================================
# Hilfsfunktionen
# =============================================================================


def _read_jsonl(path: Path) -> list[dict]:
    """JSONL-Datei lesen — fehlerhafte Zeilen überspringen."""
    results = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                results.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    except OSError:
        pass
    return results
