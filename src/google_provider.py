"""
H.E.I.N.Z.E.L. Provider – Google Gemini
Tier 1: chat, chat_stream, models, model_detail, token_count
Tier 2: embeddings
Tier 3: –

Google Gemini API Besonderheiten:
- Endpoint: generativelanguage.googleapis.com/v1beta/models/{model}:generateContent
- Rollen: "user" und "model" (nicht "assistant")
- Content-Format: {parts: [{text: "..."}]}
- API-Key über Query-Parameter
- Streaming: :streamGenerateContent?alt=sse
"""
import json
import os
import time
import sys

from base import BaseProvider
from models import (
    ChatRequest, ChatResponse, StreamChunk, TokenCountRequest,
    TokenCountResponse, ModelDetail, EmbeddingRequest, EmbeddingResponse,
    EmbeddingData,
)


class GoogleProvider(BaseProvider):

    _tier1_core = ["chat", "chat_stream", "models_list", "model_detail", "token_count"]
    _tier2_extended = ["embeddings"]
    _tier3_specialized = []

    _features = {
        "tool_use": True, "vision": True, "web_search": True,
        "citations": False, "thinking": True, "cache_control": False,
        "embeddings": True, "audio": False, "images": False, "moderation": False,
    }

    # ─── Hilfsmethoden ─────────────────────────────────────────

    def _api_key(self) -> str:
        from config import instance_config
        return instance_config.api_key("GOOGLE_API_KEY")

    def _get_headers(self) -> dict:
        return {"Content-Type": "application/json"}

    def _get_endpoint(self, model: str = "") -> str:
        base = self.config.get("api_base", "https://generativelanguage.googleapis.com/v1beta")
        m = model or self.config.get("default_model", "gemini-2.0-flash")
        return f"{base}/models/{m}:generateContent?key={self._api_key()}"

    def _get_stream_endpoint(self, model: str) -> str:
        base = self.config.get("api_base", "https://generativelanguage.googleapis.com/v1beta")
        return f"{base}/models/{model}:streamGenerateContent?alt=sse&key={self._api_key()}"

    def _get_token_count_endpoint(self, model: str) -> str:
        base = self.config.get("api_base", "https://generativelanguage.googleapis.com/v1beta")
        return f"{base}/models/{model}:countTokens?key={self._api_key()}"

    def _get_embed_endpoint(self, model: str) -> str:
        base = self.config.get("api_base", "https://generativelanguage.googleapis.com/v1beta")
        return f"{base}/models/{model}:embedContent?key={self._api_key()}"

    def _role(self, role: str) -> str:
        """Anthropic/OpenAI roles → Gemini roles."""
        return "model" if role == "assistant" else "user"

    def _to_contents(self, messages) -> list:
        """
        Wandelt Messages in Gemini-contents um.
        Gemini erwartet alternierend user/model — aufeinanderfolgende
        gleiche Rollen werden zusammengeführt.
        """
        contents = []
        for msg in messages:
            role = self._role(msg.role)
            parts = self._gemini_parts(msg.content)
            # Gleiche Rollen hintereinander zusammenführen
            if contents and contents[-1]["role"] == role:
                contents[-1]["parts"].extend(parts)
            else:
                contents.append({"role": role, "parts": parts})
        return contents

    def _gemini_parts(self, content) -> list:
        """Wandelt MessageContent in Gemini-parts.

        str  → [{"text": "..."}]
        list → text, image und document als inline_data
        """
        raw_parts = self._content_to_parts(content)
        result = []
        for p in raw_parts:
            t = p["type"]
            if t == "text":
                result.append({"text": p.get("text", "")})
            elif t == "image":
                result.append({"inline_data": {
                    "mime_type": p["media_type"],
                    "data": p["data"],
                }})
            elif t == "document":
                result.append({"inline_data": {
                    "mime_type": "application/pdf",
                    "data": p["data"],
                }})
            else:
                result.append({"text": str(p)})
        return result

    def _build_payload(self, request: ChatRequest) -> dict:
        payload = {"contents": self._to_contents(request.messages), "generationConfig": {}}
        if request.system:
            payload["system_instruction"] = {"parts": [{"text": request.system}]}
        if request.max_tokens:
            payload["generationConfig"]["maxOutputTokens"] = request.max_tokens
        if request.temperature is not None:
            payload["generationConfig"]["temperature"] = request.temperature
        if request.top_p is not None:
            payload["generationConfig"]["topP"] = request.top_p
        if request.stop_sequences:
            payload["generationConfig"]["stopSequences"] = request.stop_sequences
        if not payload["generationConfig"]:
            del payload["generationConfig"]
        if request.tools:
            payload["tools"] = [{"function_declarations": request.tools}]
        return payload

    # ─── Abstract Implementations (für BaseProvider ABC) ───────

    def _transform_request(self, request: ChatRequest) -> dict:
        return self._build_payload(request)

    def _transform_stream_request(self, request: ChatRequest) -> dict:
        return self._build_payload(request)

    def _transform_response(self, raw: dict) -> ChatResponse:
        candidates = raw.get("candidates", [])
        content = ""
        content_blocks = []
        stop_reason = None
        if candidates:
            cand = candidates[0]
            stop_reason = cand.get("finishReason", "STOP").lower()
            for part in cand.get("content", {}).get("parts", []):
                if "text" in part:
                    content += part["text"]
                    content_blocks.append({"type": "text", "text": part["text"]})
                elif "functionCall" in part:
                    fc = part["functionCall"]
                    content_blocks.append({
                        "type": "tool_use",
                        "name": fc.get("name", ""),
                        "input": fc.get("args", {}),
                    })
        usage_meta = raw.get("usageMetadata", {})
        return ChatResponse(
            content=content,
            model=raw.get("modelVersion", self.config.get("default_model", "gemini")),
            usage={
                "input_tokens": usage_meta.get("promptTokenCount", 0),
                "output_tokens": usage_meta.get("candidatesTokenCount", 0),
            },
            provider=self.provider_name,
            stop_reason=stop_reason,
            content_blocks=content_blocks,
        )

    def _parse_stream_chunk(self, raw_line: str) -> StreamChunk | None:
        try:
            ev = json.loads(raw_line)
        except json.JSONDecodeError:
            return None
        candidates = ev.get("candidates", [])
        usage_meta = ev.get("usageMetadata", {})
        if not candidates:
            if usage_meta:
                return StreamChunk(type="usage", usage={
                    "input_tokens": usage_meta.get("promptTokenCount", 0),
                    "output_tokens": usage_meta.get("candidatesTokenCount", 0),
                })
            return None
        cand = candidates[0]
        finish = cand.get("finishReason")
        parts = cand.get("content", {}).get("parts", [])
        text = "".join(p.get("text", "") for p in parts if "text" in p)
        if finish in ("STOP", "MAX_TOKENS"):
            return StreamChunk(type="done")
        if text:
            return StreamChunk(type="content_delta", content=text,
                               model=ev.get("modelVersion"))
        return None

    # ─── Tier 1: Chat (Override wegen Modell im Endpoint) ──────

    async def chat(self, request: ChatRequest) -> ChatResponse:
        await self._ensure_client()
        start = time.perf_counter()
        status = "success"
        err = None
        in_tok = out_tok = 0
        model = request.model or self.config.get("default_model", "gemini-2.0-flash")
        payload = self._build_payload(request)
        self.logger.log_request("/chat", payload)
        try:
            resp = await self._client.post(
                self._get_endpoint(model), headers=self._get_headers(), json=payload)
            resp.raise_for_status()
            result = self._transform_response(resp.json())
            in_tok = result.usage.get("input_tokens", 0)
            out_tok = result.usage.get("output_tokens", 0)
            self.logger.log_response("/chat", resp.status_code, result.model_dump())
            return result
        except Exception as e:
            status = "error"
            err = str(e)
            self.logger.log_error("/chat", err)
            raise
        finally:
            ms = int((time.perf_counter() - start) * 1000)
            await self._log_cost(model, in_tok, out_tok, ms, request.context, status, err)

    async def chat_stream(self, request: ChatRequest):
        await self._ensure_client()
        start = time.perf_counter()
        status = "success"
        err = None
        in_tok = out_tok = 0
        model = request.model or self.config.get("default_model", "gemini-2.0-flash")
        payload = self._build_payload(request)
        self.logger.log_request("/chat/stream", payload)
        try:
            async with self._client.stream(
                "POST", self._get_stream_endpoint(model),
                headers=self._get_headers(), json=payload
            ) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data = line[6:].strip()
                    if not data or data == "[DONE]":
                        break
                    chunk = self._parse_stream_chunk(data)
                    if chunk is None:
                        continue
                    if chunk.type == "usage" and chunk.usage:
                        in_tok = chunk.usage.get("input_tokens", in_tok)
                        out_tok = chunk.usage.get("output_tokens", out_tok)
                    yield chunk
        except Exception as e:
            status = "error"
            err = str(e)
            self.logger.log_error("/chat/stream", err)
            yield StreamChunk(type="error", error=err)
        finally:
            ms = int((time.perf_counter() - start) * 1000)
            self.logger.log_response("/chat/stream", 200, {
                "model": model, "input_tokens": in_tok,
                "output_tokens": out_tok, "latency_ms": ms,
            })
            await self._log_cost(model, in_tok, out_tok, ms, request.context, status, err)

    # ─── Tier 1: Model Detail ──────────────────────────────────

    async def get_model_detail(self, model_id: str) -> ModelDetail:
        await self._ensure_client()
        base = self.config.get("api_base", "https://generativelanguage.googleapis.com/v1beta")
        url = f"{base}/models/{model_id}?key={self._api_key()}"
        resp = await self._client.get(url, headers=self._get_headers())
        resp.raise_for_status()
        d = resp.json()
        return ModelDetail(
            id=d.get("name", model_id).split("/")[-1],
            name=d.get("displayName", model_id),
            provider=self.provider_name,
            created=None,
            owned_by="google",
        )

    # ─── Tier 1: Token Count ───────────────────────────────────

    async def count_tokens(self, request: TokenCountRequest) -> TokenCountResponse:
        await self._ensure_client()
        model = request.model or self.config.get("default_model", "gemini-2.0-flash")
        payload = {"contents": self._to_contents(request.messages)}
        if request.system:
            payload["system_instruction"] = {"parts": [{"text": request.system}]}
        resp = await self._client.post(
            self._get_token_count_endpoint(model),
            headers=self._get_headers(), json=payload,
        )
        resp.raise_for_status()
        return TokenCountResponse(
            input_tokens=resp.json().get("totalTokens", 0),
            model=model, provider=self.provider_name,
        )

    # ─── Tier 2: Embeddings ────────────────────────────────────

    async def create_embedding(self, request: EmbeddingRequest) -> EmbeddingResponse:
        await self._ensure_client()
        model = request.model or self.config.get(
            "embedding_model", "text-embedding-004")
        texts = request.input if isinstance(request.input, list) else [request.input]
        data = []
        for i, text in enumerate(texts):
            payload = {"content": {"parts": [{"text": text}]}}
            resp = await self._client.post(
                self._get_embed_endpoint(model),
                headers=self._get_headers(), json=payload,
            )
            resp.raise_for_status()
            values = resp.json().get("embedding", {}).get("values", [])
            data.append(EmbeddingData(index=i, embedding=values))
        return EmbeddingResponse(
            data=data, model=model,
            usage={"prompt_tokens": len(texts), "total_tokens": len(texts)},
            provider=self.provider_name,
        )
