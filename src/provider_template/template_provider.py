"""
H.E.I.N.Z.E.L. Provider — Template fuer neue Provider

ANLEITUNG:
1. Diese Datei kopieren: cp template_provider.py myprovider_provider.py
2. Klasse umbenennen: MyProviderProvider
3. Die 5 Pflicht-Methoden implementieren (markiert mit # IMPLEMENT)
4. YAML-Config anlegen (siehe provider.yaml.example)
5. PROVIDER_TYPE in docker-compose setzen
6. Testen: pytest tests/test_provider_template.py -k myprovider
"""
from typing import AsyncGenerator, Optional
from base import BaseProvider
from models import (
    ChatRequest, ChatResponse, StreamChunk,
    TokenCountRequest, TokenCountResponse,
)


class TemplateProvider(BaseProvider):
    """
    Minimal-Implementierung als Startpunkt fuer neue Provider.
    Erbt Tier-2/3-Defaults von BaseProvider (raise EndpointNotAvailable).
    """

    # ═══════════════════════════════════════
    # PFLICHT: Diese 5 Methoden implementieren
    # ═══════════════════════════════════════

    def get_models(self) -> list[str]:
        """Liste aller verfuegbaren Modell-IDs zurueckgeben."""
        # IMPLEMENT: Modellnamen des Providers
        return ["my-model-v1", "my-model-v2"]

    def get_default_model(self) -> str:
        """Standard-Modell wenn keins angegeben."""
        # IMPLEMENT: Standard-Modell
        return "my-model-v1"

    def _get_endpoint(self) -> str:
        """API-Endpunkt-URL (aus self.config["api_base"] + Pfad)."""
        # IMPLEMENT: z.B. return f"{self.config['api_base']}/chat/completions"
        return f"{self.config['api_base']}/completions"

    def _get_headers(self) -> dict:
        """HTTP-Headers fuer alle Requests (Auth, Content-Type)."""
        # IMPLEMENT: Auth-Header
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _transform_request(self, request: ChatRequest) -> dict:
        """ChatRequest in Provider-spezifisches JSON-Format umwandeln."""
        # IMPLEMENT: Provider-Format bauen
        return {
            "model": request.model or self.get_default_model(),
            "messages": [{"role": m.role, "content": m.content}
                         for m in request.messages],
            "max_tokens": request.max_tokens,
        }

    def _transform_response(self, response: dict) -> ChatResponse:
        """Provider-JSON-Antwort in ChatResponse umwandeln."""
        # IMPLEMENT: Felder aus response extrahieren
        choice = response["choices"][0]
        return ChatResponse(
            content=choice["message"]["content"],
            model=response.get("model", self.get_default_model()),
            usage={
                "input_tokens": response.get("usage", {}).get("prompt_tokens", 0),
                "output_tokens": response.get("usage", {}).get("completion_tokens", 0),
            },
            provider=self.provider_name,
            stop_reason=choice.get("finish_reason"),
        )

    # ═══════════════════════════════════════
    # OPTIONAL: Streaming (default: kein Streaming)
    # ═══════════════════════════════════════

    def _transform_stream_request(self, request: ChatRequest) -> dict:
        """Wie _transform_request, aber mit stream=True."""
        payload = self._transform_request(request)
        payload["stream"] = True
        return payload

    def _parse_stream_chunk(self, data: str) -> Optional[StreamChunk]:
        """
        Eine SSE-Zeile (data: {...}) parsen und als StreamChunk zurueckgeben.
        None zurueckgeben wenn die Zeile ignoriert werden soll.
        """
        # IMPLEMENT: Provider-spezifisches Chunk-Format
        import json
        try:
            chunk = json.loads(data)
        except Exception:
            return None
        delta = chunk.get("choices", [{}])[0].get("delta", {})
        content = delta.get("content")
        if content:
            return StreamChunk(type="content_delta", content=content)
        finish = chunk.get("choices", [{}])[0].get("finish_reason")
        if finish:
            return StreamChunk(type="done")
        return None

    # ═══════════════════════════════════════
    # OPTIONAL: Token-Zaehlung
    # ═══════════════════════════════════════

    async def count_tokens(self, request: TokenCountRequest) -> TokenCountResponse:
        """
        Tokens zaehlen. Default-Implementierung: grobe Schaetzung (4 Zeichen = 1 Token).
        Fuer echte Zaehlung: Provider-API nutzen.
        """
        total = sum(len(str(m.content)) // 4 for m in request.messages)
        return TokenCountResponse(
            input_tokens=total,
            model=request.model or self.get_default_model(),
            provider=self.provider_name,
        )
