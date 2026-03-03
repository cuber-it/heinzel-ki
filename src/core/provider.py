"""HttpLLMProvider — HTTP-Client gegen Heinzel Provider-Services.

Implementiert LLMProvider-ABC (chat + stream) und Management-API
(health, list_models, set_model).

Kein LLM-spezifischer Code — spricht gegen unsere eigene Service-API,
hinter der beliebige LLMs stecken koennen (OpenAI, Anthropic, Ollama, ...).
"""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator

import httpx

from .base import LLMProvider
from .exceptions import ProviderError

logger = logging.getLogger(__name__)


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
                        except json.JSONDecodeError:
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
