"""
H.E.I.N.Z.E.L. Provider Gateway – BaseProvider
Alle Endpoints definiert. Default = 501 Not Implemented.
Jeder Provider überschreibt was er kann.
"""
from abc import ABC, abstractmethod
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
import httpx
import os
import sys
import time

from models import (
    ChatRequest, ChatResponse, StreamChunk, TokenCountRequest,
    TokenCountResponse, ModelDetail, EmbeddingRequest, EmbeddingResponse,
    BatchCreateRequest, BatchStatus, BatchListResponse, BatchResultsResponse,
    ModerationRequest, ModerationResponse, AudioSpeechRequest,
    ImageGenerationRequest, ImageResponse, ImageEditRequest,
    ImageVariationRequest, AudioResponse, CapabilitiesResponse,
    CapabilityTier, HealthResponse, ConnectionStatus, RequestContext,
    NotImplementedResponse,
)
from logger import RequestResponseLogger
from database import cost_logger
from config import instance_config
from retry import with_retry, RetryExhausted, RateLimitHit


class EndpointNotAvailable(Exception):
    """Raised when a provider doesn't support an endpoint."""
    def __init__(self, endpoint: str, provider: str):
        self.endpoint = endpoint
        self.provider = provider
        self.detail = NotImplementedResponse(
            endpoint=endpoint, provider=provider,
            message=f"'{endpoint}' is not available for provider '{provider}'"
        )
        super().__init__(self.detail.message)


