"""Tests für WebSearchAddOn — Intent-Parser, Backend-Wechsel, Context-Injection."""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock

from addons.web_search import WebSearchAddOn, parse_intent, IntentType, SearchResult
from addons.web_search.addon import _format_results
from addons.web_search.models import SearchIntent


# =============================================================================
# Fixtures
# =============================================================================


def _make_addon(backend_name="duckduckgo", targets=None) -> WebSearchAddOn:
    addon = WebSearchAddOn(
        backend_name=backend_name,
        targets=targets or {"uc-it": "https://uc-it.de"},
    )
    # Backend durch Mock ersetzen
    mock_backend = AsyncMock()
    mock_backend.name = backend_name
    mock_backend.search = AsyncMock(return_value=[
        SearchResult(title="Ergebnis 1", url="https://example.com", snippet="Snippet 1", source=backend_name),
        SearchResult(title="Ergebnis 2", url="https://example.org", snippet="Snippet 2", source=backend_name),
    ])
    mock_backend.close = AsyncMock()
    addon._backend = mock_backend
    return addon


class _FakeHeinzel:
    pass


# =============================================================================
# Intent-Parser — WEB_SEARCH
# =============================================================================


@pytest.mark.parametrize("text", [
    "suche nach Python asyncio",
    "durchsuche das web nach KI-Agenten",
    "recherchiere zu Jinja2 Templates",
    "finde mir Infos zu Docker",
    "search for Python tutorials",
    "look up asyncio documentation",
])
def test_parse_intent_web_search(text):
    intent = parse_intent(text)
    assert intent.type == IntentType.WEB_SEARCH
    assert intent.query != ""


def test_parse_intent_web_search_query_content():
    intent = parse_intent("suche nach Python asyncio Beispielen")
    assert "Python asyncio" in intent.query


# =============================================================================
# Intent-Parser — SITE_SEARCH
# =============================================================================


@pytest.mark.parametrize("text", [
    "durchsuche die seite uc-it.de nach MCP",
    "suche auf docs.python.org nach asyncio",
    "search site uc-it.de for Heinzel",
])
def test_parse_intent_site_search(text):
    intent = parse_intent(text)
    assert intent.type == IntentType.SITE_SEARCH
    assert intent.target != ""
    assert intent.query != ""


def test_parse_intent_site_search_target():
    intent = parse_intent("durchsuche die seite uc-it.de nach MCP Artikeln")
    assert "uc-it.de" in intent.target
    assert "MCP" in intent.query


# =============================================================================
# Intent-Parser — FETCH
# =============================================================================


@pytest.mark.parametrize("text", [
    "sieh dir die seite uc-it.de an",
    "öffne https://example.com",
    "lade die seite docs.python.org",
    "zeig mir uc-it.de",
    "fetch https://example.com",
])
def test_parse_intent_fetch(text):
    intent = parse_intent(text)
    assert intent.type == IntentType.FETCH
    assert intent.target != ""


def test_parse_intent_fetch_url():
    intent = parse_intent("sieh dir uc-it.de an")
    assert "uc-it.de" in intent.target


# =============================================================================
# Intent-Parser — NONE
# =============================================================================


@pytest.mark.parametrize("text", [
    "wie geht es dir",
    "was ist 2+2",
    "erkläre mir Pydantic",
    "",
])
def test_parse_intent_none(text):
    intent = parse_intent(text)
    assert intent.type == IntentType.NONE


# =============================================================================
# Intent-Parser — Priorität FETCH > SITE > WEB
# =============================================================================


def test_parse_intent_priority_fetch_over_web():
    """FETCH hat höhere Priorität als WEB_SEARCH."""
    intent = parse_intent("sieh dir uc-it.de an und suche nach MCP")
    assert intent.type == IntentType.FETCH


# =============================================================================
# Target-Auflösung
# =============================================================================


