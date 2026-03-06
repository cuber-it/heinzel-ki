"""Datenmodelle für WebSearchAddOn."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


# =============================================================================
# SearchResult
# =============================================================================


@dataclass
class SearchResult:
    """Einheitliches Ergebnis-Format für alle Backends."""

    title: str
    url: str
    snippet: str = ""
    source: str = ""          # Backend-Name: "searxng", "duckduckgo", "fetch"

    def as_text(self) -> str:
        """Kompakte Textdarstellung für LLM-Kontext."""
        lines = [f"[{self.title}]({self.url})"]
        if self.snippet:
            lines.append(self.snippet)
        return "\n".join(lines)


# =============================================================================
# SearchIntent — was will der Nutzer?
# =============================================================================


class IntentType(str, enum.Enum):
    WEB_SEARCH = "web_search"     # "suche nach X" → normales Backend
    SITE_SEARCH = "site_search"   # "durchsuche seite X nach Y" → site:-Filter
    FETCH = "fetch"               # "sieh dir X an" → Seite direkt laden
    NONE = "none"                 # kein Web-Intent erkannt


@dataclass
class SearchIntent:
    """Geparster Intent aus dem User-Input."""

    type: IntentType = IntentType.NONE
    query: str = ""               # Suchbegriff
    target: str = ""              # URL oder Target-Name bei SITE_SEARCH / FETCH
    raw_input: str = ""           # Original-Input zur Fehleranalyse
