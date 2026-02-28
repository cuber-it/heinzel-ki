"""
H.E.I.N.Z.E.L. Provider Gateway – FastAPI App
Version 2.0.0 – Alle Endpoints, alle Tiers.
"""
import logging
import os
import yaml
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import StreamingResponse, Response

from models import (
    ChatRequest, ChatResponse, TokenCountRequest, TokenCountResponse,
    ModelsResponse, ModelDetailResponse, EmbeddingRequest, EmbeddingResponse,
    BatchCreateRequest, BatchStatus, BatchListResponse, BatchResultsResponse,
    ModerationRequest, ModerationResponse, AudioSpeechRequest,
    AudioResponse, ImageGenerationRequest, ImageResponse,
    ImageEditRequest, ImageVariationRequest,
    CapabilitiesResponse, HealthResponse, ConnectionStatus,
)
from anthropic_provider import AnthropicProvider
from openai_provider import OpenAIProvider
from google_provider import GoogleProvider
from base import BaseProvider, EndpointNotAvailable
from database import cost_logger
from config import instance_config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
provider: BaseProvider | None = None

# Dialog-Logging: instance_config (ENV > YAML > Default: true)
_log_requests = instance_config.log_requests()


def load_config() -> dict:
    path = os.environ.get("CONFIG_PATH", "/config/anthropic.yaml")
    with open(path) as f:
        config = yaml.safe_load(f)
    # Pflichtfelder validieren
    for field in ("name", "api_base", "default_model"):
        if not config.get(field):
            raise ValueError(f"Config-Fehler: Pflichtfeld '{field}' fehlt in {path}")
    return config


def create_provider(config: dict) -> BaseProvider:
    pt = os.environ.get("PROVIDER_TYPE", "anthropic")
    if pt == "anthropic":
        return AnthropicProvider(config)
    if pt == "openai":
        return OpenAIProvider(config)
    if pt == "google":
        return GoogleProvider(config)
    raise ValueError(f"Unknown provider: {pt}")


def _check_api_key(provider_type: str) -> None:
    """Fail-Fast: bricht den Start ab wenn der API-Key fehlt."""
    key_map = {
        "anthropic": "ANTHROPIC_API_KEY",
        "openai":    "OPENAI_API_KEY",
        "google":    "GOOGLE_API_KEY",
    }
    env_var = key_map.get(provider_type)
    if env_var is None:
        return  # Custom Provider, kein Standard-Key-Check
    key = instance_config.api_key(env_var)
    if not key or key.startswith("sk-...") or key.startswith("sk-ant-..."):
        msg = (
            f"FATAL: API-Key fehlt fuer Provider '{provider_type}'. "
            f"Bitte {env_var} als Umgebungsvariable oder in instance.yaml setzen."
        )
        logger.error(msg)
        raise RuntimeError(msg)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global provider
    config = load_config()
    pt = os.environ.get("PROVIDER_TYPE", "anthropic")
    _check_api_key(pt)
    provider = create_provider(config)
    provider.connect()
    await cost_logger.connect()
    logger.info(f"Provider started: {provider.provider_name} | dialog_logging={_log_requests}")
    # Retention-Cleanup beim Start
    ret = instance_config.retention()
    log_dir = os.environ.get("LOG_DIR", "/data")
    cleanup_logs(log_dir, max_age_days=ret["log_max_age_days"],
                 max_size_mb=ret["log_max_size_mb"], compress=ret["log_compress"])
    await cleanup_metrics_db(
        cost_logger._db_type, cost_logger._sqlite_path or cost_logger._db_url or "",
        max_age_days=ret["metrics_max_age_days"]
    )
    yield
    if provider:
        provider.disconnect()
        await cost_logger.disconnect()


app = FastAPI(
    title="H.E.I.N.Z.E.L. Provider Gateway",
    description="Unified LLM Provider Gateway – All Tiers",
    version="2.0.0", lifespan=lifespan,
)


def _handle(e: Exception, ep: str):
    if isinstance(e, EndpointNotAvailable):
        raise HTTPException(status_code=501, detail=e.detail.model_dump())
    logger.error(f"{ep}: {e}")
    raise HTTPException(status_code=500, detail=str(e))