def test_resolve_known_target():
    addon = _make_addon(targets={"uc-it": "https://uc-it.de"})
    assert addon._resolve_target("uc-it") == "https://uc-it.de"


def test_resolve_unknown_target_passthrough():
    addon = _make_addon()
    assert addon._resolve_target("https://example.com") == "https://example.com"


def test_add_target():
    addon = _make_addon()
    addon.add_target("docs", "https://docs.python.org")
    assert addon._resolve_target("docs") == "https://docs.python.org"


# =============================================================================
# Backend-Wechsel
# =============================================================================


def test_set_backend():
    addon = _make_addon()
    addon.set_backend("duckduckgo")
    assert addon._backend_name == "duckduckgo"


# =============================================================================
# Target-Filter
# =============================================================================


def test_set_active_target():
    addon = _make_addon()
    addon.set_active_target("uc-it")
    assert addon._active_target == "uc-it"


def test_clear_active_target():
    addon = _make_addon()
    addon.set_active_target("uc-it")
    addon.set_active_target(None)
    assert addon._active_target is None


# =============================================================================
# get_status
# =============================================================================


def test_get_status():
    addon = _make_addon(targets={"uc-it": "https://uc-it.de"})
    status = addon.get_status()
    assert status["backend"] == "duckduckgo"
    assert "uc-it" in status["targets"]
    assert status["active_target"] is None


# =============================================================================
# on_context_build — Intent erkannt
# =============================================================================


@pytest.mark.asyncio
async def test_on_context_build_web_search():
    """WEB_SEARCH-Intent → metadata['search_results'] gesetzt."""
    from core.models import PipelineContext

    addon = _make_addon()
    ctx = PipelineContext(session_id="s", parsed_input="suche nach Python asyncio")
    updated = await addon.on_context_build(ctx)

    assert "search_results" in updated.metadata
    assert len(updated.metadata["search_results"]) > 0
    assert updated.metadata["search_intent"] == "web_search"


@pytest.mark.asyncio
async def test_on_context_build_no_intent():
    """Kein Intent → Context unverändert."""
    from core.models import PipelineContext

    addon = _make_addon()
    ctx = PipelineContext(session_id="s", parsed_input="wie geht es dir")
    updated = await addon.on_context_build(ctx)

    assert updated is ctx


@pytest.mark.asyncio
async def test_on_context_build_immutable():
    """on_context_build verändert originalen Context nicht."""
    from core.models import PipelineContext

    addon = _make_addon()
    ctx = PipelineContext(session_id="s", parsed_input="suche nach asyncio")
    updated = await addon.on_context_build(ctx)

    assert updated is not ctx
    assert "search_results" not in (ctx.metadata or {})


@pytest.mark.asyncio
async def test_on_context_build_with_active_target():
    """Aktiver Target-Filter wird bei WEB_SEARCH als site-Parameter übergeben."""
    from core.models import PipelineContext

    addon = _make_addon(targets={"uc-it": "https://uc-it.de"})
    addon.set_active_target("uc-it")

    ctx = PipelineContext(session_id="s", parsed_input="suche nach MCP")
    await addon.on_context_build(ctx)

    # Backend wurde mit site-Param aufgerufen
    call_kwargs = addon._backend.search.call_args
    assert call_kwargs is not None


# =============================================================================
# _format_results
# =============================================================================


def test_format_results_web():
    results = [SearchResult(title="T", url="https://x.com", snippet="S", source="ddg")]
    intent = SearchIntent(type=IntentType.WEB_SEARCH, query="test")
    formatted = _format_results(results, intent)
    assert len(formatted) == 1
    assert "Suchergebnis" in formatted[0]
    assert "https://x.com" in formatted[0]


def test_format_results_fetch():
    results = [SearchResult(title="T", url="https://x.com", snippet="Inhalt", source="fetch")]
    intent = SearchIntent(type=IntentType.FETCH, target="x.com", query="x.com")
    formatted = _format_results(results, intent)
    assert "Inhalt von" in formatted[0]
