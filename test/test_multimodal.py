"""
Tests fuer HNZ-001-0011: Multimodal-Support (Bild, PDF, Dokument)
"""
import os, sys
os.environ.setdefault("LOG_DIR", "/tmp")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))

from models import ChatMessage, TextBlock, ImageBlock, DocumentBlock


# ─── Datenmodell ─────────────────────────────────────────────

def test_message_str_content():
    m = ChatMessage(role="user", content="hallo")
    assert m.content == "hallo"


def test_message_multimodal_content():
    m = ChatMessage(role="user", content=[
        TextBlock(text="schau dir das an"),
        ImageBlock(media_type="image/jpeg", data="abc123"),
    ])
    assert m.content[0].type == "text"
    assert m.content[1].type == "image"
    assert m.content[1].media_type == "image/jpeg"


def test_message_document_content():
    m = ChatMessage(role="user", content=[
        TextBlock(text="hier das pdf"),
        DocumentBlock(data="pdfbase64"),
    ])
    assert m.content[1].type == "document"
    assert m.content[1].media_type == "application/pdf"


def test_message_empty_defaults():
    m = ChatMessage(role="user")
    assert m.content == ""


# ─── base._content_to_parts ──────────────────────────────────

def _fake_provider():
    from base import BaseProvider
    class FP(BaseProvider):
        provider_name = "test"
        def get_models(self): return []
        def get_default_model(self): return "x"
        def _get_headers(self): return {}
        def _get_endpoint(self, p=""): return ""
        def _transform_request(self, r): return {}
        def _transform_response(self, r): return None
        def _transform_stream_request(self, r): return {}
        def _parse_stream_chunk(self, l): return None
    return FP({"name": "test", "api_base": "http://x", "default_model": "x"})


def test_content_to_parts_str():
    p = _fake_provider()
    parts = p._content_to_parts("hallo")
    assert parts == [{"type": "text", "text": "hallo"}]


def test_content_to_parts_list():
    p = _fake_provider()
    parts = p._content_to_parts([
        TextBlock(text="hi"),
        ImageBlock(media_type="image/png", data="abc"),
    ])
    assert parts[0]["type"] == "text"
    assert parts[1]["type"] == "image"


# ─── Anthropic ───────────────────────────────────────────────

def _anthropic():
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-test"
    from anthropic_provider import AnthropicProvider
    return AnthropicProvider({"name": "anthropic", "api_base": "http://x", "default_model": "claude-3-5-sonnet-20241022"})


def test_anthropic_str_passthrough():
    ap = _anthropic()
    assert ap._anthropic_content("hallo") == "hallo"


def test_anthropic_image():
    ap = _anthropic()
    r = ap._anthropic_content([TextBlock(text="schau"), ImageBlock(media_type="image/jpeg", data="b64")])
    assert r[0] == {"type": "text", "text": "schau"}
    assert r[1]["type"] == "image"
    assert r[1]["source"]["type"] == "base64"
    assert r[1]["source"]["media_type"] == "image/jpeg"


def test_anthropic_pdf():
    ap = _anthropic()
    r = ap._anthropic_content([DocumentBlock(data="pdfb64")])
    assert r[0]["type"] == "document"
    assert r[0]["source"]["media_type"] == "application/pdf"
    assert r[0]["source"]["data"] == "pdfb64"


# ─── OpenAI ──────────────────────────────────────────────────

def _openai():
    os.environ["OPENAI_API_KEY"] = "sk-test"
    from openai_provider import OpenAIProvider
    return OpenAIProvider({"name": "openai", "api_base": "http://x", "default_model": "gpt-4o"})


def test_openai_str_passthrough():
    op = _openai()
    assert op._openai_content("hallo") == "hallo"


def test_openai_image():
    op = _openai()
    r = op._openai_content([TextBlock(text="schau"), ImageBlock(media_type="image/png", data="b64")])
    assert r[0] == {"type": "text", "text": "schau"}
    assert r[1]["type"] == "image_url"
    assert r[1]["image_url"]["url"] == "data:image/png;base64,b64"


def test_openai_pdf_extracted_as_text():
    """OpenAI: PDF wird als Text extrahiert statt Exception"""
    import base64
    op = _openai()
    fake_pdf = base64.b64encode(b"%PDF-1.4 fake").decode()
    r = op._openai_content([DocumentBlock(data=fake_pdf)])
    if isinstance(r, str):
        assert len(r) > 0
    else:
        assert r[0]["type"] == "text"


# ─── Google ──────────────────────────────────────────────────

def _google():
    os.environ["GOOGLE_API_KEY"] = "test-key"
    from google_provider import GoogleProvider
    return GoogleProvider({"name": "google", "api_base": "http://x", "default_model": "gemini-1.5-pro"})


def test_google_str():
    gp = _google()
    parts = gp._gemini_parts("hallo")
    assert parts == [{"text": "hallo"}]


def test_google_image():
    gp = _google()
    parts = gp._gemini_parts([TextBlock(text="hi"), ImageBlock(media_type="image/jpeg", data="b64")])
    assert parts[0] == {"text": "hi"}
    assert parts[1]["inline_data"]["mime_type"] == "image/jpeg"
    assert parts[1]["inline_data"]["data"] == "b64"


def test_google_pdf():
    gp = _google()
    parts = gp._gemini_parts([DocumentBlock(data="pdfb64")])
    assert parts[0]["inline_data"]["mime_type"] == "application/pdf"
    assert parts[0]["inline_data"]["data"] == "pdfb64"
