"""WebSearchAddOn — Web-Suche, Site-Suche und Fetch."""

from .addon import WebSearchAddOn, parse_intent
from .backends import SearchBackend, SearXNGBackend, DuckDuckGoBackend, FetchBackend, create_backend
from .models import SearchResult, SearchIntent, IntentType

__all__ = [
    "WebSearchAddOn",
    "parse_intent",
    "SearchBackend",
    "SearXNGBackend",
    "DuckDuckGoBackend",
    "FetchBackend",
    "create_backend",
    "SearchResult",
    "SearchIntent",
    "IntentType",
]
