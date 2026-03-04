"""Tests fuer HttpLLMProvider + ProviderRegistry — HNZ-002-0008.

Testet:
  - HttpLLMProvider: Properties, chat, stream, health, list_models, set_model
  - HttpLLMProvider: ProviderError bei HTTP-Fehlern und Connection-Fehlern
  - ProviderRegistry: load_config, check_all, get_active, switch_to, fallback, reload_config
  - ProviderRegistry: ConfigError bei fehlender/leerer Config
  - ProviderRegistry: Config-Pfad-Aufloesung (Konstruktor, Env, Default)
  - Runner: set_provider mit health-Check + turn-safem swap

Kein echter Server — unittest.mock fuer alle HTTP-Calls.
"""

from __future__ import annotations

import asyncio
import json
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from core.runner import Runner, LLMProvider
from core.exceptions import ConfigError, ProviderError
from core.provider import HttpLLMProvider
from core.provider_registry import ProviderRegistry


# =============================================================================
# Hilfsfunktionen
# =============================================================================

def _json_resp(data: dict, status: int = 200):
    """Minimaler Response-Mock mit .json() und .raise_for_status()."""
    from unittest.mock import MagicMock
    resp = MagicMock()
    resp.status_code = status
    resp.json = MagicMock(return_value=data)
    resp.raise_for_status = MagicMock()
    return resp


def _make_client_mock(get_resp=None, post_resp=None, stream_resp=None):
    """Baut einen AsyncMock fuer httpx.AsyncClient.

    stream_resp: direkt als Kontext-Manager zurueckgegeben (kein await),
                 daher MagicMock statt AsyncMock fuer client.stream.
    """
    from unittest.mock import MagicMock
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    if get_resp is not None:
        client.get = AsyncMock(return_value=get_resp)
    if post_resp is not None:
        client.post = AsyncMock(return_value=post_resp)
    if stream_resp is not None:
        # stream() wird mit "async with client.stream(...) as resp" genutzt
        # kein await — also MagicMock der direkt stream_resp zurueckgibt
        client.stream = MagicMock(return_value=stream_resp)
    return client


def _make_stream_mock(lines: list[str]):
    """Baut einen AsyncMock fuer client.stream() Kontext-Manager."""
    async def fake_aiter_lines():
        for line in lines:
            yield line

    from unittest.mock import MagicMock
    resp = AsyncMock()
    resp.raise_for_status = MagicMock()   # kein await in provider.py
    resp.aiter_lines = fake_aiter_lines
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def _sse_lines(*chunks: str) -> list[str]:
    """Baut SSE-Zeilen aus Content-Chunks."""
    lines = []
    for chunk in chunks:
        lines.append("data: " + json.dumps({"type": "content_delta", "content": chunk}))
    lines.append("data: [DONE]")
    return lines


# =============================================================================
# Fixtures
# =============================================================================

@pytest.fixture
def provider() -> HttpLLMProvider:
    return HttpLLMProvider(name="openai", base_url="http://fake:12101", model="gpt-4o")


@pytest.fixture
def providers_yaml(tmp_path: Path) -> Path:
    cfg = tmp_path / "providers.yaml"
    cfg.write_text(textwrap.dedent("""\
        providers:
          - name: openai
            url: http://fake:12101
          - name: anthropic
            url: http://fake:12102
    """))
    return cfg


@pytest.fixture
def registry(providers_yaml: Path) -> ProviderRegistry:
    return ProviderRegistry(config_path=str(providers_yaml))


# =============================================================================
# HttpLLMProvider — Properties + set_model
# =============================================================================

def test_provider_properties(provider: HttpLLMProvider) -> None:
    assert provider.name == "openai"
    assert provider.base_url == "http://fake:12101"
    assert provider.current_model == "gpt-4o"


def test_provider_trailing_slash_stripped() -> None:
    p = HttpLLMProvider(name="x", base_url="http://fake:12101/")
    assert p.base_url == "http://fake:12101"


def test_set_model(provider: HttpLLMProvider) -> None:
    provider.set_model("gpt-4o-mini")
    assert provider.current_model == "gpt-4o-mini"


# =============================================================================
# HttpLLMProvider — health
# =============================================================================

@pytest.mark.asyncio
async def test_health_returns_true(provider: HttpLLMProvider) -> None:
    resp = _json_resp({"status": "ok", "provider": "openai", "timestamp": "t"})
    with patch("core.provider.httpx.AsyncClient", return_value=_make_client_mock(get_resp=resp)):
        assert await provider.health() is True


