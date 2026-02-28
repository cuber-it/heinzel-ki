"""
H.E.I.N.Z.E.L. Provider – Anthropic
Tier 1: chat, chat_stream, models, model_detail, token_count
Tier 2: batches
Tier 3: –
"""
import json
import os

from base import BaseProvider
from models import (
    ChatRequest, ChatResponse, StreamChunk, TokenCountRequest,
    TokenCountResponse, ModelDetail, BatchCreateRequest, BatchStatus,
    BatchListResponse, BatchResultsResponse, BatchResultItem,
)


class AnthropicProvider(BaseProvider):

    _tier1_core = ["chat", "chat_stream", "models_list", "model_detail", "token_count"]
    _tier2_extended = ["batches"]
    _tier3_specialized = []

    _features = {
        "tool_use": True, "vision": True, "web_search": True,
        "citations": True, "thinking": True, "cache_control": True,
        "embeddings": False, "audio": False, "images": False, "moderation": False,
    }

    # ─── Abstract Implementations ──────────────────────────────

    def _get_headers(self) -> dict:
        from config import instance_config
        return {
            "x-api-key": instance_config.api_key("ANTHROPIC_API_KEY"),
            "anthropic-version": self.config.get("api_version", "2023-06-01"),
            "Content-Type": "application/json",
        }

    def _get_endpoint(self, path: str = "/messages") -> str:
        base = self.config.get("api_base", "https://api.anthropic.com/v1")
        return f"{base}{path}"

    def _transform_request(self, request: ChatRequest) -> dict:
        model = request.model or self.config.get("default_model", "claude-sonnet-4-20250514")
        payload = {
            "model": model,
            "max_tokens": request.max_tokens,
            "messages": [
                {"role": m.role, "content": self._anthropic_content(m.content)}
                for m in request.messages
            ],
        }
        if request.system:
            payload["system"] = request.system
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.stop_sequences:
            payload["stop_sequences"] = request.stop_sequences
        if request.tools:
            payload["tools"] = request.tools
        return payload

    def _anthropic_content(self, content) -> str | list:
        """Wandelt MessageContent in Anthropic-Format.

        str  → unveraendert (schneller Pfad)
        list → TextBlock, ImageBlock, DocumentBlock je nach type
        """
        parts = self._content_to_parts(content)
        if len(parts) == 1 and parts[0]["type"] == "text":
            return parts[0]["text"]  # einfacher Text bleibt str
        result = []
        for p in parts:
            t = p["type"]
            if t == "text":
                result.append({"type": "text", "text": p["text"]})
            elif t == "image":
                result.append({"type": "image", "source": {
                    "type": "base64",
                    "media_type": p["media_type"],
                    "data": p["data"],
                }})
            elif t == "document":
                result.append({"type": "document", "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": p["data"],
                }})
        return result

    def _transform_response(self, raw: dict) -> ChatResponse:
        content_blocks = raw.get("content", [])
        text_parts = [b["text"] for b in content_blocks if b.get("type") == "text"]
        return ChatResponse(
            content="".join(text_parts),
            model=raw.get("model", "unknown"),
            usage={
                "input_tokens": raw.get("usage", {}).get("input_tokens", 0),
                "output_tokens": raw.get("usage", {}).get("output_tokens", 0),
            },
            provider=self.provider_name,
            stop_reason=raw.get("stop_reason"),
            content_blocks=content_blocks,
        )

    def _transform_stream_request(self, request: ChatRequest) -> dict:
        p = self._transform_request(request)
        p["stream"] = True
        return p

    def _parse_stream_chunk(self, raw_line: str) -> StreamChunk | None:
        try:
            ev = json.loads(raw_line)
        except json.JSONDecodeError:
            return None
        t = ev.get("type")
        if t == "message_start":
            msg = ev.get("message", {})
            u = msg.get("usage", {})
            return StreamChunk(type="usage", model=msg.get("model"),
                               usage={"input_tokens": u.get("input_tokens", 0), "output_tokens": 0})
        if t == "content_block_delta":
            txt = ev.get("delta", {}).get("text", "")
            return StreamChunk(type="content_delta", content=txt) if txt else None
        if t == "message_delta":
            u = ev.get("usage", {})
            return StreamChunk(type="usage", usage={"output_tokens": u.get("output_tokens", 0)})
        if t == "message_stop":
            return StreamChunk(type="done")
        return None

    # ─── Tier 1: Model Detail ──────────────────────────────────

    async def get_model_detail(self, model_id: str) -> ModelDetail:
        await self._ensure_client()
        resp = await self._client.get(
            self._get_endpoint(f"/models/{model_id}"),
            headers=self._get_headers(),
        )
        resp.raise_for_status()
        d = resp.json()
        return ModelDetail(
            id=d.get("id", model_id),
            name=d.get("display_name", d.get("id", model_id)),
            provider=self.provider_name,
            created=d.get("created_at"),
            owned_by="anthropic",
        )

    # ─── Tier 1: Token Count ───────────────────────────────────

    async def count_tokens(self, request: TokenCountRequest) -> TokenCountResponse:
        await self._ensure_client()
        model = request.model or self.get_default_model()
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": self._anthropic_content(m.content)} for m in request.messages],
        }
        if request.system:
            payload["system"] = request.system
        if request.tools:
            payload["tools"] = request.tools
        resp = await self._client.post(
            self._get_endpoint("/messages/count_tokens"),
            headers=self._get_headers(), json=payload,
        )
        resp.raise_for_status()
        return TokenCountResponse(
            input_tokens=resp.json().get("input_tokens", 0),
            model=model, provider=self.provider_name,
        )

    # ─── Tier 2: Batches ───────────────────────────────────────

    async def create_batch(self, request: BatchCreateRequest) -> BatchStatus:
        await self._ensure_client()
        model = request.model or self.get_default_model()
        reqs = []
        for item in request.requests:
            p = dict(item.params)
            p.setdefault("model", model)
            reqs.append({"custom_id": item.custom_id, "params": p})
        resp = await self._client.post(
            self._get_endpoint("/messages/batches"),
            headers=self._get_headers(), json={"requests": reqs},
        )
        resp.raise_for_status()
        return self._parse_batch(resp.json())

    async def list_batches(self) -> BatchListResponse:
        await self._ensure_client()
        resp = await self._client.get(
            self._get_endpoint("/messages/batches"),
            headers=self._get_headers(),
        )
        resp.raise_for_status()
        d = resp.json()
        return BatchListResponse(
            batches=[self._parse_batch(b) for b in d.get("data", d.get("batches", []))],
            provider=self.provider_name,
        )

    async def get_batch(self, batch_id: str) -> BatchStatus:
        await self._ensure_client()
        resp = await self._client.get(
            self._get_endpoint(f"/messages/batches/{batch_id}"),
            headers=self._get_headers(),
        )
        resp.raise_for_status()
        return self._parse_batch(resp.json())

    async def cancel_batch(self, batch_id: str) -> BatchStatus:
        await self._ensure_client()
        resp = await self._client.post(
            self._get_endpoint(f"/messages/batches/{batch_id}/cancel"),
            headers=self._get_headers(),
        )
        resp.raise_for_status()
        return self._parse_batch(resp.json())

    async def get_batch_results(self, batch_id: str) -> BatchResultsResponse:
        await self._ensure_client()
        resp = await self._client.get(
            self._get_endpoint(f"/messages/batches/{batch_id}/results"),
            headers=self._get_headers(),
        )
        resp.raise_for_status()
        results = []
        for line in resp.text.strip().split("\n"):
            if not line.strip():
                continue
            e = json.loads(line)
            results.append(BatchResultItem(
                custom_id=e.get("custom_id", ""),
                result=e.get("result"), error=e.get("error"),
            ))
        return BatchResultsResponse(
            batch_id=batch_id, results=results, provider=self.provider_name,
        )

    def _parse_batch(self, d: dict) -> BatchStatus:
        c = d.get("request_counts", {})
        return BatchStatus(
            id=d.get("id", ""), status=d.get("processing_status", d.get("status", "unknown")),
            total_requests=c.get("total"), completed_requests=c.get("succeeded"),
            failed_requests=c.get("errored"), created_at=d.get("created_at"),
            ended_at=d.get("ended_at"), provider=self.provider_name,
        )
