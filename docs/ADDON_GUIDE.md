# ADDON_GUIDE — Wie schreibt man ein AddOn?

Dieses Dokument erklärt wie AddOns für das Heinzel-System entwickelt werden.
Ein AddOn ist ein Plugin das sich in die Pipeline einhängt und an definierten
Punkten (HookPoints) eingreifen kann — ohne den Kern zu verändern.

---

## Inhaltsverzeichnis

1. [Minimales AddOn](#1-minimales-addon)
2. [Klassenattribute](#2-klassenattribute)
3. [Lifecycle](#3-lifecycle)
4. [Hooks](#4-hooks)
5. [PipelineContext lesen und modifizieren](#5-pipelinecontext-lesen-und-modifizieren)
6. [AddOnResult](#6-addonresult)
7. [Abhängigkeiten zwischen AddOns](#7-abhängigkeiten-zwischen-addons)
8. [AddOnManager — Registrierung und Priorität](#8-addonmanager--registrierung-und-priorität)
9. [Fehlerbehandlung](#9-fehlerbehandlung)
10. [Checkliste vor dem ersten Commit](#10-checkliste-vor-dem-ersten-commit)

---

## 1. Minimales AddOn

```python
from core.addon import AddOn
from core.models import AddOnResult, PipelineContext


class MyAddOn(AddOn):
    name = "my_addon"          # Pflicht — eindeutiger snake_case Bezeichner
    version = "0.1.0"          # Optional — Semantic Versioning
    dependencies: list[str] = []  # Optional — Namen anderer AddOns

    async def on_input(self, ctx: PipelineContext) -> AddOnResult:
        # Hier passiert etwas Sinnvolles
        return AddOnResult(modified_ctx=ctx)
```

Mehr braucht es nicht. Alle anderen Hooks sind als No-Op vorgegeben und
müssen nur überschrieben werden wenn sie gebraucht werden.

---

## 2. Klassenattribute

| Attribut       | Typ          | Pflicht | Beschreibung                                      |
|---------------|--------------|---------|---------------------------------------------------|
| `name`        | `str`        | ✅      | Eindeutiger Bezeichner, snake_case, nie leer      |
| `version`     | `str`        | —       | Semantic Versioning, Default `"0.1.0"`            |
| `dependencies`| `list[str]`  | —       | Namen von AddOns die vorher attached sein müssen  |

**Regel:** `name` muss als Klassenattribut gesetzt sein — nicht im `__init__`.
Ein leerer Name wirft beim Instanziieren sofort `AddOnLoadError`.

---

## 3. Lifecycle

```
AddOnManager.attach(addon, heinzel)
    │
    ├─ Dependency-Check: alle dependencies attached?
    ├─ addon.on_attach(heinzel)   ← hier initialisieren
    └─ addon._state = ATTACHED

[Dispatch läuft ...]

AddOnManager.detach("my_addon")
    │
    ├─ addon.on_detach(heinzel)   ← hier aufräumen
    └─ addon._state = DETACHED
```

### on_attach

Wird aufgerufen wenn das AddOn eingehängt wird. Hier:
- Verbindungen aufbauen (DB, API, ...)
- Config lesen
- Interne State initialisieren

```python
async def on_attach(self, heinzel: object) -> None:
    self._client = await MyApiClient.connect(url=self._config["url"])
```

### on_detach

Wird aufgerufen beim Aushängen. Hier:
- Verbindungen schließen
- Ressourcen freigeben

```python
async def on_detach(self, heinzel: object) -> None:
    if self._client:
        await self._client.close()
```

### is_available

Optionale Laufzeit-Prüfung. Gibt `False` zurück wenn das AddOn temporär
nicht einsatzbereit ist (z.B. API nicht erreichbar). Der Dispatcher
überspringt dann alle Hooks dieses AddOns.

```python
def is_available(self) -> bool:
    return self._client is not None and self._client.is_connected()
```

Standard-Implementierung: `True` wenn `state == ATTACHED`.

---

## 4. Hooks

Jeder Hook entspricht einem `HookPoint` aus `core.models.HookPoint`.
Die Methode heißt immer gleich wie der HookPoint-Value (lowercase).

| HookPoint              | Methode                  | Wann                                 |
|-----------------------|--------------------------|--------------------------------------|
| `ON_INPUT`            | `on_input`               | Roheingabe empfangen                 |
| `ON_INPUT_PARSED`     | `on_input_parsed`        | Eingabe geparst                      |
| `ON_MEMORY_QUERY`     | `on_memory_query`        | Vor Gedächtnisabfrage                |
| `ON_MEMORY_HIT`       | `on_memory_hit`          | Gedächtnis-Treffer                   |
| `ON_MEMORY_MISS`      | `on_memory_miss`         | Kein Gedächtnis-Treffer              |
| `ON_CONTEXT_BUILD`    | `on_context_build`       | Kontext wird aufgebaut               |
| `ON_CONTEXT_READY`    | `on_context_ready`       | Kurz vor LLM-Call                    |
| `ON_LLM_REQUEST`      | `on_llm_request`         | LLM-Request abgeschickt              |
| `ON_STREAM_CHUNK`     | `on_stream_chunk`        | Gestreamter Chunk angekommen         |
| `ON_THINKING_STEP`    | `on_thinking_step`       | Reasoning-Step abgeschlossen         |
| `ON_LLM_RESPONSE`     | `on_llm_response`        | LLM-Antwort vollständig              |
| `ON_TOOL_REQUEST`     | `on_tool_request`        | LLM möchte Tool aufrufen             |
| `ON_TOOL_RESULT`      | `on_tool_result`         | Tool-Ergebnis zurück                 |
| `ON_TOOL_ERROR`       | `on_tool_error`          | Tool-Aufruf fehlgeschlagen           |
| `ON_LOOP_ITERATION`   | `on_loop_iteration`      | Reasoning-Loop-Iteration beginnt     |
| `ON_LOOP_END`         | `on_loop_end`            | Reasoning-Loop beendet               |
| `ON_OUTPUT`           | `on_output`              | Ausgabe wird vorbereitet             |
| `ON_OUTPUT_SENT`      | `on_output_sent`         | Ausgabe gesendet                     |
| `ON_STORE`            | `on_store`               | Kontext wird persistiert             |
| `ON_STORED`           | `on_stored`              | Kontext persistiert                  |
| `ON_SESSION_START`    | `on_session_start`       | Session beginnt                      |
| `ON_SESSION_END`      | `on_session_end`         | Session endet                        |
| `ON_ERROR`            | `on_error`               | Fehler in der Pipeline               |

**Wichtig:** Nur die Hooks überschreiben die wirklich gebraucht werden.
Die No-Op Defaults in `AddOn` sind absichtlich — kein Overhead für
uninvolvierte AddOns.

---

## 5. PipelineContext lesen und modifizieren

Der `PipelineContext` ist **immutabel** (Pydantic `frozen=True`).
Zum Modifizieren `model_copy(update={...})` verwenden:

```python
async def on_input(self, ctx: PipelineContext) -> AddOnResult:
    # Lesen
    eingabe = ctx.raw_input

    # Modifizieren — immer model_copy, nie direktes Setzen
    bereinigt = eingabe.strip().lower()
    new_ctx = ctx.model_copy(update={"raw_input": bereinigt})

    return AddOnResult(modified_ctx=new_ctx)
```

**Niemals** direkt auf `ctx`-Felder schreiben — Pydantic wirft eine Exception
da das Model frozen ist.

---

## 6. AddOnResult

Rückgabe aller Hook-Methoden:

```python
class AddOnResult(BaseModel, frozen=True):
    modified_ctx: PipelineContext  # Immer zurückgeben — auch unverändert
    halt: bool = False             # True = Chain abbrechen
    ack: bool = True               # Reserviert für zukünftige Nutzung
    error: str | None = None       # Fehlermeldung (nur informational)
```

### Normaler Fall — Context unverändert

```python
return AddOnResult(modified_ctx=ctx)
```

### Context modifiziert

```python
new_ctx = ctx.model_copy(update={"raw_input": "bereinigt"})
return AddOnResult(modified_ctx=new_ctx)
```

### Chain abbrechen

`halt=True` stoppt den Dispatch — nachfolgende AddOns werden nicht
mehr aufgerufen. Sinnvoll z.B. bei Spam-Erkennung oder Command-Handling.

```python
return AddOnResult(modified_ctx=ctx, halt=True)
```

---

## 7. Abhängigkeiten zwischen AddOns

Wenn ein AddOn ein anderes voraussetzt:

```python
class SearchAddOn(AddOn):
    name = "search"
    dependencies = ["cache"]   # 'cache' muss vorher attached sein
```

Der `AddOnManager` prüft beim `attach()` ob alle Abhängigkeiten bereits
attached und available sind. Fehlt eine, wird `AddOnDependencyError` geworfen.

```python
from core.exceptions import AddOnDependencyError

try:
    await manager.attach(search_addon, heinzel)
except AddOnDependencyError as e:
    print(f"Fehlende Abhängigkeit: {e}")
```

**Reihenfolge beim attach:** Abhängigkeiten zuerst einhängen.

---

## 8. AddOnManager — Registrierung und Priorität

```python
from core.addon import AddOnManager

manager = AddOnManager()

# Attach mit expliziter Priorität (niedriger = früher in der Dispatch-Chain)
await manager.attach(logging_addon, heinzel, priority=10)   # läuft zuerst
await manager.attach(search_addon, heinzel, priority=50)
await manager.attach(output_addon, heinzel, priority=90)    # läuft zuletzt

# Dispatch eines Hooks
result_ctx = await manager.dispatch("on_input", ctx)

# Einzelnes AddOn suchen
addon = manager.get("search")

# Alle aushängen (umgekehrte Reihenfolge)
await manager.detach_all()
```

**Priorität-Empfehlungen:**

| Bereich              | Priorität  |
|---------------------|------------|
| Logging / Tracing   | 10–20      |
| Eingabe-Filter      | 20–40      |
| Gedächtnis          | 40–60      |
| Anreicherung        | 60–80      |
| Ausgabe / Speichern | 80–100     |

---

## 9. Fehlerbehandlung

Exceptions aus einem Hook werden vom Dispatcher abgefangen und isoliert —
die Chain läuft weiter. Das eigene AddOn sollte trotzdem defensiv sein:

```python
async def on_llm_response(self, ctx: PipelineContext) -> AddOnResult:
    try:
        await self._log_to_db(ctx)
    except Exception as exc:
        # Eigenes Fehler-Handling — nicht nach oben propagieren
        # (würde vom Dispatcher sowieso gefangen, aber explizit ist besser)
        self._logger.error("DB-Log fehlgeschlagen", error=str(exc))

    return AddOnResult(modified_ctx=ctx)
```

Für Fehler die das AddOn dauerhaft deaktivieren sollen:

```python
async def on_attach(self, heinzel: object) -> None:
    try:
        self._client = await connect()
    except ConnectionError as exc:
        # on_attach Exception → AddOnManager setzt state=ERROR
        # AddOn wird nicht in die Dispatch-Chain aufgenommen
        raise
```

---

## 10. Checkliste vor dem ersten Commit

- [ ] `name` als Klassenattribut gesetzt (snake_case, eindeutig)
- [ ] Nur benötigte Hooks überschrieben (No-Ops nicht kopieren)
- [ ] Jeder Hook gibt `AddOnResult(modified_ctx=...)` zurück
- [ ] Context nur via `model_copy(update={...})` modifiziert
- [ ] `on_attach` / `on_detach` implementiert wenn externe Ressourcen genutzt werden
- [ ] `dependencies` gesetzt wenn andere AddOns vorausgesetzt werden
- [ ] `is_available()` überschrieben wenn Laufzeit-Check sinnvoll ist
- [ ] Test vorhanden der das AddOn gegen den Compliance-Test prüft
- [ ] Kein Hardcoding von Pfaden, Ports oder Credentials