# ═══════════════════════════════════════════════════════════════
# LOGGING CONTROL (Runtime)
# ═══════════════════════════════════════════════════════════════

@app.post("/logging/enable")
async def logging_enable():
    provider.logger.enabled = True
    return {"dialog_logging": True}

@app.post("/logging/disable")
async def logging_disable():
    provider.logger.enabled = False
    return {"dialog_logging": False}

@app.get("/logging/status")
async def logging_status():
    return {"dialog_logging": provider.logger.enabled}


# ═══════════════════════════════════════════════════════════════
# RETENTION (Log-Cleanup)
# ═══════════════════════════════════════════════════════════════

@app.post("/retention/run")
async def retention_run():
    """Retention-Cleanup manuell auslösen (Logs + Metriken-DB)."""
    ret = instance_config.retention()
    log_dir = os.environ.get("LOG_DIR", "/data")
    log_result = cleanup_logs(
        log_dir,
        max_age_days=ret["log_max_age_days"],
        max_size_mb=ret["log_max_size_mb"],
        compress=ret["log_compress"],
    )
    db_result = await cleanup_metrics_db(
        cost_logger._db_type,
        cost_logger._sqlite_path or getattr(cost_logger, "_db_url", "") or "",
        max_age_days=ret["metrics_max_age_days"],
    )
    return {"logs": log_result, "metrics_db": db_result, "policy": ret}


# ═══════════════════════════════════════════════════════════════
# LOG ABRUF (Dialog-Logs)
# ═══════════════════════════════════════════════════════════════

from log_reader import read_logs
from typing import Optional
from commands import is_command, extract_command, execute_command
from retention import cleanup_logs, cleanup_metrics_db

@app.get("/logs")
async def logs(
    session_id: Optional[str] = None,
    heinzel_id: Optional[str] = None,
    task_id:    Optional[str] = None,
    type:       Optional[str] = None,   # request|response|error
    since:      Optional[str] = None,   # ISO-Datetime
    until:      Optional[str] = None,
    limit:      int = 100,
):
    """
    Gibt Dialog-Log-Einträge zurück. Neueste zuerst.
    Filter kombinierbar: ?session_id=X&type=request&since=2026-02-27T00:00:00Z
    """
    log_dir = os.environ.get("LOG_DIR", "/data")
    entries = read_logs(
        log_dir=log_dir,
        provider=provider.provider_name,
        session_id=session_id,
        heinzel_id=heinzel_id,
        task_id=task_id,
        entry_type=type,
        since=since,
        until=until,
        limit=min(limit, 1000),
    )
    return {"count": len(entries), "entries": entries}


# ═══════════════════════════════════════════════════════════════
# METRIKEN (Cost/Token-DB)
# ═══════════════════════════════════════════════════════════════

@app.get("/metrics")
async def metrics(
    session_id: Optional[str] = None,
    heinzel_id: Optional[str] = None,
    model:      Optional[str] = None,
    since:      Optional[str] = None,
    until:      Optional[str] = None,
    status:     Optional[str] = None,
    limit:      int = 100,
):
    """Rohe Metriken-Einträge aus der DB. Neueste zuerst."""
    rows = await cost_logger.query(
        session_id=session_id, heinzel_id=heinzel_id,
        provider=provider.provider_name, model=model,
        since=since, until=until, status=status, limit=limit,
    )
    return {"count": len(rows), "entries": rows}


@app.get("/metrics/rate-limits")
async def metrics_rate_limits():
    """Anzahl der Rate-Limit-Hits (429) seit Provider-Start."""
    hits = getattr(provider, "_rate_limit_hits", [])
    return {
        "total_hits": len(hits),
        "last_hit": hits[-1] if hits else None,
        "retry_config": provider.config.get("retry", {}),
    }


@app.get("/metrics/summary")
async def metrics_summary(
    session_id: Optional[str] = None,
    heinzel_id: Optional[str] = None,
    since:      Optional[str] = None,
    until:      Optional[str] = None,
):
    """Aggregierte Metriken: Requests, Tokens, Latenz, Fehler."""
    return await cost_logger.summary(
        session_id=session_id, heinzel_id=heinzel_id,
        since=since, until=until,
    )




