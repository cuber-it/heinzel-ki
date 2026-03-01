# HNZ-002 Handover — Vollständiger Arbeitsplan

Erstellt: 2026-03-01  
Zweck: Kontextkompaktifizierung überleben — alle Infos für den nächsten Schritt

---

## 1. Ausgangslage

### Repos und Branches

| Maschine  | Pfad                          | Branch      | Commit   |
|-----------|-------------------------------|-------------|----------|
| Cirrus    | ~/Workspace/heinzel-ki        | development | b57948e  |
| theBrain  | ~/Workspace/heinzel-ki        | development | b57948e  |
| GitHub    | cuber-it/heinzel-ki           | main + development | b57948e |

- `mvp-002` Branch existiert NICHT mehr — muss neu angelegt werden von `development`
- Beide Maschinen synchron, sauber, kein uncommitted stuff

### Relevante Verzeichnisse

```
~/Workspace/heinzel-ki/          ← Ziel-Repo (HNZ-002 wird hier gebaut)
~/Workspace/theBrain/code/core/  ← Existierender heinzel-core (REFERENZ, nicht kopieren)
~/Workspace/ucuber/              ← MCP-Projekte (mcp_shell_tools etc.)
```

---

## 2. Was bereits existiert — der alte heinzel-core

Unter `~/Workspace/theBrain/code/core/` liegt ein vollständig funktionierender heinzel-core
(164 Tests grün, Python 3.12, pip install -e . funktioniert).

**Was dort fertig und brauchbar ist:**

| Modul | Inhalt | Nutzbarkeit für HNZ-002 |
|-------|--------|------------------------|
| `src/heinzel/models/` | Message, Session, Exchange, Fact, Errors (HeinzelError-Hierarchie) | Direkt übernehmen, anpassen |
| `src/heinzel/config.py` | Pydantic-Config, YAML + ENV-Override, Singleton | Direkt übernehmen |
| `src/heinzel/log.py` | Strukturiertes Logging, DB-Handler | Direkt übernehmen |
| `src/heinzel/prompts/` | Jinja2 PromptEngine + SectionRegistry | Direkt übernehmen |
| `src/heinzel/clients/provider.py` | HTTP-Client zum LLM-Provider (httpx) | Direkt übernehmen |
| `src/heinzel/clients/inmemory.py` | InMemorySessionClient + InMemoryFactsClient für Tests | Direkt übernehmen |
| `src/heinzel/clients/session.py` | asyncpg SessionClient | Übernehmen |
| `src/heinzel/clients/facts.py` | asyncpg FactsClient | Übernehmen |
| `src/heinzel/commands/` | CommandRegistry, 15+ Commands inkl. /history, /alias, /facts, /skill | Als Referenz — wird später CommandAddOn |
| `src/heinzel/addon.py` | BaseAddon, AddonManager, AddonState, Lifecycle | Referenz — wird in HNZ-002 NEU designed |
| `src/heinzel/addons/mattermost.py` | MattermostAddon (WebSocket, History, Approval) | Später (HNZ-006) |
| `src/heinzel/core.py` | BaseHeinzel (1108 Zeilen) | REFERENZ — wird nicht kopiert, neu gebaut |
| `tests/` | 164 Tests, vollständig gemockt | Als Vorlage für Test-Struktur |

**Wichtig:** Der alte Core ist NICHT die Basis für HNZ-002 — er ist REFERENZ.
HNZ-002 baut eine neue Architektur (PipelineContext, ContextHistory, HookPoint).
Einzelne Module (models, config, log, prompts, clients) können direkt übernommen werden.

---

## 3. Die neue Architektur — HNZ-002

### Kernprinzip: Immutable Pipeline

```
User Input
    ↓
PipelineContext.initial(raw_input=...) → Snapshot #0
    ↓ ctx = ctx.evolve(phase=ON_INPUT_PARSED, ...)  → Snapshot #1
    ↓ ctx = ctx.evolve(phase=ON_MEMORY_QUERY, ...)  → Snapshot #2
    ↓ ctx = ctx.evolve(phase=ON_CONTEXT_BUILD, ...) → Snapshot #3
    ↓ [Reasoning Loop]
    │   ctx = ctx.evolve(phase=ON_LLM_REQUEST, ...)  → Snapshot #N
    │   ctx = ctx.evolve(phase=ON_LLM_RESPONSE, ...) → Snapshot #N+1
    │   strategy.should_continue(ctx, history) ?
    └─→ ctx = ctx.evolve(phase=ON_OUTPUT, response=...) → letzter Snapshot
    ↓
ContextHistory.to_reasoning_trace() → menschenlesbare Denkgeschichte
```

Jeder `ctx.evolve()` erzeugt einen neuen Snapshot. `previous` zeigt auf den vorherigen.
Kein AddOn mutiert ctx direkt — immer nur via `evolve()`.

### Paketstruktur Ziel

