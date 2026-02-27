"""
HEINZEL-HOST Provider Gateway – Pydantic Models
All tiers: Core, Extended, Specialized
"""
from pydantic import BaseModel
from typing import Optional, Literal, Union, Annotated
from pydantic import Field


# ─── Content Blocks (Multimodal) ──────────────────────────────

class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImageBlock(BaseModel):
    type: Literal["image"] = "image"
    media_type: Literal["image/jpeg", "image/png", "image/gif", "image/webp"]
    data: str  # base64-encoded


class DocumentBlock(BaseModel):
    type: Literal["document"] = "document"
    media_type: Literal["application/pdf"] = "application/pdf"
    data: str  # base64-encoded


ContentBlock = Annotated[
    Union[TextBlock, ImageBlock, DocumentBlock],
    Field(discriminator="type")
]

# Unified content type: str (Text) oder Liste von Blöcken (Multimodal)
MessageContent = Union[str, list[ContentBlock]]


# ─── Context & Common ─────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: MessageContent = ""


class RequestContext(BaseModel):
    heinzel_id: Optional[str] = None
    session_id: Optional[str] = None
    task_id: Optional[str] = None


# ─── Tier 1: Core ─────────────────────────────────────────────────

class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    model: Optional[str] = None
    max_tokens: int = 1024
    system: Optional[str] = None
    temperature: Optional[float] = None
    top_p: Optional[float] = None
    stop_sequences: Optional[list[str]] = None
    tools: Optional[list[dict]] = None
    context: Optional[RequestContext] = None


class ChatResponse(BaseModel):
    content: str
    model: str
    usage: dict
    provider: str
    stop_reason: Optional[str] = None
    content_blocks: Optional[list[dict]] = None


class StreamChunk(BaseModel):
    type: str  # content_delta | usage | done | error | command_response
    content: Optional[str] = None
    model: Optional[str] = None
    usage: Optional[dict] = None
    error: Optional[str] = None
    command: Optional[str] = None   # Kommando das ausgeführt wurde
    result: Optional[dict] = None   # Kommando-Ergebnis (bei command_response)


class TokenCountRequest(BaseModel):
    messages: list[ChatMessage]
    model: Optional[str] = None
    system: Optional[str] = None
    tools: Optional[list[dict]] = None


class TokenCountResponse(BaseModel):
    input_tokens: int
    model: str
    provider: str


class ModelDetail(BaseModel):
    id: str
    name: Optional[str] = None
    provider: str
    created: Optional[int] = None
    owned_by: Optional[str] = None
    context_window: Optional[int] = None
    max_output_tokens: Optional[int] = None


class ModelsResponse(BaseModel):
    models: list[str]
    default: str
    provider: str


class ModelDetailResponse(BaseModel):
    model: ModelDetail
    provider: str


# ─── Tier 2: Extended ─────────────────────────────────────────────

class EmbeddingRequest(BaseModel):
    input: str | list[str]
    model: Optional[str] = None
    encoding_format: Optional[str] = None
    dimensions: Optional[int] = None
    context: Optional[RequestContext] = None


class EmbeddingData(BaseModel):
    index: int
    embedding: list[float]
    object: str = "embedding"


class EmbeddingResponse(BaseModel):
    data: list[EmbeddingData]
    model: str
    usage: dict
    provider: str


class BatchRequestItem(BaseModel):
    custom_id: str
    params: dict


class BatchCreateRequest(BaseModel):
    requests: list[BatchRequestItem]
    model: Optional[str] = None
    context: Optional[RequestContext] = None


class BatchStatus(BaseModel):
    id: str
    status: str
    total_requests: Optional[int] = None
    completed_requests: Optional[int] = None
    failed_requests: Optional[int] = None
    created_at: Optional[str] = None
    ended_at: Optional[str] = None
    provider: str


class BatchListResponse(BaseModel):
    batches: list[BatchStatus]
    provider: str


class BatchResultItem(BaseModel):
    custom_id: str
    result: Optional[dict] = None
    error: Optional[dict] = None


class BatchResultsResponse(BaseModel):
    batch_id: str
    results: list[BatchResultItem]
    provider: str


# ─── Tier 3: Specialized ──────────────────────────────────────────

class ModerationRequest(BaseModel):
    input: str | list[str]
    model: Optional[str] = None
    context: Optional[RequestContext] = None


class ModerationResult(BaseModel):
    flagged: bool
    categories: dict
    category_scores: dict


class ModerationResponse(BaseModel):
    id: str
    results: list[ModerationResult]
    model: str
    provider: str


class AudioTranscriptionRequest(BaseModel):
    model: Optional[str] = None
    language: Optional[str] = None
    prompt: Optional[str] = None
    response_format: Optional[str] = None
    temperature: Optional[float] = None
    context: Optional[RequestContext] = None


class AudioTranslationRequest(BaseModel):
    model: Optional[str] = None
    prompt: Optional[str] = None
    response_format: Optional[str] = None
    temperature: Optional[float] = None
    context: Optional[RequestContext] = None


class AudioSpeechRequest(BaseModel):
    input: str
    model: Optional[str] = None
    voice: str = "alloy"
    response_format: Optional[str] = None
    speed: Optional[float] = None
    context: Optional[RequestContext] = None


class AudioResponse(BaseModel):
    text: str
    model: str
    provider: str


class ImageGenerationRequest(BaseModel):
    prompt: str
    model: Optional[str] = None
    n: int = 1
    size: Optional[str] = None
    quality: Optional[str] = None
    style: Optional[str] = None
    response_format: Optional[str] = None
    context: Optional[RequestContext] = None


class ImageData(BaseModel):
    url: Optional[str] = None
    b64_json: Optional[str] = None
    revised_prompt: Optional[str] = None


class ImageResponse(BaseModel):
    data: list[ImageData]
    model: str
    provider: str


class ImageEditRequest(BaseModel):
    prompt: str
    model: Optional[str] = None
    n: int = 1
    size: Optional[str] = None
    response_format: Optional[str] = None
    context: Optional[RequestContext] = None


class ImageVariationRequest(BaseModel):
    model: Optional[str] = None
    n: int = 1
    size: Optional[str] = None
    response_format: Optional[str] = None
    context: Optional[RequestContext] = None


# ─── Gateway Meta ──────────────────────────────────────────────────

class CapabilityTier(BaseModel):
    core: list[str]
    extended: list[str]
    specialized: list[str]


class CapabilitiesResponse(BaseModel):
    provider: str
    tiers: CapabilityTier
    features: dict


class HealthResponse(BaseModel):
    status: str
    provider: str
    timestamp: str


class ConnectionStatus(BaseModel):
    status: str
    provider: str
    timestamp: str
    reset: Optional[bool] = None


class NotImplementedResponse(BaseModel):
    error: str = "not_yet_implemented"
    endpoint: str
    provider: str
    message: str = "This endpoint is not available for this provider"
