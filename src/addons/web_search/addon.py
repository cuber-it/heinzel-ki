"""WebSearchAddOn — Web-Suche, Site-Suche und Fetch für Heinzel.

Intent-Erkennung aus natürlicher Sprache:
    "suche nach X" / "durchsuche das web nach X"  → WEB_SEARCH
    "durchsuche die seite X nach Y"               → SITE_SEARCH (site:-Filter)
    "sieh dir X an" / "öffne X" / "lade X"        → FETCH

Ergebnisse landen in ctx.metadata['search_results'] als Textblöcke
und werden vom PromptBuilderAddOn ins Template eingebaut.

Konfiguration (heinzel.yaml):
    addons:
      web_search:
        backend: duckduckgo       # aktives Backend — beliebig: searxng, duckduckgo, fetch
        max_results: 5
        backends:
          searxng:
            url: http://services:12004
            engines: [google, bing]
            timeout: 10
          duckduckgo:
            timeout: 10
          fetch:
            timeout: 15
        targets:                  # benannte Direktziele
          uc-it: https://uc-it.de
          docs: https://docs.python.org

CLI-Kommandos (!search):
    !search status                → aktives Backend + verfügbare Targets
    !search backend duckduckgo    → Backend wechseln
    !search target uc-it          → nächste Suche auf Target begrenzen
    !search target off            → Target-Filter aufheben
"""

from __future__ import annotations

import logging
import re

from core.addon import AddOn
from core.models import PipelineContext, ContextHistory, AddOnResult

from .backends import SearchBackend, create_backend, FetchBackend
from .models import IntentType, SearchIntent, SearchResult

logger = logging.getLogger(__name__)

# =============================================================================
# Intent-Muster (Deutsch + Englisch, case-insensitive)
# =============================================================================

_FETCH_PATTERNS = [
    r"sieh?\s+dir\s+(?:die\s+)?(?:seite\s+)?(?P<url>\S+)\s+an",
    r"öffne?\s+(?:die\s+)?(?:seite\s+)?(?P<url>\S+)",
    r"lade?\s+(?:die\s+)?(?:seite\s+)?(?P<url>\S+)",
    r"zeig?\s+mir\s+(?:die\s+)?(?:seite\s+)?(?P<url>\S+)",
    r"fetch\s+(?P<url>\S+)",
    r"open\s+(?P<url>\S+)",
]

_SITE_SEARCH_PATTERNS = [
    r"durchsuche?\s+(?:die\s+)?seite\s+(?P<site>\S+)\s+(?:nach\s+)?(?P<query>.+)",
    r"suche?\s+auf\s+(?P<site>\S+)\s+(?:nach\s+)?(?P<query>.+)",
    r"search\s+(?:on\s+|site\s+)(?P<site>\S+)\s+(?:for\s+)?(?P<query>.+)",
]

_WEB_SEARCH_PATTERNS = [
    r"durchsuche?\s+(?:das\s+)?web\s+nach\s+(?P<query>.+)",
    r"suche?\s+(?:im\s+web\s+|online\s+)?nach\s+(?P<query>.+)",
    r"recherchiere?\s+(?:zu\s+|über\s+|nach\s+)?(?P<query>.+)",
    r"finde?\s+(?:mir\s+)?(?:infos?\s+(?:zu|über|nach)\s+)?(?P<query>.+)",
    r"search\s+(?:the\s+web\s+)?(?:for\s+)?(?P<query>.+)",
    r"look\s+up\s+(?P<query>.+)",
]