```
heinzel-ki/
├── heinzel_core/              ← Das neue Paket
│   ├── __init__.py
│   ├── models.py              ← Story 0001: PipelineContext, ContextHistory, HookPoint, Basis-Models
│   ├── exceptions.py          ← Story 0001: HeinzelError-Hierarchie
│   ├── addon.py               ← Story 0002+0003: AddOn Interface + AddOnRouter
│   ├── base.py                ← Story 0006: BaseHeinzel + Pipeline Engine
│   ├── config.py              ← Story 0007: Config-System
│   ├── provider.py            ← Story 0008: Provider-Transport
│   ├── reasoning.py           ← Story 0009+0010: ReasoningStrategy + PassthroughStrategy
│   ├── goals.py               ← Story 0011: GoalTracker Interface
│   ├── resources.py           ← Story 0012: ResourceBudget Interface
│   ├── introspection.py       ← Story 0013: IntrospectionInterface
│   ├── evaluation.py          ← Story 0014: EvaluationInterface
│   ├── session.py             ← Story 0017: SessionManager Interface
│   ├── compaction.py          ← Story 0018: CompactionStrategy Interface
│   └── mcp.py                 ← Story 0004+0005: MCPToolsRouter Interface + Stub
├── tests/
│   └── heinzel_core/
│       ├── test_models.py
│       ├── test_base.py
│       └── ...
├── pyproject.toml
├── src/                       ← MVP-001 Provider-Code (bleibt, nicht anfassen)
│   ├── llm-provider/
│   └── frontend/
└── ...
```

---

## 4. Stories HNZ-002 — vollständige Liste

Alle 18 Stories stehen in Wekan (Board: H.E.I.N.Z.E.L., Liste: Stories).
Story 0001 ist **In Progress**.

| Story | Titel | Status | Abhängigkeit |
|-------|-------|--------|--------------|
| 0001 | Core Datenmodelle — PipelineContext, ContextHistory, HookPoint | In Progress | — |
| 0002 | AddOn Interface + Lifecycle + Dependency-System | Stories | 0001 |
| 0003 | AddOnRouter — Registrierung, Dispatch, Dependency-Resolver | Stories | 0002 |
| 0004 | MCPToolsRouter Interface | Stories | 0002 |
| 0005 | MCPAddOn Stub | Stories | 0004 |
| 0006 | BaseHeinzel + Pipeline Engine — Immutable Context Flow | Stories | 0001,0002,0003 |
| 0007 | Config-System — YAML, ENV-Override, Singleton | Stories | 0001 |
| 0008 | Provider-Transport — HTTP-Client, chat/stream, health | Stories | 0001 |
| 0009 | ReasoningStrategy Interface + PassthroughStrategy + StrategyRegistry | Stories | 0001 |
| 0010 | ComplexityEstimator + StrategySelector | Stories | 0009 |
| 0011 | GoalTracker Interface | Stories | 0001 |
| 0012 | ResourceBudget Interface | Stories | 0001 |
| 0013 | IntrospectionInterface | Stories | 0001,0006 |
| 0014 | EvaluationInterface | Stories | 0001 |
| 0015 | CLI-Testharness | Stories | 0006,0007,0008 |
| 0016 | Packaging — pip install heinzel-core, CI | Stories | alle |
| 0017 | SessionManager Interface | Stories | 0001 |
| 0018 | CompactionStrategy Interface | Stories | 0017 |

**Empfohlene Reihenfolge:**
0001 → 0007 → 0008 → 0009 → 0002 → 0003 → 0006 → 0017 → 0011 → 0012 → 0013 → 0014 → 0010 → 0004 → 0005 → 0015 → 0016 → 0018

---

## 5. Story 0001 im Detail — nächster Schritt

### Was zu bauen ist

**Datei: `heinzel_core/models.py`**

```python
# HookPoint Enum (23 Punkte)
ON_INPUT, ON_INPUT_PARSED, ON_MEMORY_QUERY, ON_MEMORY_HIT, ON_MEMORY_MISS,
ON_CONTEXT_BUILD, ON_CONTEXT_READY, ON_LLM_REQUEST, ON_STREAM_CHUNK,
ON_THINKING_STEP, ON_LLM_RESPONSE, ON_TOOL_REQUEST, ON_TOOL_RESULT,
ON_TOOL_ERROR, ON_LOOP_ITERATION, ON_LOOP_END, ON_OUTPUT, ON_OUTPUT_SENT,
ON_STORE, ON_STORED, ON_SESSION_START, ON_SESSION_END, ON_ERROR

# PipelineContext (immutabel, Pydantic frozen=True)
# + .evolve(**changes) → neuer Snapshot
# + .initial(raw_input, ...) → erster Snapshot

# ContextHistory
# + push(ctx), current, initial, at_phase(), diff(), to_reasoning_trace()

# Basis-Models (alle frozen): Message, MemoryResult, ThinkingStep,
#   ToolCall, ToolResult, AddOnResult, Fact, Skill, Goal

# ContextDiff: added_fields, changed_fields, phases_between
```

