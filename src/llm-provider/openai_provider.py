"""
H.E.I.N.Z.E.L. Provider – OpenAI
Tier 1: chat, chat_stream, models, model_detail, token_count
Tier 2: embeddings, batches
Tier 3: moderation, audio (transcription, translation, speech),
        images (generation, edit, variation)
"""
import json
import os

from base import BaseProvider
from models import (
    ChatRequest, ChatResponse, StreamChunk, TokenCountRequest,
    TokenCountResponse, ModelDetail, EmbeddingRequest, EmbeddingResponse,
    EmbeddingData, BatchCreateRequest, BatchStatus, BatchListResponse,
    BatchResultsResponse, BatchResultItem, ModerationRequest,
    ModerationResponse, ModerationResult, AudioSpeechRequest,
    AudioResponse, ImageGenerationRequest, ImageResponse, ImageData,
    ImageEditRequest, ImageVariationRequest,
)


class OpenAIProvider(BaseProvider):

    _tier1_core = ["chat", "chat_stream", "models_list", "model_detail", "token_count"]
    _tier2_extended = ["embeddings", "batches"]
    _tier3_specialized = [
        "moderation", "audio_transcription", "audio_translation",
        "audio_speech", "image_generation", "image_edit", "image_variation",
    ]

    _features = {
        "tool_use": True, "vision": True, "web_search": False,
        "citations": False, "thinking": True, "cache_control": False,
        "embeddings": True, "audio": True, "images": True, "moderation": True,
    }

    # ─── Abstract Implementations ──────────────────────────────

    def _get_headers(self) -> dict:
        from config import instance_config
        return {
            "Authorization": f"Bearer {instance_config.api_key('OPENAI_API_KEY')}",
            "Content-Type": "application/json",
        }

    def _get_endpoint(self, path: str = "/chat/completions") -> str:
        base = self.config.get("api_base", "https://api.openai.com/v1")
        return f"{base}{path}"

    def _transform_request(self, request: ChatRequest) -> dict:
        model = request.model or self.config.get("default_model", "gpt-4o")
        msgs = []
        if request.system:
            msgs.append({"role": "system", "content": request.system})
        for m in request.messages:
            # Tool-Messages übersetzen: Anthropic-Format → OpenAI-Format
            if m.role == "assistant" and isinstance(m.content, list):
                # Assistant mit tool_use Blöcken → OpenAI tool_calls
                text_parts = []
                tool_calls = []
                for block in m.content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        import json as _json
                        tool_calls.append({
                            "id": block["id"],
                            "type": "function",
                            "function": {
                                "name": block["name"],
                                "arguments": _json.dumps(block.get("input", {})),
                            },
                        })
                    elif isinstance(block, dict) and block.get("type") == "text":
                        text_parts.append(block.get("text", ""))
                    elif isinstance(block, str):
                        text_parts.append(block)
                msg = {"role": "assistant", "content": "\n".join(text_parts) or None}
                if tool_calls:
                    msg["tool_calls"] = tool_calls
                msgs.append(msg)
            elif m.role == "user" and isinstance(m.content, list) and any(
                isinstance(b, dict) and b.get("type") == "tool_result" for b in m.content
            ):
                # tool_result Blöcke → separate role:"tool" Messages
                for block in m.content:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        msgs.append({
                            "role": "tool",
                            "tool_call_id": block.get("tool_use_id", ""),
                            "content": block.get("content", ""),
                        })
            else:
                msgs.append({"role": m.role, "content": self._openai_content(m.content)})
        # GPT-5+ nutzt max_completion_tokens statt max_tokens
        token_key = "max_completion_tokens" if "gpt-5" in model or "o3" in model or "o4" in model else "max_tokens"
        payload = {"model": model, token_key: request.max_tokens, "messages": msgs}
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.top_p is not None:
            payload["top_p"] = request.top_p
        if request.stop_sequences:
            payload["stop"] = request.stop_sequences
        if request.tools:
            payload["tools"] = request.tools
        return payload

    def _openai_content(self, content) -> str | list:
        """Wandelt MessageContent in OpenAI-Format.

        str  → unveraendert
        list → text bleibt text, image wird image_url,
                document (PDF) → Text-Extraktion via pypdf
        """
        parts = self._adapt_parts_for_provider(self._content_to_parts(content))
        if len(parts) == 1 and parts[0]["type"] == "text":
            return parts[0]["text"]
        result = []
        for p in parts:
            t = p["type"]
            if t == "text":
                result.append({"type": "text", "text": p["text"]})
            elif t == "image":
                result.append({"type": "image_url", "image_url": {
                    "url": f"data:{p['media_type']};base64,{p['data']}"
                }})
        return result

    def _transform_response(self, raw: dict) -> ChatResponse:
        ch = raw["choices"][0]
        msg = ch["message"]
        content = msg.get("content") or ""

        # Content-Blöcke in einheitliches Format (wie Anthropic)
        content_blocks = []
        if content:
            content_blocks.append({"type": "text", "text": content})
        for tc in msg.get("tool_calls") or []:
            import json as _json
            content_blocks.append({
                "type": "tool_use",
                "id": tc["id"],
                "name": tc["function"]["name"],
                "input": _json.loads(tc["function"].get("arguments", "{}")),
            })

        # stop_reason normalisieren: OpenAI "tool_calls" → "tool_use"
        stop_reason = ch.get("finish_reason")
        if stop_reason == "tool_calls":
            stop_reason = "tool_use"

        return ChatResponse(
            content=content,
            model=raw.get("model", "unknown"),
            usage={
                "input_tokens": raw.get("usage", {}).get("prompt_tokens", 0),
                "output_tokens": raw.get("usage", {}).get("completion_tokens", 0),
            },
            provider=self.provider_name,
            stop_reason=stop_reason,
            content_blocks=content_blocks,
        )

    def _transform_stream_request(self, request: ChatRequest) -> dict:
        p = self._transform_request(request)
        p["stream"] = True
        p["stream_options"] = {"include_usage": True}
        return p

    def _parse_stream_chunk(self, raw_line: str) -> StreamChunk | None:
        try:
            ev = json.loads(raw_line)
        except json.JSONDecodeError:
            return None
        usage = ev.get("usage")
        if usage:
            return StreamChunk(type="usage", model=ev.get("model"), usage={
                "input_tokens": usage.get("prompt_tokens", 0),
                "output_tokens": usage.get("completion_tokens", 0),
            })
        choices = ev.get("choices", [])
        if not choices:
            return None
        ch = choices[0]
        if ch.get("finish_reason") == "stop":
            return StreamChunk(type="done", model=ev.get("model"))
        content = ch.get("delta", {}).get("content", "")
        if content:
            return StreamChunk(type="content_delta", content=content, model=ev.get("model"))
        return None

    # ─── Tier 1: Model Detail ──────────────────────────────────

    async def get_model_detail(self, model_id: str) -> ModelDetail:
        await self._ensure_client()
        h = dict(self._get_headers())
        h.pop("Content-Type", None)
        resp = await self._client.get(self._get_endpoint(f"/models/{model_id}"), headers=h)
        resp.raise_for_status()
        d = resp.json()
        return ModelDetail(
            id=d.get("id", model_id), name=d.get("id", model_id),
            provider=self.provider_name, created=d.get("created"),
            owned_by=d.get("owned_by"),
        )

    # ─── Tier 1: Token Count ───────────────────────────────────

    async def count_tokens(self, request: TokenCountRequest) -> TokenCountResponse:
        model = request.model or self.get_default_model()
        try:
            import tiktoken
            enc = tiktoken.encoding_for_model(model)
        except Exception:
            import tiktoken
            enc = tiktoken.get_encoding("cl100k_base")
        total = 0
        for m in request.messages:
            total += 4 + len(enc.encode(m.content)) + len(enc.encode(m.role))
        if request.system:
            total += 4 + len(enc.encode(request.system))
        total += 2
        return TokenCountResponse(input_tokens=total, model=model, provider=self.provider_name)

    # ═══════════════════════════════════════════════════════════
    # TIER 2: EXTENDED
    # ═══════════════════════════════════════════════════════════

    async def create_embedding(self, request: EmbeddingRequest) -> EmbeddingResponse:
        await self._ensure_client()
        model = request.model or self.config.get("embedding_model", "text-embedding-3-small")
        payload = {"model": model, "input": request.input}
        if request.encoding_format:
            payload["encoding_format"] = request.encoding_format
        if request.dimensions:
            payload["dimensions"] = request.dimensions
        resp = await self._client.post(
            self._get_endpoint("/embeddings"), headers=self._get_headers(), json=payload)
        resp.raise_for_status()
        raw = resp.json()
        data = [EmbeddingData(index=i["index"], embedding=i["embedding"]) for i in raw.get("data", [])]
        return EmbeddingResponse(
            data=data, model=raw.get("model", model),
            usage={"prompt_tokens": raw.get("usage", {}).get("prompt_tokens", 0),
                   "total_tokens": raw.get("usage", {}).get("total_tokens", 0)},
            provider=self.provider_name,
        )

    # ─── Batches ───────────────────────────────────────────────

    async def create_batch(self, request: BatchCreateRequest) -> BatchStatus:
        await self._ensure_client()
        model = request.model or self.get_default_model()
        lines = []
        for item in request.requests:
            p = dict(item.params)
            p.setdefault("model", model)
            lines.append(json.dumps({
                "custom_id": item.custom_id, "method": "POST",
                "url": "/v1/chat/completions", "body": p,
            }))
        jsonl = "\n".join(lines)
        h = dict(self._get_headers())
        h.pop("Content-Type", None)
        # Upload JSONL file
        fr = await self._client.post(
            self._get_endpoint("/files"), headers=h,
            files={"file": ("batch.jsonl", jsonl.encode(), "application/jsonl")},
            data={"purpose": "batch"},
        )
        fr.raise_for_status()
        file_id = fr.json()["id"]
        # Create batch
        resp = await self._client.post(
            self._get_endpoint("/batches"), headers=self._get_headers(),
            json={"input_file_id": file_id, "endpoint": "/v1/chat/completions",
                  "completion_window": "24h"},
        )
        resp.raise_for_status()
        return self._parse_batch(resp.json())

    async def list_batches(self) -> BatchListResponse:
        await self._ensure_client()
        h = dict(self._get_headers())
        h.pop("Content-Type", None)
        resp = await self._client.get(self._get_endpoint("/batches"), headers=h)
        resp.raise_for_status()
        return BatchListResponse(
            batches=[self._parse_batch(b) for b in resp.json().get("data", [])],
            provider=self.provider_name,
        )

    async def get_batch(self, batch_id: str) -> BatchStatus:
        await self._ensure_client()
        h = dict(self._get_headers())
        h.pop("Content-Type", None)
        resp = await self._client.get(self._get_endpoint(f"/batches/{batch_id}"), headers=h)
        resp.raise_for_status()
        return self._parse_batch(resp.json())

    async def cancel_batch(self, batch_id: str) -> BatchStatus:
        await self._ensure_client()
        resp = await self._client.post(
            self._get_endpoint(f"/batches/{batch_id}/cancel"), headers=self._get_headers())
        resp.raise_for_status()
        return self._parse_batch(resp.json())

    async def get_batch_results(self, batch_id: str) -> BatchResultsResponse:
        await self._ensure_client()
        h = dict(self._get_headers())
        h.pop("Content-Type", None)
        # Get output_file_id
        br = await self._client.get(self._get_endpoint(f"/batches/{batch_id}"), headers=h)
        br.raise_for_status()
        ofid = br.json().get("output_file_id")
        if not ofid:
            return BatchResultsResponse(batch_id=batch_id, results=[], provider=self.provider_name)
        # Download results
        fr = await self._client.get(self._get_endpoint(f"/files/{ofid}/content"), headers=h)
        fr.raise_for_status()
        results = []
        for line in fr.text.strip().split("\n"):
            if not line.strip():
                continue
            e = json.loads(line)
            results.append(BatchResultItem(
                custom_id=e.get("custom_id", ""),
                result=e.get("response", {}).get("body"),
                error=e.get("error"),
            ))
        return BatchResultsResponse(batch_id=batch_id, results=results, provider=self.provider_name)

    def _parse_batch(self, d: dict) -> BatchStatus:
        c = d.get("request_counts", {})
        return BatchStatus(
            id=d.get("id", ""), status=d.get("status", "unknown"),
            total_requests=c.get("total"), completed_requests=c.get("completed"),
            failed_requests=c.get("failed"), created_at=str(d.get("created_at", "")),
            ended_at=str(d.get("completed_at", "")), provider=self.provider_name,
        )

    # ═══════════════════════════════════════════════════════════
    # TIER 3: SPECIALIZED
    # ═══════════════════════════════════════════════════════════

    async def create_moderation(self, request: ModerationRequest) -> ModerationResponse:
        await self._ensure_client()
        payload = {"input": request.input}
        if request.model:
            payload["model"] = request.model
        resp = await self._client.post(
            self._get_endpoint("/moderations"), headers=self._get_headers(), json=payload)
        resp.raise_for_status()
        raw = resp.json()
        return ModerationResponse(
            id=raw.get("id", ""),
            results=[ModerationResult(flagged=r["flagged"], categories=r["categories"],
                                      category_scores=r["category_scores"])
                     for r in raw.get("results", [])],
            model=raw.get("model", ""), provider=self.provider_name,
        )

    async def transcribe_audio(self, file_bytes: bytes, filename: str, **kw) -> AudioResponse:
        await self._ensure_client()
        model = kw.get("model") or self.config.get("audio_model", "whisper-1")
        h = dict(self._get_headers())
        h.pop("Content-Type", None)
        data = {"model": model}
        for k in ("language", "prompt", "response_format"):
            if kw.get(k):
                data[k] = kw[k]
        if kw.get("temperature") is not None:
            data["temperature"] = str(kw["temperature"])
        resp = await self._client.post(
            self._get_endpoint("/audio/transcriptions"), headers=h,
            files={"file": (filename, file_bytes, "audio/mpeg")}, data=data,
        )
        resp.raise_for_status()
        raw = resp.json() if "application/json" in resp.headers.get("content-type", "") else {"text": resp.text}
        return AudioResponse(text=raw.get("text", resp.text), model=model, provider=self.provider_name)

    async def translate_audio(self, file_bytes: bytes, filename: str, **kw) -> AudioResponse:
        await self._ensure_client()
        model = kw.get("model") or self.config.get("audio_model", "whisper-1")
        h = dict(self._get_headers())
        h.pop("Content-Type", None)
        data = {"model": model}
        for k in ("prompt", "response_format"):
            if kw.get(k):
                data[k] = kw[k]
        if kw.get("temperature") is not None:
            data["temperature"] = str(kw["temperature"])
        resp = await self._client.post(
            self._get_endpoint("/audio/translations"), headers=h,
            files={"file": (filename, file_bytes, "audio/mpeg")}, data=data,
        )
        resp.raise_for_status()
        raw = resp.json() if "application/json" in resp.headers.get("content-type", "") else {"text": resp.text}
        return AudioResponse(text=raw.get("text", resp.text), model=model, provider=self.provider_name)

    async def create_speech(self, request: AudioSpeechRequest) -> bytes:
        await self._ensure_client()
        model = request.model or self.config.get("tts_model", "tts-1")
        payload = {"model": model, "input": request.input, "voice": request.voice}
        if request.response_format:
            payload["response_format"] = request.response_format
        if request.speed is not None:
            payload["speed"] = request.speed
        resp = await self._client.post(
            self._get_endpoint("/audio/speech"), headers=self._get_headers(), json=payload)
        resp.raise_for_status()
        return resp.content

    async def generate_image(self, request: ImageGenerationRequest) -> ImageResponse:
        await self._ensure_client()
        model = request.model or self.config.get("image_model", "dall-e-3")
        payload = {"model": model, "prompt": request.prompt, "n": request.n}
        for k in ("size", "quality", "style", "response_format"):
            v = getattr(request, k, None)
            if v:
                payload[k] = v
        resp = await self._client.post(
            self._get_endpoint("/images/generations"), headers=self._get_headers(), json=payload)
        resp.raise_for_status()
        return self._parse_img(resp.json(), model)

    async def edit_image(self, image_bytes: bytes, request: ImageEditRequest,
                         mask_bytes: bytes | None = None) -> ImageResponse:
        await self._ensure_client()
        model = request.model or "dall-e-2"
        h = dict(self._get_headers())
        h.pop("Content-Type", None)
        files = {"image": ("image.png", image_bytes, "image/png")}
        if mask_bytes:
            files["mask"] = ("mask.png", mask_bytes, "image/png")
        data = {"prompt": request.prompt, "model": model, "n": str(request.n)}
        if request.size:
            data["size"] = request.size
        resp = await self._client.post(
            self._get_endpoint("/images/edits"), headers=h, files=files, data=data)
        resp.raise_for_status()
        return self._parse_img(resp.json(), model)

    async def create_image_variation(self, image_bytes: bytes,
                                     request: ImageVariationRequest) -> ImageResponse:
        await self._ensure_client()
        model = request.model or "dall-e-2"
        h = dict(self._get_headers())
        h.pop("Content-Type", None)
        files = {"image": ("image.png", image_bytes, "image/png")}
        data = {"model": model, "n": str(request.n)}
        if request.size:
            data["size"] = request.size
        resp = await self._client.post(
            self._get_endpoint("/images/variations"), headers=h, files=files, data=data)
        resp.raise_for_status()
        return self._parse_img(resp.json(), model)

    def _parse_img(self, raw: dict, model: str) -> ImageResponse:
        return ImageResponse(
            data=[ImageData(url=i.get("url"), b64_json=i.get("b64_json"),
                            revised_prompt=i.get("revised_prompt"))
                  for i in raw.get("data", [])],
            model=model, provider=self.provider_name,
        )
