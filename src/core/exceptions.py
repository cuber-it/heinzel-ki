"""Heinzel Exception-Hierarchie.

Alle Exceptions sind direkt aus core.exceptions importierbar.
"""

from __future__ import annotations


class HeinzelError(Exception):
    """Basis-Exception für alle Heinzel-Fehler."""

    def __init__(self, message: str, details: str | None = None) -> None:
        self.message = message
        self.details = details
        super().__init__(message)

    def __str__(self) -> str:
        if self.details:
            return f"{self.message} — {self.details}"
        return self.message


# --- Provider ---

class ProviderError(HeinzelError):
    """Fehler bei der LLM-Provider-Kommunikation."""

    def __init__(self, message: str, status_code: int | None = None, detail: str | None = None) -> None:
        self.status_code = status_code
        super().__init__(message, detail)

    def __str__(self) -> str:
        parts = [self.message]
        if self.status_code:
            parts.append(f"status={self.status_code}")
        if self.details:
            parts.append(self.details)
        return " | ".join(parts)


class ContextLengthExceededError(ProviderError):
    """Kontextfenster des Modells erschoepft (HTTP 400 vom Provider).

    Wird in _call_provider() gefangen um:
    1. Das Limit zu merken (limit_discovered)
    2. compact() auszuloesen
    3. Den Request zu wiederholen

    tokens_sent      -- geschaetzte Token die wir gesendet haben
    limit_discovered -- vom Provider gemeldetes Limit (falls vorhanden)
    """

    def __init__(
        self,
        message: str,
        tokens_sent: int = 0,
        limit_discovered: int | None = None,
        detail: str | None = None,
    ) -> None:
        self.tokens_sent = tokens_sent
        self.limit_discovered = limit_discovered
        super().__init__(message, status_code=400, detail=detail)

    def __str__(self) -> str:
        parts = [self.message, f"tokens_sent={self.tokens_sent}"]
        if self.limit_discovered:
            parts.append(f"limit={self.limit_discovered}")
        return " | ".join(parts)


# --- Datenbank ---

class DatabaseError(HeinzelError):
    """Fehler bei Datenbankoperationen."""

    def __init__(self, message: str, query: str | None = None, original_exception: Exception | None = None) -> None:
        self.query = query
        self.original_exception = original_exception
        details = str(original_exception) if original_exception else None
        super().__init__(message, details)


# --- Konfiguration ---

class ConfigError(HeinzelError):
    """Fehler bei der Konfiguration."""

    def __init__(self, message: str, missing_key: str | None = None, config_path: str | None = None) -> None:
        self.missing_key = missing_key
        self.config_path = config_path
        details = f"key={missing_key}" if missing_key else None
        super().__init__(message, details)


# --- Session ---

class SessionError(HeinzelError):
    """Fehler bei Session-Operationen."""

    def __init__(self, message: str, session_id: str | None = None) -> None:
        self.session_id = session_id
        details = f"session_id={session_id}" if session_id else None
        super().__init__(message, details)


class SessionNotFoundError(SessionError):
    """Session mit der angegebenen ID nicht gefunden."""
    pass


# --- Strategy ---

class StrategyError(HeinzelError):
    """Fehler bei der Reasoning-Strategie."""

    def __init__(self, message: str, strategy_name: str | None = None) -> None:
        self.strategy_name = strategy_name
        details = f"strategy={strategy_name}" if strategy_name else None
        super().__init__(message, details)


# --- AddOn ---

class AddOnError(HeinzelError):
    """Basis-Exception für AddOn-Fehler."""

    def __init__(
        self,
        message: str,
        addon_name: str | None = None,
        hook_point: str | None = None,
        original_exception: Exception | None = None,
    ) -> None:
        self.addon_name = addon_name
        self.hook_point = hook_point
        self.original_exception = original_exception
        details = str(original_exception) if original_exception else None
        super().__init__(message, details)

    def __str__(self) -> str:
        parts = [self.message]
        if self.addon_name:
            parts.append(f"addon={self.addon_name}")
        if self.hook_point:
            parts.append(f"hook={self.hook_point}")
        if self.details:
            parts.append(self.details)
        return " | ".join(parts)


class AddOnDependencyError(AddOnError):
    """Eine AddOn-Abhängigkeit fehlt."""
    pass


class AddOnLoadError(AddOnError):
    """Fehler beim Laden eines AddOn."""
    pass


class CircuitOpenError(AddOnError):
    """Circuit-Breaker für ein AddOn ist offen."""

    def __init__(self, message: str, addon_name: str | None = None, failure_count: int = 0) -> None:
        self.failure_count = failure_count
        super().__init__(message, addon_name=addon_name)

    def __str__(self) -> str:
        parts = [self.message]
        if self.addon_name:
            parts.append(f"addon={self.addon_name}")
        parts.append(f"failures={self.failure_count}")
        return " | ".join(parts)


__all__ = [
    "HeinzelError",
    "ProviderError",
    "DatabaseError",
    "ConfigError",
    "SessionError",
    "SessionNotFoundError",
    "StrategyError",
    "AddOnError",
    "AddOnDependencyError",
    "AddOnLoadError",
    "CircuitOpenError",
    "ContextLengthExceededError",
]