class WebSearchAddOn(AddOn):
    """Web-Suche mit Backend-Wechsel und Intent-Erkennung.

    Erkennt Web-Intents im User-Input bei ON_CONTEXT_BUILD,
    führt die Suche aus und injiziert Ergebnisse in ctx.metadata.
    """

    name = "web_search"
    version = "0.1.0"
    dependencies: list[str] = []

    def __init__(
        self,
        backend_name: str = "duckduckgo",  # Pflicht aus Config — kein bevorzugtes Backend
        max_results: int = 5,
        backends_config: dict | None = None,
        targets: dict[str, str] | None = None,
    ) -> None:
        self._backend_name = backend_name
        self._max_results = max_results
        self._backends_config: dict = backends_config or {}
        self._targets: dict[str, str] = targets or {}
        self._backend: SearchBackend | None = None
        self._active_target: str | None = None   # gesetzt via !search target

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def on_attach(self, heinzel) -> None:
        self._backend = self._make_backend(self._backend_name)
        # Als lokale Tools beim MCPToolsRouter registrieren
        # → LLM kann autonom entscheiden wann er sucht (wie Claude/ChatGPT)
        self._register_tools(heinzel)

        logger.info(
            f"[WebSearchAddOn] bereit — Backend: '{self._backend_name}', "
            f"Targets: {list(self._targets.keys())}"
        )

    async def on_detach(self, heinzel) -> None:
        self._unregister_tools(heinzel)
        if self._backend:
            await self._backend.close()
        self._backend = None

    # -------------------------------------------------------------------------
    # Pipeline Hook
    # -------------------------------------------------------------------------

    async def on_context_build(
        self, ctx: PipelineContext, history: ContextHistory | None = None
    ) -> AddOnResult:
        """Intent erkennen, Suche ausführen, Ergebnisse in metadata."""
        intent = parse_intent(ctx.parsed_input or "", self._targets)

        if intent.type == IntentType.NONE:
            return AddOnResult(modified_ctx=ctx)

        results = await self._execute(intent)
        if not results:
            return AddOnResult(modified_ctx=ctx)

        metadata = dict(ctx.metadata) if ctx.metadata else {}
        metadata["search_results"] = _format_results(results, intent)
        metadata["search_intent"] = intent.type.value
        metadata["search_query"] = intent.query

        return AddOnResult(modified_ctx=ctx.model_copy(update={"metadata": metadata}))

    # -------------------------------------------------------------------------
    # Öffentliche API
    # -------------------------------------------------------------------------

    async def search(
        self,
        query: str,
        max_results: int | None = None,
        site: str | None = None,
    ) -> list[SearchResult]:
        """Direkte Suche — für ReAct-Tools und !search Kommando."""
        if self._backend is None:
            logger.warning("[WebSearchAddOn] kein Backend — on_attach() nicht aufgerufen?")
            return []
        n = max_results or self._max_results
        kwargs = {}
        if site:
            kwargs["site"] = self._resolve_target(site)
        return await self._backend.search(query, max_results=n, **kwargs)

    async def fetch(self, url: str) -> list[SearchResult]:
        """URL direkt laden."""
        backend = FetchBackend()
        return await backend.search(url)

    def set_backend(self, name: str) -> None:
        """Backend zur Laufzeit wechseln."""
        self._backend = self._make_backend(name)
        self._backend_name = name
        logger.info(f"[WebSearchAddOn] Backend gewechselt zu '{name}'")

    def set_active_target(self, target: str | None) -> None:
        """Target-Filter setzen (None = aufheben)."""
        self._active_target = target

    def get_status(self) -> dict:
        """Status für !search status."""
        return {
            "backend": self._backend_name,
            "active_target": self._active_target,
            "targets": self._targets,
            "max_results": self._max_results,
        }

    def add_target(self, name: str, url: str) -> None:
        """Benanntes Target hinzufügen."""
        self._targets[name] = url
        logger.info(f"[WebSearchAddOn] Target hinzugefügt: '{name}' → {url}")

    # -------------------------------------------------------------------------
    # Interna
    # -------------------------------------------------------------------------

    async def _execute(self, intent: SearchIntent) -> list[SearchResult]:
        """Intent ausführen."""
        if intent.type == IntentType.FETCH:
            url = self._resolve_target(intent.target) or intent.target
            return await self.fetch(url)

        site = None
        if intent.type == IntentType.SITE_SEARCH:
            site = self._resolve_target(intent.target) or intent.target
        elif self._active_target:
            site = self._resolve_target(self._active_target) or self._active_target

        return await self.search(intent.query, site=site)

    def _resolve_target(self, name_or_url: str) -> str:
        """Target-Name → URL. Wenn keine Übereinstimmung: unverändert zurück."""
        return self._targets.get(name_or_url, name_or_url)

    def _make_backend(self, name: str) -> SearchBackend:
        config = self._backends_config.get(name, {})
        return create_backend(name, config)

    def _register_tools(self, heinzel) -> None:
        """web_search und fetch_page als lokale Tools beim MCPToolsRouter anmelden."""
        router = heinzel.addons.get("mcp_tools_router")
        if router is None:
            logger.debug("[WebSearchAddOn] kein MCPToolsRouter — Tool-Registrierung übersprungen")
            return

        async def _handle_web_search(args: dict) -> str:
            query = args.get("query", "")
            site = args.get("site")
            max_results = args.get("max_results", self._max_results)
            results = await self.search(query, max_results=max_results, site=site)
            if not results:
                return "Keine Ergebnisse gefunden."
            return "\n\n".join(r.as_text() for r in results)

        async def _handle_fetch_page(args: dict) -> str:
            url = args.get("url", "")
            results = await self.fetch(url)
            if not results:
                return "Seite konnte nicht geladen werden."
            return results[0].snippet

        router.register_local_handler(
            "local:web_search:search",
            _handle_web_search,
            description="Im Web suchen. Optional site= für Site-spezifische Suche.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Suchbegriff"},
                    "site": {"type": "string", "description": "Optional: Domain für Site-Suche"},
                    "max_results": {"type": "integer", "description": "Anzahl Ergebnisse (default 5)"},
                },
                "required": ["query"],
            },
        )
        router.register_local_handler(
            "local:web_search:fetch_page",
            _handle_fetch_page,
            description="Eine Webseite laden und ihren Textinhalt zurückgeben.",
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL der Seite"},
                },
                "required": ["url"],
            },
        )
        logger.info("[WebSearchAddOn] Tools registriert: local:web_search:search, local:web_search:fetch_page")

    def _unregister_tools(self, heinzel) -> None:
        """Tools beim MCPToolsRouter abmelden."""
        try:
            router = heinzel.addons.get("mcp_tools_router")
            if router:
                router.unregister_local_handler("local:web_search:search")
                router.unregister_local_handler("local:web_search:fetch_page")
        except Exception:
            pass


