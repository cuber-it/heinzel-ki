"""
H.E.I.N.Z.E.L. Provider — Session Store

Isoliert session-spezifische Parameter (model, temperature, max_tokens)
pro session_id. Thread-safe für asyncio (kein Lock nötig da single-threaded).

Maximale Anzahl Sessions: 1000 (LRU-ähnlich, älteste wird verdrängt).
"""
from collections import OrderedDict
from typing import Any

MAX_SESSIONS = 1000
_DEFAULT_PARAMS: dict[str, Any] = {
    "model": None,
    "temperature": None,
    "max_tokens": None,
}


class SessionStore:
    def __init__(self, max_sessions: int = MAX_SESSIONS):
        self._max = max_sessions
        self._store: OrderedDict[str, dict] = OrderedDict()

    def get(self, session_id: str) -> dict:
        """Gibt session-spezifische Params zurück. Erstellt bei Bedarf."""
        if session_id not in self._store:
            if len(self._store) >= self._max:
                self._store.popitem(last=False)  # Älteste raus
            self._store[session_id] = dict(_DEFAULT_PARAMS)
        self._store.move_to_end(session_id)
        return self._store[session_id]

    def delete(self, session_id: str) -> None:
        self._store.pop(session_id, None)

    def count(self) -> int:
        return len(self._store)

    def session_ids(self) -> list[str]:
        return list(self._store.keys())