@pytest.mark.asyncio
async def test_health_returns_false_on_error_status(provider: HttpLLMProvider) -> None:
    resp = _json_resp({"status": "error", "provider": "openai", "timestamp": "t"})
    with patch("core.provider.httpx.AsyncClient", return_value=_make_client_mock(get_resp=resp)):
        assert await provider.health() is False


@pytest.mark.asyncio
async def test_health_returns_false_on_connect_error(provider: HttpLLMProvider) -> None:
    client = _make_client_mock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    with patch("core.provider.httpx.AsyncClient", return_value=client):
        assert await provider.health() is False


# =============================================================================
# HttpLLMProvider — list_models
# =============================================================================

@pytest.mark.asyncio
async def test_list_models_ok(provider: HttpLLMProvider) -> None:
    resp = _json_resp({"models": ["gpt-4o", "gpt-4o-mini"], "default": "gpt-4o", "provider": "openai"})
    with patch("core.provider.httpx.AsyncClient", return_value=_make_client_mock(get_resp=resp)):
        models = await provider.list_models()
    assert models == ["gpt-4o", "gpt-4o-mini"]


@pytest.mark.asyncio
async def test_list_models_raises_on_connect_error(provider: HttpLLMProvider) -> None:
    client = _make_client_mock()
    client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
    with patch("core.provider.httpx.AsyncClient", return_value=client):
        with pytest.raises(ProviderError):
            await provider.list_models()


# =============================================================================
# HttpLLMProvider — chat
# =============================================================================

@pytest.mark.asyncio
async def test_chat_returns_content(provider: HttpLLMProvider) -> None:
    resp = _json_resp({"content": "Hallo Welt", "model": "gpt-4o", "usage": {}, "provider": "openai"})
    with patch("core.provider.httpx.AsyncClient", return_value=_make_client_mock(post_resp=resp)):
        result = await provider.chat([{"role": "user", "content": "Hallo"}])
    assert result == "Hallo Welt"


@pytest.mark.asyncio
async def test_chat_sends_system_prompt(provider: HttpLLMProvider) -> None:
    resp = _json_resp({"content": "ok", "model": "gpt-4o", "usage": {}, "provider": "openai"})
    client = _make_client_mock(post_resp=resp)
    with patch("core.provider.httpx.AsyncClient", return_value=client):
        await provider.chat([{"role": "user", "content": "test"}], system_prompt="Du bist Heinzel")
    payload = client.post.call_args.kwargs["json"]
    assert payload["system"] == "Du bist Heinzel"


@pytest.mark.asyncio
async def test_chat_model_override(provider: HttpLLMProvider) -> None:
    """Explizites model-Argument hat Vorrang vor provider._model."""
    resp = _json_resp({"content": "ok", "model": "gpt-4o-mini", "usage": {}, "provider": "openai"})
    client = _make_client_mock(post_resp=resp)
    with patch("core.provider.httpx.AsyncClient", return_value=client):
        await provider.chat([{"role": "user", "content": "test"}], model="gpt-4o-mini")
    payload = client.post.call_args.kwargs["json"]
    assert payload["model"] == "gpt-4o-mini"


@pytest.mark.asyncio
async def test_chat_raises_provider_error_on_http_500(provider: HttpLLMProvider) -> None:
    req = httpx.Request("POST", "http://fake:12101/chat")
    err_resp = httpx.Response(500, json={"detail": "error"})
    err_resp.request = req
    client = _make_client_mock()
    client.post = AsyncMock(side_effect=httpx.HTTPStatusError("500", request=req, response=err_resp))
    with patch("core.provider.httpx.AsyncClient", return_value=client):
        with pytest.raises(ProviderError) as exc_info:
            await provider.chat([{"role": "user", "content": "test"}])
    assert exc_info.value.status_code == 500


@pytest.mark.asyncio
async def test_chat_raises_provider_error_on_connect_error(provider: HttpLLMProvider) -> None:
    client = _make_client_mock()
    client.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
    with patch("core.provider.httpx.AsyncClient", return_value=client):
        with pytest.raises(ProviderError):
            await provider.chat([{"role": "user", "content": "test"}])


# =============================================================================
# HttpLLMProvider — stream
# =============================================================================

