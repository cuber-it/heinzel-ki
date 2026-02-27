# Neuen Provider hinzufügen — Schritt-für-Schritt

## Überblick

Der LLM Provider ist so gebaut, dass jeder neue Provider nur eine Python-Datei
und eine YAML-Config braucht. Der Rest (HTTP-Layer, Logging, Metriken, Kommandos,
Retention) ist fertig.

Nächste Kandidaten: Ollama (lokal), Mistral, Cohere, xAI/Grok.

---

## 1. Provider-Klasse anlegen

```bash
cd services/llm_provider/src
cp ../provider_template/template_provider.py myprovider_provider.py
```

Öffne `myprovider_provider.py` und implementiere die 5 markierten Methoden:

| Methode | Was | Beispiel |
|---|---|---|
| `get_models()` | Modell-IDs | `["gpt-4o", "gpt-4o-mini"]` |
| `get_default_model()` | Standard | `"gpt-4o"` |
| `_get_endpoint()` | API-URL | `f"{self.config['api_base']}/chat/completions"` |
| `_get_headers()` | Auth | `{"Authorization": f"Bearer {self.api_key}"}` |
| `_transform_request()` | ChatRequest → dict | Provider-Format bauen |
| `_transform_response()` | dict → ChatResponse | Felder extrahieren |

Streaming ist optional — `_transform_stream_request()` und `_parse_stream_chunk()`
überschreiben falls der Provider SSE unterstützt.

---

## 2. Provider in main.py registrieren

In `services/llm_provider/src/main.py`, Funktion `create_provider()`:

```python
from myprovider_provider import MyProviderProvider

def create_provider(config):
    pt = os.environ.get("PROVIDER_TYPE", "anthropic")
    ...
    if pt == "myprovider":
        return MyProviderProvider(config)
```

---

## 3. YAML-Config anlegen

```bash
cp provider_template/provider.yaml.example config/myprovider.yaml
# Anpassen: name, api_base, default_model, models
```

API-Key in `config/instance.yaml`:
```yaml
api_key: "sk-..."
```

---

## 4. Docker-Compose-Service hinzufügen

```bash
cp provider_template/Dockerfile.example docker/llm-myprovider/Dockerfile
```

In der passenden `compose.yml`:
```yaml
provider-myprovider:
  build:
    context: ../..
    dockerfile: docker/llm-myprovider/Dockerfile
  ports:
    - "12XXX:8000"
  environment:
    PROVIDER_TYPE: myprovider
    CONFIG_PATH: /config/myprovider.yaml
  volumes:
    - ./config:/config:ro
    - ./data/myprovider:/data
```

Port-Schema: 12001-12100 Infrastructure, 12101-12200 Special Heinzels, usw.

---

## 5. Testen

```bash
# Unit-Tests (kein echter API-Key nötig)
cd services/llm_provider
pytest tests/ -q

# Smoke-Test gegen laufenden Container
curl http://localhost:12XXX/health
curl http://localhost:12XXX/models
curl -X POST http://localhost:12XXX/chat \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Hallo"}]}'
```

---

## Ollama vorbereiten (lokale LLMs)

Für Ollama reicht ein minimaler Provider:

```python
class OllamaProvider(BaseProvider):
    def get_models(self): return ["llama3.2", "mistral", "gemma2"]
    def get_default_model(self): return "llama3.2"
    def _get_endpoint(self): return f"{self.config['api_base']}/api/chat"
    def _get_headers(self): return {"Content-Type": "application/json"}
    def _transform_request(self, req):
        return {"model": req.model or self.get_default_model(),
                "messages": [{"role": m.role, "content": m.content} for m in req.messages],
                "stream": False}
    def _transform_response(self, resp):
        return ChatResponse(
            content=resp["message"]["content"],
            model=resp.get("model", self.get_default_model()),
            usage={"input_tokens": resp.get("prompt_eval_count", 0),
                   "output_tokens": resp.get("eval_count", 0)},
            provider=self.provider_name,
        )
```

YAML: `api_base: "http://ollama:11434"` — kein API-Key nötig.