@app.get("/status")
async def status():
    """Provider-Status: health, model, logging, rate-limits."""
    return {
        "provider":         provider.provider_name,
        "connected":        provider._connected,
        "status":           "ok" if provider._connected else "disconnected",
        "default_model":    provider.get_default_model(),
        "available_models": provider.get_models(),
        "dialog_logging":   provider.logger.enabled,
        "rate_limit_hits":  len(getattr(provider, "_rate_limit_hits", [])),
        "retry_config":     provider.config.get("retry", {}),
    }


@app.get("/health", response_model=HealthResponse)
async def health():
    return provider.health()

@app.get("/capabilities", response_model=CapabilitiesResponse)
async def capabilities():
    return provider.get_capabilities()

@app.post("/connect", response_model=ConnectionStatus)
async def connect():
    return provider.connect()

@app.post("/disconnect", response_model=ConnectionStatus)
async def disconnect():
    return provider.disconnect()

@app.post("/reset", response_model=ConnectionStatus)
async def reset():
    return provider.reset()


# ═══════════════════════════════════════════════════════════════
# TIER 1: CORE
# ═══════════════════════════════════════════════════════════════

@app.get("/models", response_model=ModelsResponse)
async def models_list():
    return ModelsResponse(
        models=provider.get_models(), default=provider.get_default_model(),
        provider=provider.provider_name,
    )

@app.get("/models/{model_id}", response_model=ModelDetailResponse)
async def model_detail(model_id: str):
    try:
        d = await provider.get_model_detail(model_id)
        return ModelDetailResponse(model=d, provider=provider.provider_name)
    except Exception as e:
        _handle(e, "GET /models/{id}")

@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    # Letztes Kommando im Message-Strom abfangen
    if request.messages:
        last = request.messages[-1]
        if last.role == "user" and is_command(str(last.content)):
            cmd, args = extract_command(str(last.content))
            result = execute_command(cmd, args, provider)
            from models import ChatResponse
            return ChatResponse(
                content=f"[!{cmd}] {result}",
                model=provider.get_default_model(),
                usage={"input_tokens": 0, "output_tokens": 0},
                provider=provider.provider_name,
            )
    try:
        return await provider.chat(request)
    except Exception as e:
        _handle(e, "POST /chat")


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    # Letztes Kommando im Message-Strom abfangen
    if request.messages:
        last = request.messages[-1]
        if last.role == "user" and is_command(str(last.content)):
            cmd, args = extract_command(str(last.content))
            result = execute_command(cmd, args, provider)
            from models import StreamChunk
            async def cmd_sse():
                chunk = StreamChunk(type="command_response", command=cmd, result=result)
                yield f"data: {chunk.model_dump_json()}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(cmd_sse(), media_type="text/event-stream", headers={
                "Cache-Control": "no-cache", "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            })
    async def sse():
        async for chunk in provider.chat_stream(request):
            yield f"data: {chunk.model_dump_json()}\n\n"
        yield "data: [DONE]\n\n"
    return StreamingResponse(sse(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache", "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
    })

@app.post("/tokens/count", response_model=TokenCountResponse)
async def tokens_count(request: TokenCountRequest):
    try:
        return await provider.count_tokens(request)
    except Exception as e:
        _handle(e, "POST /tokens/count")


# ═══════════════════════════════════════════════════════════════
# TIER 2: EXTENDED
# ═══════════════════════════════════════════════════════════════

@app.post("/embeddings", response_model=EmbeddingResponse)
async def embeddings(request: EmbeddingRequest):
    try:
        return await provider.create_embedding(request)
    except Exception as e:
        _handle(e, "POST /embeddings")

@app.post("/batches", response_model=BatchStatus)
async def create_batch(request: BatchCreateRequest):
    try:
        return await provider.create_batch(request)
    except Exception as e:
        _handle(e, "POST /batches")

@app.get("/batches", response_model=BatchListResponse)
async def list_batches():
    try:
        return await provider.list_batches()
    except Exception as e:
        _handle(e, "GET /batches")