class BaseProvider(ABC):
    """
    Abstract base for all LLM providers.

    Tier 1 (Core):       chat, chat_stream, models, model_detail, token_count
    Tier 2 (Extended):   embeddings, batches
    Tier 3 (Specialized): moderation, audio, images
    """

    _tier1_core: list[str] = []
    _tier2_extended: list[str] = []
    _tier3_specialized: list[str] = []
    _features: dict = {}

    def __init__(self, config: dict):
        self.config = config
        self.provider_name = config.get("name", "unknown")
        self._connected = False
        self._client: httpx.AsyncClient | None = None
        log_dir = os.environ.get("LOG_DIR", "/data")
        log_requests = instance_config.log_requests()
        self.logger = RequestResponseLogger(self.provider_name, log_dir, enabled=log_requests)
        self._rate_limit_hits: list = []  # Timestamps von 429-Hits

    # ─── Abstract: Jeder Provider MUSS diese implementieren ────

    @abstractmethod
    def _get_headers(self) -> dict:
        """Gibt provider-spezifische HTTP-Header zurück (Auth, Content-Type etc.)."""
        pass

    @abstractmethod
    def _get_endpoint(self, path: str = "") -> str:
        """Gibt die vollständige API-URL zurück. path ist provider-spezifisch."""
        pass

    @abstractmethod
    def _transform_request(self, request: ChatRequest) -> dict:
        """Wandelt ChatRequest in provider-spezifisches JSON-Payload um."""
        pass

    @abstractmethod
    def _transform_response(self, raw_response: dict) -> ChatResponse:
        """Wandelt die rohe API-Antwort in ein einheitliches ChatResponse um."""
        pass

    @abstractmethod
    def _transform_stream_request(self, request: ChatRequest) -> dict:
        """Wie _transform_request, aber mit stream=True o.ä. Provider-Flag."""
        pass

    @abstractmethod
    def _parse_stream_chunk(self, raw_line: str) -> StreamChunk | None:
        """
        Parst eine SSE-Zeile (ohne 'data: ' Prefix) zu einem StreamChunk.
        Gibt None zurück wenn die Zeile ignoriert werden soll.
        StreamChunk.type: 'content_delta' | 'usage' | 'done' | 'error'
        """
        pass

    def _content_to_parts(self, content) -> list:
        """
        Normalisiert MessageContent zu einer Liste von provider-agnostischen
        Dicts — wird von _transform_request in den Subklassen genutzt.

        str        → [{"type": "text", "text": "..."}]
        list       → unveraendert weitergegeben (bereits ContentBlocks)
        """
        if isinstance(content, str):
            return [{"type": "text", "text": content}]
        # list[ContentBlock] — als dicts serialisieren
        result = []
        for block in content:
            if hasattr(block, "model_dump"):
                result.append(block.model_dump())
            elif isinstance(block, dict):
                result.append(block)
        return result

    def _adapt_parts_for_provider(self, parts: list) -> list:
        """
        Passt ContentBlocks fuer diesen Provider an.
        Wird von Subklassen aufgerufen um unterstuetzte Typen zu pruefen
        und ggf. document-Blocks in Text-Extraktion umzuwandeln.
        """
        from file_processor import PROVIDER_NATIVE, _extract_pdf
        import base64 as _b64
        native = PROVIDER_NATIVE.get(self.provider_name, set())
        result = []
        for p in parts:
            if p.get("type") == "document" and "application/pdf" not in native:
                # Provider kann kein natives PDF → Text extrahieren
                try:
                    raw = _b64.b64decode(p["data"])
                    block = _extract_pdf(raw, "dokument.pdf")
                    result.append(block.model_dump())
                except Exception as e:
                    result.append({"type": "text", "text": f"[PDF-Extraktion fehlgeschlagen: {e}]"})
            else:
                result.append(p)
        return result

    # ─── Hilfsmethoden ─────────────────────────────────────────

    def _ts(self) -> str:
        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

    def _not_impl(self, endpoint: str):
        raise EndpointNotAvailable(endpoint, self.provider_name)

    def _ctx(self, request) -> dict:
        """Extrahiert session_id, heinzel_id, task_id aus dem Request-Context."""
        ctx = request.context
        if ctx is None:
            return {}
        return {
            "session_id": ctx.session_id,
            "heinzel_id": ctx.heinzel_id,
            "task_id": ctx.task_id,
        }

    async def _ensure_client(self):
        if not self._connected or not self._client:
            self.connect()  # connect() ist sync — bewusst, kein await nötig

    async def _log_cost(self, model, in_tok, out_tok, ms, ctx, status, err=None):
        ctx = ctx or RequestContext()
        try:
            await cost_logger.log_request(
                provider=self.provider_name, model=model,
                input_tokens=in_tok, output_tokens=out_tok,
                latency_ms=ms, heinzel_id=ctx.heinzel_id,
                session_id=ctx.session_id, task_id=ctx.task_id,
                status=status, error_message=err,
            )
        except Exception as e:
            print(f"Cost logging failed: {e}", file=sys.stderr)

    # ─── Connection Management ─────────────────────────────────

    def connect(self) -> ConnectionStatus:
        if not self._connected:
            self._client = httpx.AsyncClient(timeout=120.0)
            self._connected = True
        return ConnectionStatus(
            status="connected", provider=self.provider_name, timestamp=self._ts())

    def disconnect(self) -> ConnectionStatus:
        if self._connected:
            self._connected = False
        return ConnectionStatus(
            status="disconnected", provider=self.provider_name, timestamp=self._ts())

    def reset(self) -> ConnectionStatus:
        self.disconnect()
        r = self.connect()
        r.reset = True
        return r

    def health(self) -> HealthResponse:
        return HealthResponse(
            status="ok" if self._connected else "disconnected",
            provider=self.provider_name, timestamp=self._ts())

    # ─── Capabilities ──────────────────────────────────────────

    def get_capabilities(self) -> CapabilitiesResponse:
        return CapabilitiesResponse(
            provider=self.provider_name,
            tiers=CapabilityTier(
                core=self._tier1_core,
                extended=self._tier2_extended,
                specialized=self._tier3_specialized,
            ),
            features=self._features,
        )

    # ═══════════════════════════════════════════════════════════
    # TIER 1: CORE
    # ═══════════════════════════════════════════════════════════

    def get_models(self) -> list[str]:
        return self.config.get("models", [self.get_default_model()])

    def get_default_model(self) -> str:
        return self.config.get("default_model", "unknown")

    async def get_model_detail(self, model_id: str) -> ModelDetail:
        self._not_impl("GET /models/{id}")

    async def count_tokens(self, request: TokenCountRequest) -> TokenCountResponse:
        self._not_impl("POST /tokens/count")

    async def chat(self, request: ChatRequest) -> ChatResponse:
        await self._ensure_client()
        start = time.perf_counter()
        status = "success"
        err = None
        in_tok = out_tok = 0
        model = request.model or self.get_default_model()

        ctx = self._ctx(request)
        endpoint = self._get_endpoint()
        headers = self._get_headers()
        payload = self._transform_request(request)
        self.logger.log_request("/chat", payload, **ctx)

        async def _do_request():
            resp = await self._client.post(endpoint, headers=headers, json=payload)
            resp.raise_for_status()
            return resp

        try:
            resp = await with_retry(_do_request, self.config,
                                    rate_limit_tracker=self._rate_limit_hits)
            result = self._transform_response(resp.json())
            in_tok = result.usage.get("input_tokens", 0)
            out_tok = result.usage.get("output_tokens", 0)
            model = result.model
            self.logger.log_response("/chat", resp.status_code, result.model_dump(), **ctx)
            return result
        except RateLimitHit as e:
            status = "rate_limit"
            err = str(e)
            self.logger.log_error("/chat", err, **ctx)
            raise Exception(err) from e
        except RetryExhausted as e:
            status = "error"
            err = str(e)
            self.logger.log_error("/chat", err, **ctx)
            raise Exception(err) from e
        except httpx.HTTPStatusError as e:
            status = "error"
            try:
                body = e.response.json()
                detail = body.get("error", body)
                err = detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail)
            except Exception:
                err = e.response.text or str(e)
            self.logger.log_error("/chat", err, **ctx)
            raise Exception(err) from e
        except Exception as e:
            status = "error"
            err = str(e)
            self.logger.log_error("/chat", err, **ctx)
            raise
        finally:
            ms = int((time.perf_counter() - start) * 1000)
            await self._log_cost(model, in_tok, out_tok, ms, request.context, status, err)

    async def chat_stream(self, request: ChatRequest) -> AsyncGenerator[StreamChunk, None]:
        await self._ensure_client()
        start = time.perf_counter()
        status = "success"
        err = None
        in_tok = out_tok = 0
        model = request.model or self.get_default_model()

        ctx = self._ctx(request)
        endpoint = self._get_endpoint()
        headers = self._get_headers()
        payload = self._transform_stream_request(request)
        self.logger.log_request("/chat/stream", payload, **ctx)

        try:
            async with self._client.stream("POST", endpoint, headers=headers, json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:]
                    if data.strip() == "[DONE]":
                        break
                    chunk = self._parse_stream_chunk(data)
                    if chunk is None:
                        continue
                    if chunk.type == "usage" and chunk.usage:
                        in_tok = chunk.usage.get("input_tokens", in_tok)
                        out_tok = chunk.usage.get("output_tokens", out_tok)
                    if chunk.model:
                        model = chunk.model
                    yield chunk
        except httpx.HTTPStatusError as e:
            status = "error"
            try:
                await e.response.aread()
                err = str(e.response.json().get("error", e.response.text))
            except Exception:
                try:
                    err = e.response.text or str(e)
                except Exception:
                    err = str(e)
            self.logger.log_error("/chat/stream", err, **ctx)
            yield StreamChunk(type="error", error=err)
        except Exception as e:
            status = "error"
            err = str(e)
            self.logger.log_error("/chat/stream", err, **ctx)
            yield StreamChunk(type="error", error=err)
        finally:
            ms = int((time.perf_counter() - start) * 1000)
            self.logger.log_response("/chat/stream", 200, {
                "model": model, "input_tokens": in_tok,
                "output_tokens": out_tok, "latency_ms": ms,
            }, **ctx)
            await self._log_cost(model, in_tok, out_tok, ms, request.context, status, err)

    # ═══════════════════════════════════════════════════════════
    # TIER 2: EXTENDED
    # ═══════════════════════════════════════════════════════════

    async def create_embedding(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self._not_impl("POST /embeddings")

    async def create_batch(self, request: BatchCreateRequest) -> BatchStatus:
        self._not_impl("POST /batches")

    async def list_batches(self) -> BatchListResponse:
        self._not_impl("GET /batches")

    async def get_batch(self, batch_id: str) -> BatchStatus:
        self._not_impl("GET /batches/{id}")

    async def cancel_batch(self, batch_id: str) -> BatchStatus:
        self._not_impl("POST /batches/{id}/cancel")

    async def get_batch_results(self, batch_id: str) -> BatchResultsResponse:
        self._not_impl("GET /batches/{id}/results")

    # ═══════════════════════════════════════════════════════════
    # TIER 3: SPECIALIZED
    # ═══════════════════════════════════════════════════════════

    async def create_moderation(self, request: ModerationRequest) -> ModerationResponse:
        self._not_impl("POST /moderations")

    async def transcribe_audio(self, file_bytes: bytes, filename: str, **kwargs) -> AudioResponse:
        self._not_impl("POST /audio/transcriptions")

    async def translate_audio(self, file_bytes: bytes, filename: str, **kwargs) -> AudioResponse:
        self._not_impl("POST /audio/translations")

    async def create_speech(self, request: AudioSpeechRequest) -> bytes:
        self._not_impl("POST /audio/speech")

    async def generate_image(self, request: ImageGenerationRequest) -> ImageResponse:
        self._not_impl("POST /images/generations")

    async def edit_image(self, image_bytes: bytes, request: ImageEditRequest,
                         mask_bytes: bytes | None = None) -> ImageResponse:
        self._not_impl("POST /images/edits")

    async def create_image_variation(self, image_bytes: bytes,
                                     request: ImageVariationRequest) -> ImageResponse:
        self._not_impl("POST /images/variations")
