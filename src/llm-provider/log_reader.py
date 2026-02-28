"""
H.E.I.N.Z.E.L. Provider — Log-Reader

Liest und filtert Dialog-Logs aus JSONL-Files.
Unterstützt Filter nach session_id, heinzel_id, Zeitraum, Typ.

Alle rotierten Dateien (.jsonl, .jsonl.1 bis .jsonl.5) werden einbezogen.
"""
import json
import os
import glob
from datetime import datetime, timezone
from typing import Optional


def _log_files(log_dir: str, provider: str) -> list[str]:
    """Alle JSONL-Dateien für einen Provider, neueste zuerst."""
    base = os.path.join(log_dir, f"{provider}.jsonl")
    files = sorted(glob.glob(base + "*"), reverse=True)
    # Hauptdatei zuerst, dann .1, .2 ...
    result = []
    if os.path.exists(base):
        result.append(base)
    for f in files:
        if f != base:
            result.append(f)
    return result


def read_logs(
    log_dir: str,
    provider: str,
    session_id: Optional[str] = None,
    heinzel_id: Optional[str] = None,
    task_id: Optional[str] = None,
    entry_type: Optional[str] = None,   # request|response|error
    since: Optional[str] = None,        # ISO-Datetime
    until: Optional[str] = None,        # ISO-Datetime
    limit: int = 100,
) -> list[dict]:
    """
    Liest und filtert Log-Einträge.
    Gibt maximal `limit` Einträge zurück, neueste zuerst.
    """
    since_dt = _parse_dt(since)
    until_dt = _parse_dt(until)
    results = []

    for filepath in _log_files(log_dir, provider):
        if not os.path.exists(filepath):
            continue
        try:
            lines = open(filepath, encoding="utf-8").readlines()
        except Exception:
            continue
        # Neueste zuerst innerhalb der Datei
        for line in reversed(lines):
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Filter
            if session_id and entry.get("session_id") != session_id:
                continue
            if heinzel_id and entry.get("heinzel_id") != heinzel_id:
                continue
            if task_id and entry.get("task_id") != task_id:
                continue
            if entry_type and entry.get("type") != entry_type:
                continue
            ts = _parse_dt(entry.get("timestamp"))
            if since_dt and ts and ts < since_dt:
                continue
            if until_dt and ts and ts > until_dt:
                continue

            results.append(entry)
            if len(results) >= limit:
                return results

    return results


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        return None