@pytest.mark.asyncio
async def test_stream_yields_chunks(provider: HttpLLMProvider) -> None:
    stream_mock = _make_stream_mock(_sse_lines("Hallo", " ", "Welt"))
    client = _make_client_mock(stream_resp=stream_mock)
    with patch("core.provider.httpx.AsyncClient", return_value=client):
        chunks = [c async for c in provider.stream([{"role": "user", "content": "test"}])]
    assert chunks == ["Hallo", " ", "Welt"]


@pytest.mark.asyncio
async def test_stream_stops_at_done(provider: HttpLLMProvider) -> None:
    lines = [
        "data: " + json.dumps({"type": "content_delta", "content": "A"}),
        "data: [DONE]",
        "data: " + json.dumps({"type": "content_delta", "content": "B"}),
    ]
    stream_mock = _make_stream_mock(lines)
    client = _make_client_mock(stream_resp=stream_mock)
    with patch("core.provider.httpx.AsyncClient", return_value=client):
        chunks = [c async for c in provider.stream([{"role": "user", "content": "test"}])]
    assert chunks == ["A"]


@pytest.mark.asyncio
async def test_stream_ignores_non_data_lines(provider: HttpLLMProvider) -> None:
    lines = [
        ": keep-alive",
        "data: " + json.dumps({"type": "content_delta", "content": "X"}),
        "data: [DONE]",
    ]
    stream_mock = _make_stream_mock(lines)
    client = _make_client_mock(stream_resp=stream_mock)
    with patch("core.provider.httpx.AsyncClient", return_value=client):
        chunks = [c async for c in provider.stream([{"role": "user", "content": "test"}])]
    assert chunks == ["X"]


# =============================================================================
# ProviderRegistry — Config laden
# =============================================================================

def test_registry_load_config_ok(registry: ProviderRegistry) -> None:
    registry.load_config()
    assert len(registry.providers) == 2
    assert registry.providers[0].name == "openai"
    assert registry.providers[1].name == "anthropic"


def test_registry_load_config_missing_file(tmp_path: Path) -> None:
    r = ProviderRegistry(config_path=str(tmp_path / "nope.yaml"))
    with pytest.raises(ConfigError):
        r.load_config()


def test_registry_load_config_empty_providers(tmp_path: Path) -> None:
    cfg = tmp_path / "providers.yaml"
    cfg.write_text("providers: []\n")
    r = ProviderRegistry(config_path=str(cfg))
    with pytest.raises(ConfigError):
        r.load_config()


def test_registry_load_config_skips_invalid_entries(tmp_path: Path) -> None:
    cfg = tmp_path / "providers.yaml"
    cfg.write_text(textwrap.dedent("""\
        providers:
          - name: openai
            url: http://fake:12101
          - name: ""
            url: http://fake:12102
          - url: http://fake:12103
    """))
    r = ProviderRegistry(config_path=str(cfg))
    r.load_config()
    assert len(r.providers) == 1


# =============================================================================
# ProviderRegistry — Config-Pfad-Aufloesung
# =============================================================================

def test_registry_config_path_from_constructor(tmp_path: Path) -> None:
    path = str(tmp_path / "p.yaml")
    r = ProviderRegistry(config_path=path)
    assert r.config_path == Path(path)


def test_registry_config_path_from_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = str(tmp_path / "p.yaml")
    monkeypatch.setenv("HEINZEL_PROVIDERS_CONFIG", path)
    r = ProviderRegistry()
    assert r.config_path == Path(path)


def test_registry_config_path_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HEINZEL_PROVIDERS_CONFIG", raising=False)
    r = ProviderRegistry()
    assert r.config_path == Path("providers.yaml")


# =============================================================================
# ProviderRegistry — check_all, get_active, switch_to, fallback
# =============================================================================

@pytest.mark.asyncio
async def test_registry_check_all(registry: ProviderRegistry) -> None:
    registry.load_config()

    async def fake_health(self) -> bool:
        return self.name == "openai"

    with patch.object(HttpLLMProvider, "health", fake_health):
        status = await registry.check_all()

    assert status == {"openai": True, "anthropic": False}


@pytest.mark.asyncio
async def test_registry_startup_activates_first_healthy(registry: ProviderRegistry) -> None:
    async def fake_health(self) -> bool:
        return self.name == "openai"

    with patch.object(HttpLLMProvider, "health", fake_health):
        await registry.startup()

    assert registry.active is not None
    assert registry.active.name == "openai"