**Datei: `heinzel_core/exceptions.py`**

```python
HeinzelError → AddOnError → AddOnDependencyError, AddOnLoadError, CircuitOpenError
HeinzelError → ProviderError, DatabaseError, ConfigError, SessionError, StrategyError
```

### Referenz für models.py

Aus dem alten Core übernehmen/anpassen:
- `~/Workspace/theBrain/code/core/src/heinzel/models/chat.py` → Message-Struktur
- `~/Workspace/theBrain/code/core/src/heinzel/models/errors.py` → Error-Hierarchie als Basis
- `~/Workspace/theBrain/code/core/src/heinzel/models/facts.py` → Fact-Struktur
- `~/Workspace/theBrain/code/core/src/heinzel/models/session.py` → Session/Exchange

### Akzeptanzkriterien (aus Story)

- `evolve()` erzeugt neuen Snapshot, `previous` korrekt gesetzt
- `ContextHistory`: push, current, initial, at_phase
- `diff()` erkennt geänderte Felder korrekt
- `to_reasoning_trace()` gibt menschenlesbare Ausgabe
- `frozen=True`: direkte Mutation wirft TypeError
- Keine zirkulären Imports
- 100% Unit-Test-Coverage

---

## 6. Vorbereitungsschritte vor erstem Code

```bash
# 1. Branch anlegen
cd ~/Workspace/heinzel-ki
git checkout development
git checkout -b mvp-002

# 2. Paketstruktur anlegen
mkdir -p heinzel_core tests/heinzel_core

# 3. pyproject.toml anlegen (wie in theBrain/code/core — anpassen)
# Paketname: heinzel-core, src-layout: NEIN (heinzel_core/ im Root)

# 4. Erste leere __init__.py
touch heinzel_core/__init__.py tests/heinzel_core/__init__.py

# 5. Danach Story 0001 angehen
```

---

## 7. Wekan — Zugangsdaten und Tools

- URL: http://services:12050
- Login: ucuber / Frekki#171
- Board-ID: Hv3TAojctD87SjWvh
- CLI-Tool: `~/.local/bin/wekan`

```bash
wekan boards
wekan lists Hv3TAojctD87SjWvh
wekan cards Hv3TAojctD87SjWvh <LIST_ID>
wekan card <CARD_ID> Hv3TAojctD87SjWvh
wekan move <CARD_ID> <LIST_ID> Hv3TAojctD87SjWvh
```

Wichtige List-IDs:
- Stories: `qCLtRxksnmt4r2SAh`
- In Progress: `eirZs6afqu7NM33M9`
- Review: `oh9iQnrHoSaBMgGin`
- Done: `e3uh8365r8R8bwvku`
- Icebox: `EGmGBhHPa4Gec87WW`
- Epics: `DBDedvGFvtYgvk7pg`

Story 0001 Card-ID: `nkmu2zuZdHcnuRD8c` (aktuell In Progress)

---

## 8. Arbeitsregeln

- **Branch-Disziplin**: Neue Arbeit IMMER auf `mvp-002`. Nie direkt auf `main` oder `development`.
- **Git-Commits**: Single-line, Details in CHANGES.md
- **MVP-Branches**: Nie pushen (mvp-002 bleibt lokal bis merge)
- **Docker**: Bind mounts, keine named volumes
- **Pairing-Modus**: Navigator sagt was er vorhat → Driver sagt go → dann erst ausführen
- **shell-tools**: Immer für Dateioperationen auf Cirrus. Nie bash_tool/view/create_file.
- **Ports**: 12001-12100 Infra, 12101-12200 Services, 12201-12300 Tools, 12501+ Heinzels

---

## 9. Infra-Status auf theBrain

Stack unter `~/Workspace/heinzel-ki/docker/docker-compose.yml`:

| Service | Port | Status |
|---------|------|--------|
| postgres | 12001 | healthy |
| mattermost | 12002 | healthy |
| jupyterhub | 12003 | healthy |
| caddy | 12004 | healthy |
| portainer | 12005 | healthy |
| gitea | 12006 | healthy |

Starten: `cd ~/Workspace/heinzel-ki && docker compose -f docker/docker-compose.yml up -d`

OpenAI-Key liegt in `docker/llm-provider/.env` auf theBrain (nicht im Repo).

---

## 10. Was in der Icebox wartet (nicht für HNZ-002)

- CommandAddOn I+II (Commands sind noch direkt in BaseHeinzel, kommen als AddOn in HNZ-003)
- DialogLoggerAddOn (HNZ-003)
- HNZ-003: DatabaseAddOn, SessionMemoryAddOn, FactMemoryAddOn, PromptBuilderAddOn, SkillLoaderAddOn
- HNZ-004: Echter MCPClient
- HNZ-005: Memory System
- HNZ-006: Heinzel #1 — MasterBlaster
- HNZ-007: HeinzelFactory
- HNZ-008: Multi-Heinzel
- HNZ-009: Evolution/Mutation
