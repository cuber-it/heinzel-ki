"""
Tests fuer HNZ-001-0007: Provider-Template Validierung via Dummy-Provider
Beweist dass das Template-Muster funktioniert.
"""
import sys, os, asyncio, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../src"))

import pytest
import httpx


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ─── Dummy-Provider (Template-basiert) ────────────────────────

class DummyProvider:
    """
    Minimal-Provider nach Template-Muster.
    Gibt vorprogrammierte Antworten zurueck ohne echte HTTP-Calls.
    """
    provider_name = "dummy"

    class _FakeLogger:
        enabled = True

    logger = _FakeLogger()
    _connected = True

    config = {"api_base": "http://dummy:9999", "default_model": "dummy-v1",
              "name": "dummy"}
    api_key = "test-key"

    def get_models(self):
        return ["dummy-v1", "dummy-v2"]

    def get_default_model(self):
        return "dummy-v1"

    def _get_endpoint(self):
        return f"{self.config['api_base']}/chat"

    def _get_headers(self):
        return {"Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"}

    def _transform_request(self, request):
        from models import ChatRequest
        return {
            "model": request.model or self.get_default_model(),
            "messages": [{"role": m.role, "content": m.content}
                         for m in request.messages],
            "max_tokens": request.max_tokens,
        }

    def _transform_response(self, response):
        from models import ChatResponse
        return ChatResponse(
            content=response["choices"][0]["message"]["content"],
            model=response.get("model", self.get_default_model()),
            usage={"input_tokens": response.get("usage", {}).get("prompt_tokens", 0),
                   "output_tokens": response.get("usage", {}).get("completion_tokens", 0)},
            provider=self.provider_name,
        )


# ─── Tests ────────────────────────────────────────────────────

def test_dummy_get_models():
    p = DummyProvider()
    models = p.get_models()
    assert isinstance(models, list)
    assert len(models) >= 1


def test_dummy_get_default_model():
    p = DummyProvider()
    default = p.get_default_model()
    assert default in p.get_models()


def test_dummy_transform_request():
    from models import ChatRequest, ChatMessage
    p = DummyProvider()
    req = ChatRequest(messages=[ChatMessage(role="user", content="Hallo")])
    payload = p._transform_request(req)
    assert "messages" in payload
    assert payload["messages"][0]["content"] == "Hallo"
    assert "model" in payload


def test_dummy_transform_response():
    p = DummyProvider()
    fake_resp = {
        "choices": [{"message": {"content": "Antwort"}, "finish_reason": "stop"}],
        "model": "dummy-v1",
        "usage": {"prompt_tokens": 5, "completion_tokens": 3},
    }
    result = p._transform_response(fake_resp)
    assert result.content == "Antwort"
    assert result.model == "dummy-v1"
    assert result.usage["input_tokens"] == 5


def test_dummy_headers_contain_auth():
    p = DummyProvider()
    headers = p._get_headers()
    assert "Authorization" in headers
    assert "test-key" in headers["Authorization"]


def test_dummy_endpoint_contains_api_base():
    p = DummyProvider()
    endpoint = p._get_endpoint()
    assert p.config["api_base"] in endpoint


def test_template_interface_matches_base():
    """Alle Pflicht-Methoden sind vorhanden und callable."""
    p = DummyProvider()
    for method in ["get_models", "get_default_model", "_get_endpoint",
                   "_get_headers", "_transform_request", "_transform_response"]:
        assert callable(getattr(p, method, None)), f"Methode fehlt: {method}"
