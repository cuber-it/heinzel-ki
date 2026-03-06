"""HttpLLMProvider — HTTP-Client gegen Heinzel Provider-Services.

Implementiert LLMProvider-ABC (chat + stream) und Management-API
(health, list_models, set_model).

Kein LLM-spezifischer Code — spricht gegen unsere eigene Service-API,
hinter der beliebige LLMs stecken koennen (OpenAI, Anthropic, Ollama, ...).
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from typing import Any, AsyncGenerator

import httpx

from .exceptions import ProviderError

logger = logging.getLogger(__name__)


# =============================================================================
# LLMProvider ABC — minimales Interface fuer alle LLM-Provider
# =============================================================================


class LLMProvider(ABC):
    """Minimales Interface fuer LLM-Provider.

    Konkrete Implementierungen: HttpLLMProvider (HTTP gegen Provider-Service).
    Fuer Tests genuegt ein Mock der dieses Interface implementiert.
    """

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str = "",
        model: str = "",
    ) -> str:
        """Einfacher Chat-Call. Gibt den Response-Text zurueck."""

    async def chat_tools(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str = "",
        model: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Chat mit Tool-Definitionen. Gibt (text, content_blocks) zurueck.

        content_blocks koennen tool_use-Bloecke enthalten wenn das LLM
        einen Tool-Call ausgibt.

        Default-Implementierung: ruft chat() auf, gibt leere Bloecke zurueck.
        HttpLLMProvider ueberschreibt mit echter Tool-Unterstuetzung.
        """
        text = await self.chat(messages=messages, system_prompt=system_prompt, model=model)
        return text, []

    @abstractmethod
    async def stream(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str = "",
        model: str = "",
    ) -> AsyncGenerator[str, None]:
        """Streaming-Call. Liefert Text-Chunks."""


class HttpLLMProvider(LLMProvider):
    """HTTP-Client gegen einen laufenden Heinzel Provider-Service.

    Ein Provider-Service ist ein Container der unsere Service-API
    implementiert — unabhaengig davon welches LLM dahinter steckt.

    Verwendung:
        provider = HttpLLMProvider(name="openai", base_url="http://thebrain:12101")
        ok = await provider.health()
        response = await provider.chat(messages=[{"role": "user", "content": "Hallo"}])
    """

    def __init__(
        self,
        name: str,
        base_url: str,
        model: str = "",
        timeout: float = 120.0,
    ) -> None:
        self._name = name
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._context_window: int | None = None  # lazy-discovery via ContextLengthExceededError

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @property
    def name(self) -> str:
        return self._name

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def current_model(self) -> str:
        return self._model

    @property
    def context_window(self) -> int | None:
        """Bekannte Kontextfenster-Groesse in Token.

        None = noch unbekannt (kein Request bisher oder nie ein 400 erhalten).
        Wird automatisch gesetzt wenn _call_provider() einen
        ContextLengthExceededError faengt.
        """
        return self._context_window

    @context_window.setter
    def context_window(self, value: int) -> None:
        """Setzt das Limit nach Lazy-Discovery durch einen 400-Fehler."""
        self._context_window = value

    # -------------------------------------------------------------------------
    # Management
    # -------------------------------------------------------------------------

    async def health(self) -> bool:
        """Pingt /health. Gibt True wenn status == 'ok', sonst False."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(f"{self._base_url}/health")
                resp.raise_for_status()
                return resp.json().get("status") == "ok"
        except Exception as exc:
            logger.warning("health() fehlgeschlagen fuer %s: %s", self._name, exc)
            return False

    async def list_models(self) -> list[str]:
        """Gibt alle verfuegbaren Modelle des Providers zurueck."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{self._base_url}/models")
                resp.raise_for_status()
                return resp.json().get("models", [])
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"list_models fehlgeschlagen: {self._name}",
                status_code=exc.response.status_code,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            raise ProviderError(
                f"list_models fehlgeschlagen: {self._name}",
                detail=str(exc),
            ) from exc

    def set_model(self, model: str) -> None:
        """Setzt das aktive Modell fuer folgende Requests."""
        self._model = model
        logger.debug("Provider '%s': Modell gesetzt auf '%s'", self._name, model)

    # -------------------------------------------------------------------------
    # LLMProvider-ABC
    # -------------------------------------------------------------------------

    async def chat(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str = "",
        model: str = "",
    ) -> str:
        """Blockierender Chat-Call gegen /chat."""
        payload: dict[str, Any] = {"messages": messages}
        if system_prompt:
            payload["system"] = system_prompt
        effective_model = model or self._model
        if effective_model:
            payload["model"] = effective_model

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._base_url}/chat", json=payload)
                resp.raise_for_status()
                return resp.json().get("content", "")
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"chat fehlgeschlagen: {self._name}",
                status_code=exc.response.status_code,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            raise ProviderError(
                f"chat fehlgeschlagen: {self._name}",
                detail=str(exc),
            ) from exc

    async def chat_tools(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str = "",
        model: str = "",
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Chat mit Tool-Definitionen gegen /chat.

        Schickt tools im Payload. Gibt (text, content_blocks) zurueck.
        content_blocks koennen tool_use-Bloecke enthalten.
        """
        payload: dict[str, Any] = {"messages": messages}
        if system_prompt:
            payload["system"] = system_prompt
        effective_model = model or self._model
        if effective_model:
            payload["model"] = effective_model
        if tools:
            payload["tools"] = tools

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(f"{self._base_url}/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
                text = data.get("content", "")
                content_blocks = data.get("content_blocks", []) or []
                return text, content_blocks
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"chat_tools fehlgeschlagen: {self._name}",
                status_code=exc.response.status_code,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            raise ProviderError(
                f"chat_tools fehlgeschlagen: {self._name}",
                detail=str(exc),
            ) from exc

    async def stream(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str = "",
        model: str = "",
    ) -> AsyncGenerator[str, None]:
        """Streaming Chat-Call gegen /chat/stream (SSE)."""
        payload: dict[str, Any] = {"messages": messages}
        if system_prompt:
            payload["system"] = system_prompt
        effective_model = model or self._model
        if effective_model:
            payload["model"] = effective_model

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream(
                    "POST", f"{self._base_url}/chat/stream", json=payload
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            chunk = json.loads(data)
                            if chunk.get("type") == "content_delta" and chunk.get("content"):
                                yield chunk["content"]
                        except json.JSONDecodeError as exc:
                            logger.debug("SSE-Chunk nicht parsebar: %s (data=%s)", exc, data[:80])
                            continue
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"stream fehlgeschlagen: {self._name}",
                status_code=exc.response.status_code,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            raise ProviderError(
                f"stream fehlgeschlagen: {self._name}",
                detail=str(exc),
            ) from exc


class NoopProvider(LLMProvider):
    """Fallback-Provider — gibt immer leere Antwort zurück.

    Nützlich wenn kein LLM konfiguriert ist (Tests, Dry-Run).
    """

    async def chat(self, messages, system_prompt="", model="", **kwargs) -> str:
        return ""

    async def stream(self, messages, system_prompt="", model="", **kwargs):
        return
        yield  # macht es zum Generator
