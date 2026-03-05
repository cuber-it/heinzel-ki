"""Search-Backends — SearXNG, DuckDuckGo, Fetch.

Alle Backends implementieren SearchBackend (ABC) mit einer einzigen Methode:
    async search(query, **kwargs) -> list[SearchResult]

Austausch über Config — WebSearchAddOn kennt nur das Interface.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod

import httpx

from .models import SearchResult

logger = logging.getLogger(__name__)

# Maximale Zeichen die vom Fetch-Inhalt ans LLM gehen
_FETCH_MAX_CHARS = 8000


# =============================================================================
# SearchBackend — Interface
# =============================================================================


class SearchBackend(ABC):
    """Abstrakte Basis für alle Search-Backends."""

    name: str = "abstract"

    @abstractmethod
    async def search(self, query: str, max_results: int = 5, **kwargs) -> list[SearchResult]:
        """Suche ausführen und Ergebnisse zurückgeben."""
        ...

    async def close(self) -> None:
        """Ressourcen freigeben. Standard: No-Op."""


# =============================================================================
# SearXNGBackend
# =============================================================================


class SearXNGBackend(SearchBackend):
    """Sucht via SearXNG JSON-API.

    Config:
        url: http://services:12004
        engines: [google, bing, duckduckgo]   # optional
        timeout: 10
    """

    name = "searxng"

    def __init__(
        self,
        url: str = "http://services:12004",
        engines: list[str] | None = None,
        timeout: int = 10,
    ) -> None:
        self._url = url.rstrip("/")
        self._engines = ",".join(engines) if engines else ""
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def search(self, query: str, max_results: int = 5, **kwargs) -> list[SearchResult]:
        """SearXNG /search?format=json aufrufen."""
        params: dict = {"q": query, "format": "json"}
        if self._engines:
            params["engines"] = self._engines

        # site:-Filter wenn target übergeben
        target = kwargs.get("site")
        if target:
            params["q"] = f"site:{target} {query}"

        try:
            client = await self._get_client()
            resp = await client.get(f"{self._url}/search", params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.error(f"[SearXNGBackend] Fehler: {exc}")
            return []

        results = []
        for item in data.get("results", [])[:max_results]:
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("content", ""),
                source="searxng",
            ))
        return results

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# =============================================================================
# DuckDuckGoBackend
# =============================================================================


class DuckDuckGoBackend(SearchBackend):
    """Sucht via DuckDuckGo HTML-Scraping (kein API-Key nötig).

    Fallback wenn SearXNG nicht verfügbar. Nutzt ddgs aus dem
    duckduckgo-search Paket falls installiert, sonst HTTP-Fallback.
    """

    name = "duckduckgo"

    def __init__(self, timeout: int = 10, max_results: int = 5) -> None:
        self._timeout = timeout
        self._default_max = max_results

    async def search(self, query: str, max_results: int = 5, **kwargs) -> list[SearchResult]:
        site = kwargs.get("site")
        effective_query = f"site:{site} {query}" if site else query

        # duckduckgo-search Paket bevorzugen
        try:
            from duckduckgo_search import DDGS
            import asyncio
            results = []
            loop = asyncio.get_event_loop()
            raw = await loop.run_in_executor(
                None,
                lambda: list(DDGS().text(effective_query, max_results=max_results))
            )
            for item in raw:
                results.append(SearchResult(
                    title=item.get("title", ""),
                    url=item.get("href", ""),
                    snippet=item.get("body", ""),
                    source="duckduckgo",
                ))
            return results
        except ImportError:
            pass

        # Fallback: DuckDuckGo HTML (sehr simpel)
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": effective_query},
                    headers={"User-Agent": "Mozilla/5.0"},
                )
                # Minimales Parsing — nur URLs aus results
                import re
                urls = re.findall(r'uddg=([^"&]+)', resp.text)[:max_results]
                from urllib.parse import unquote
                return [
                    SearchResult(title=unquote(u), url=unquote(u), source="duckduckgo")
                    for u in urls
                ]
        except Exception as exc:
            logger.error(f"[DuckDuckGoBackend] Fehler: {exc}")
            return []


# =============================================================================
# FetchBackend
# =============================================================================


class FetchBackend(SearchBackend):
    """Lädt eine URL direkt und extrahiert den Textinhalt.

    Kein Suchindex — für "sieh dir Seite X an"-Intents.
    Gibt ein einzelnes SearchResult mit dem Seiteninhalt zurück.
    """

    name = "fetch"

    def __init__(self, timeout: int = 15) -> None:
        self._timeout = timeout

    async def search(self, query: str, max_results: int = 1, **kwargs) -> list[SearchResult]:
        """query wird als URL interpretiert."""
        url = query.strip()
        if not url.startswith(("http://", "https://")):
            url = f"https://{url}"

        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                follow_redirects=True,
                headers={"User-Agent": "Mozilla/5.0 Heinzel/1.0"},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")

                if "html" in content_type:
                    text = _extract_text(resp.text)
                else:
                    text = resp.text

                # Kürzen damit der LLM-Kontext nicht explodiert
                if len(text) > _FETCH_MAX_CHARS:
                    text = text[:_FETCH_MAX_CHARS] + f"\n\n[... gekürzt auf {_FETCH_MAX_CHARS} Zeichen]"

                title = _extract_title(resp.text) or url
                return [SearchResult(title=title, url=url, snippet=text, source="fetch")]

        except Exception as exc:
            logger.error(f"[FetchBackend] Fehler beim Laden von '{url}': {exc}")
            return [SearchResult(
                title=f"Fehler: {url}",
                url=url,
                snippet=f"Seite konnte nicht geladen werden: {exc}",
                source="fetch",
            )]


# =============================================================================
# Hilfsfunktionen
# =============================================================================


def _extract_text(html: str) -> str:
    """Minimale Text-Extraktion aus HTML ohne externe Deps."""
    import re
    # Script/Style entfernen
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
    # Tags entfernen
    text = re.sub(r"<[^>]+>", " ", html)
    # Whitespace normalisieren
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _extract_title(html: str) -> str:
    """Titel aus HTML-Title-Tag extrahieren."""
    import re
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else ""


# =============================================================================
# Backend-Factory
# =============================================================================


def create_backend(name: str, config: dict) -> SearchBackend:
    """Backend aus Name und Config-Dict erstellen."""
    if name == "searxng":
        return SearXNGBackend(
            url=config.get("url", "http://services:12004"),
            engines=config.get("engines"),
            timeout=config.get("timeout", 10),
        )
    if name == "duckduckgo":
        return DuckDuckGoBackend(timeout=config.get("timeout", 10))
    if name == "fetch":
        return FetchBackend(timeout=config.get("timeout", 15))
    raise ValueError(f"Unbekanntes Backend: '{name}'")