@pytest.mark.asyncio
async def test_registry_get_active_raises_when_none(registry: ProviderRegistry) -> None:
    registry.load_config()
    # kein startup — active ist None
    with pytest.raises(ProviderError):
        registry.get_active()


@pytest.mark.asyncio
async def test_registry_switch_to_ok(registry: ProviderRegistry) -> None:
    registry.load_config()

    async def fake_health(self) -> bool:
        return True

    with patch.object(HttpLLMProvider, "health", fake_health):
        ok = await registry.switch_to("anthropic")

    assert ok is True
    assert registry.active is not None
    assert registry.active.name == "anthropic"


@pytest.mark.asyncio
async def test_registry_switch_to_unknown_returns_false(registry: ProviderRegistry) -> None:
    registry.load_config()
    ok = await registry.switch_to("ollama")
    assert ok is False


@pytest.mark.asyncio
async def test_registry_switch_to_unhealthy_returns_false(registry: ProviderRegistry) -> None:
    registry.load_config()

    async def fake_health(self) -> bool:
        return False

    with patch.object(HttpLLMProvider, "health", fake_health):
        ok = await registry.switch_to("anthropic")

    assert ok is False


@pytest.mark.asyncio
async def test_registry_fallback_switches_to_next_healthy(registry: ProviderRegistry) -> None:
    async def all_healthy(self) -> bool:
        return True

    with patch.object(HttpLLMProvider, "health", all_healthy):
        await registry.startup()  # openai aktiv

    async def only_anthropic(self) -> bool:
        return self.name == "anthropic"

    with patch.object(HttpLLMProvider, "health", only_anthropic):
        result = await registry.fallback()

    assert result is not None
    assert result.name == "anthropic"
    assert registry.active is not None
    assert registry.active.name == "anthropic"


@pytest.mark.asyncio
async def test_registry_fallback_returns_none_when_all_unhealthy(registry: ProviderRegistry) -> None:
    registry.load_config()

    async def fake_health(self) -> bool:
        return False

    with patch.object(HttpLLMProvider, "health", fake_health):
        result = await registry.fallback()

    assert result is None


@pytest.mark.asyncio
async def test_registry_reload_keeps_active_provider(registry: ProviderRegistry) -> None:
    async def all_healthy(self) -> bool:
        return True

    with patch.object(HttpLLMProvider, "health", all_healthy):
        await registry.startup()
        assert registry.active is not None
        assert registry.active.name == "openai"
        await registry.reload_config()

    assert registry.active is not None
    assert registry.active.name == "openai"


# =============================================================================
# Runner — set_provider
# =============================================================================

class _MockProvider(LLMProvider):
    def __init__(self, name: str = "mock", response: str = "ok", healthy: bool = True) -> None:
        self._name = name
        self._response = response
        self._healthy = healthy

    async def chat(self, messages, system_prompt="", model="") -> str:
        return self._response

    async def stream(self, messages, system_prompt="", model=""):
        yield self._response

    async def health(self) -> bool:
        return self._healthy


@pytest.mark.asyncio
async def test_set_provider_ok() -> None:
    old = _MockProvider("old")
    new = _MockProvider("new")
    heinzel = Runner(provider=old, name="test")

    ok = await heinzel.set_provider(new)

    assert ok is True
    assert heinzel.provider is new


@pytest.mark.asyncio
async def test_set_provider_rejects_unhealthy() -> None:
    old = _MockProvider("old")
    bad = _MockProvider("bad", healthy=False)
    heinzel = Runner(provider=old, name="test")

    ok = await heinzel.set_provider(bad)

    assert ok is False
    assert heinzel.provider is old


@pytest.mark.asyncio
async def test_set_provider_turn_safe() -> None:
    """Waehrend eines laufenden Turns wird Provider als pending gesetzt."""

    class SlowProvider(_MockProvider):
        async def chat(self, messages, system_prompt="", model="") -> str:
            await asyncio.sleep(0.05)
            return "slow"

    slow = SlowProvider("slow")
    new = _MockProvider("new")
    heinzel = Runner(provider=slow, name="test")

    chat_task = asyncio.create_task(heinzel.chat("test"))
    await asyncio.sleep(0.01)  # warten bis _in_turn gesetzt

    ok = await heinzel.set_provider(new)
    assert ok is True

    await chat_task

    # Nach Turn-Ende soll new aktiv sein
    assert heinzel.provider is new