# =============================================================================
# Intent-Parser
# =============================================================================


def parse_intent(text: str, targets: dict[str, str] | None = None) -> SearchIntent:
    """Natürlichsprachlichen Input → SearchIntent parsen.

    Reihenfolge: FETCH > SITE_SEARCH > WEB_SEARCH > NONE
    """
    targets = targets or {}

    # FETCH
    for pattern in _FETCH_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            url = m.group("url").rstrip(".,;")
            return SearchIntent(
                type=IntentType.FETCH,
                target=url,
                query=url,
                raw_input=text,
            )

    # SITE_SEARCH
    for pattern in _SITE_SEARCH_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return SearchIntent(
                type=IntentType.SITE_SEARCH,
                target=m.group("site").rstrip(".,;"),
                query=m.group("query").strip(),
                raw_input=text,
            )

    # WEB_SEARCH
    for pattern in _WEB_SEARCH_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            return SearchIntent(
                type=IntentType.WEB_SEARCH,
                query=m.group("query").strip().rstrip(".,;?!"),
                raw_input=text,
            )

    return SearchIntent(type=IntentType.NONE, raw_input=text)


# =============================================================================
# Hilfsfunktionen
# =============================================================================


def _format_results(results: list[SearchResult], intent: SearchIntent) -> list[str]:
    """Ergebnisse als Textliste für ctx.metadata."""
    prefix = {
        IntentType.WEB_SEARCH: "Suchergebnis",
        IntentType.SITE_SEARCH: f"Ergebnis von {intent.target}",
        IntentType.FETCH: f"Inhalt von {intent.target}",
    }.get(intent.type, "Ergebnis")

    return [f"[{prefix}] {r.as_text()}" for r in results]