@app.get("/batches/{batch_id}", response_model=BatchStatus)
async def get_batch(batch_id: str):
    try:
        return await provider.get_batch(batch_id)
    except Exception as e:
        _handle(e, "GET /batches/{id}")

@app.post("/batches/{batch_id}/cancel", response_model=BatchStatus)
async def cancel_batch(batch_id: str):
    try:
        return await provider.cancel_batch(batch_id)
    except Exception as e:
        _handle(e, "POST /batches/{id}/cancel")

@app.get("/batches/{batch_id}/results", response_model=BatchResultsResponse)
async def batch_results(batch_id: str):
    try:
        return await provider.get_batch_results(batch_id)
    except Exception as e:
        _handle(e, "GET /batches/{id}/results")


# ═══════════════════════════════════════════════════════════════
# TIER 3: SPECIALIZED
# ═══════════════════════════════════════════════════════════════

@app.post("/moderations", response_model=ModerationResponse)
async def moderations(request: ModerationRequest):
    try:
        return await provider.create_moderation(request)
    except Exception as e:
        _handle(e, "POST /moderations")

@app.post("/audio/transcriptions", response_model=AudioResponse)
async def audio_transcriptions(
    file: UploadFile = File(...), model: str = Form(None),
    language: str = Form(None), prompt: str = Form(None),
    response_format: str = Form(None), temperature: float = Form(None),
):
    try:
        content = await file.read()
        return await provider.transcribe_audio(
            content, file.filename, model=model, language=language,
            prompt=prompt, response_format=response_format, temperature=temperature,
        )
    except Exception as e:
        _handle(e, "POST /audio/transcriptions")

@app.post("/audio/translations", response_model=AudioResponse)
async def audio_translations(
    file: UploadFile = File(...), model: str = Form(None),
    prompt: str = Form(None), response_format: str = Form(None),
    temperature: float = Form(None),
):
    try:
        content = await file.read()
        return await provider.translate_audio(
            content, file.filename, model=model, prompt=prompt,
            response_format=response_format, temperature=temperature,
        )
    except Exception as e:
        _handle(e, "POST /audio/translations")

@app.post("/audio/speech")
async def audio_speech(request: AudioSpeechRequest):
    try:
        audio = await provider.create_speech(request)
        fmt = request.response_format or "mp3"
        mt = {"mp3": "audio/mpeg", "opus": "audio/opus", "aac": "audio/aac",
              "flac": "audio/flac", "wav": "audio/wav", "pcm": "audio/pcm"}
        return Response(content=audio, media_type=mt.get(fmt, "audio/mpeg"),
                        headers={"Content-Disposition": f'attachment; filename="speech.{fmt}"'})
    except Exception as e:
        _handle(e, "POST /audio/speech")

@app.post("/images/generations", response_model=ImageResponse)
async def image_generations(request: ImageGenerationRequest):
    try:
        return await provider.generate_image(request)
    except Exception as e:
        _handle(e, "POST /images/generations")

@app.post("/images/edits", response_model=ImageResponse)
async def image_edits(
    image: UploadFile = File(...), prompt: str = Form(...),
    model: str = Form(None), n: int = Form(1),
    size: str = Form(None), response_format: str = Form(None),
    mask: UploadFile = File(None),
):
    try:
        ib = await image.read()
        mb = await mask.read() if mask else None
        req = ImageEditRequest(prompt=prompt, model=model, n=n,
                               size=size, response_format=response_format)
        return await provider.edit_image(ib, req, mb)
    except Exception as e:
        _handle(e, "POST /images/edits")

@app.post("/images/variations", response_model=ImageResponse)
async def image_variations(
    image: UploadFile = File(...), model: str = Form(None),
    n: int = Form(1), size: str = Form(None),
    response_format: str = Form(None),
):
    try:
        ib = await image.read()
        req = ImageVariationRequest(model=model, n=n, size=size,
                                    response_format=response_format)
        return await provider.create_image_variation(ib, req)
    except Exception as e:
        _handle(e, "POST /images/variations")
