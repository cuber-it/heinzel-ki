# Changelog

Alle nennenswerten Änderungen werden hier dokumentiert.
Format: `[MVP-XX] — Datum — Kurzbeschreibung`, Details darunter.

---


## [MVP-002] — 2026-03-01 — HNZ-002-0003: AddOnRouter

Commit: be6dc46

### src/core/router.py (neu)
- AddOnRouter: selektive HookPoint-Registrierung (register/unregister)
- dispatch(HookPoint, ctx) -> list[AddOnResult]: Option A (Context-Kette + alle Results)
- Dependency-Resolver beim register(): AddOnDependencyError wenn nicht registriert
- Sortierung: priority aufsteigend, dann Registrierungsreihenfolge (bisect.insort)
- Fehler-Isolation: Exception -> AddOnError in results -> ON_ERROR dispatcht (nicht rekursiv)
- halt=True: bricht Chain für diesen Hook ab
- get(), list_registered(), is_registered()
- asyncio-safe

### test/core/test_router.py (neu)
- 25 Tests: register, unregister, dispatch-order, priority, halt, Fehler-Isolation, ON_ERROR, Concurrency, Performance

### src/core/__init__.py
- AddOnRouter exportiert

## [MVP-002 / HNZ-002-0002] — 2026-03-01 — AddOn Interface + Lifecycle + Dispatch

Branch: `mvp-002` | Commit: 1cf7bf8 | Tests: 75 passing (105 gesamt)

### Hinzugefügt

- `src/core/addon.py` — AddOn ABC, AddOnManager, AddOnState
  - `AddOn` abstrakte Basisklasse mit 23 Hook-Methoden als No-Op (deckt alle HookPoints ab)
  - Klassenattribute: `name`, `version`, `dependencies`
  - Lifecycle: `on_attach(heinzel)`, `on_detach(heinzel)`, `is_available()`
  - `AddOnManager` — Priority-Dispatch, Dependency-Check, halt-Flag, Fehler-Isolation
  - Exceptions aus `core.exceptions` importiert (kein Duplikat)
- `test/core/test_addon.py` — 75 Tests: State, Hooks, Compliance, Lifecycle, Dispatch
- `docs/ADDON_GUIDE.md` — vollständige Entwickler-Dokumentation
- `pytest.ini` — neu, asyncio_mode=auto

### Klärungen / Designentscheidungen

- `heinzel_core` war ein Artefakt — korrekter Importpfad ist `core.addon`
- `PipelineContext` ist frozen Pydantic — Mutation via `model_copy(update={})`, kein `with_update()`
- `AddOnManager` (diese Story) ≠ `AddOnRouter` (HNZ-002-0003) — Router hat selektive HookPoint-Registrierung

---

## [MVP-01] — 2026-02-28 — LLM Provider + Frontends

Branch: `main` | Tests: 110 passing

### Hinzugefügt

- `src/llm-provider/` — Provider Gateway (OpenAI, Anthropic, Google)
  - `base.py` — Abstrakte Basisklasse, unified interface (Tier 1/2/3)
  - `models.py` — Pydantic-Modelle (ChatRequest, ChatResponse, ContentBlocks)
  - `openai_provider.py` / `anthropic_provider.py` / `google_provider.py`
  - `file_processor.py` — Datei-Konverter (Bilder, PDF, Office, Text)
  - `config.py` — InstanceConfig: ENV > instance.yaml > Default
  - `database.py` — SQLite Metriken-Logging (costs.db)
  - `logger.py` — JSONL Session-Logging
  - `log_reader.py` — Log-Abfrage mit Filtern (session_id, heinzel_id, task_id)
  - `commands.py` — Provider-Kommandos: `!help`, `!status`, `!dlglog`
  - `retention.py` — Log-Rotation und Cleanup
  - `retry.py` — Exponential Backoff (max 3 Retries, 429/5xx)
  - `provider_template/` — Template + ONBOARDING.md für neue Provider
- `src/frontend/`
  - `cli.py` — Interaktives Terminal-Frontend mit Streaming
  - `chainlit_app.py` — Web-Chat-Interface
- `docker/docker-compose.yml` — Zentrales Compose mit Profiles
- `docker/llm-provider/` — Dockerfile + compose.{openai,anthropic,google}.yml
- `docker/frontend/` — Dockerfile + compose.yml
- `docs/mvp-01.md` — Setup-Doku: Provider/Frontend, API-Beispiele, Troubleshooting
- `test/` — 110 Tests: provider_base, commands, metrics, multimodal, file_processor, retention, retry, logging

### Ports

| Service | Port |
|---------|------|
| Provider OpenAI | 12101 |
| Provider Anthropic | 12102 |
| Provider Google | 12103 |
| Chainlit | 12201 |

### Designentscheidungen

- Provider ist **stateless** — kein Session-State, kein globales session_params
- Kommandos nur provider-level: `!help`, `!status`, `!dlglog` — Session-Kommandos gehören in HNZ-002 Core
- Dialog-Logging: Default ON, zur Laufzeit togglebar via `!dlglog` oder `POST /logging/enable|disable`
- Metriken-Logging: immer aktiv (SQLite), kein Toggle
- Multimodal: Bilder nativ bei allen Providern; PDF nativ bei Anthropic+Google, via pypdf bei OpenAI

---

## [MVP-00] — 2026-02-19/28 — Basis-Infrastruktur

Branch: `main` | Stack: theBrain

### Hinzugefügt

- `docker/docker-compose.yml` — Zentrales Compose mit Profiles
- Services: PostgreSQL (12001), Mattermost (12002), JupyterHub (12003), Caddy (12004), Portainer (12005), Gitea (12006)
- `scripts/setup.sh` — Idempotentes Setup: .env generieren, Secrets, Verzeichnisse, Mattermost-Permissions
- `.env.example` — Alle konfigurierbaren Variablen mit Platzhaltern
- `config/ports.yaml` — Zentrales Port-Schema (12xxx)
- `docs/mvp-00.md` — Vollständige Ersteinrichtung inkl. manueller Account-Schritte

### Healthchecks

Alle Container nutzen native Binaries statt curl/wget:
- PostgreSQL: `pg_isready`
- Mattermost: `/mattermost/bin/mattermost version`
- Caddy: `caddy version`
- Portainer: `/portainer --version`
- JupyterHub: `curl localhost:8000`
- Gitea: `gitea --version`

### Designentscheidungen

- Bind-Mounts statt Named Volumes
- Netzwerk `heinzel` extern, Ownership im Root-Compose
- Mattermost-Datenbank muss einmalig manuell angelegt werden (`CREATE DATABASE mattermost`)
- setup.sh ohne hängenden Gitea-Auto-Setup — manuelle Ersteinrichtung per Browser

---

## [Housekeeping] — 2026-02-28 — Git-History bereinigt

- Hostnamen und Benutzernamen aus kompletter Git-History entfernt (`git filter-repo`)
- Force-push auf alle Branches (main, development, mvp-00)
- Beide Maschinen neu geklont

---

## [MVP-02] — 2026-03-01 — HNZ-002-0001: Core Datenmodelle

Branch: `mvp-002` | Tests: 30 passing

### Hinzugefügt

- `src/core/models/types.py` — HookPoint Enum (23 Pipeline-Phasen)
- `src/core/models/base.py` — Basis-Models: Message, TokenUsage, ToolCall, ToolResult, MemoryResult, ThinkingStep, AddOnResult
- `src/core/models/placeholders.py` — Platzhalter-Models: Fact, Skill, Goal, ResourceBudget, StepPlan, Reflection, EvaluationResult
- `src/core/models/context.py` — PipelineContext (immutabel, frozen=True), evolve(), ContextDiff, ContextHistory
- `src/core/models/__init__.py` — Re-Export aller Models
- `src/core/exceptions.py` — Exception-Hierarchie: HeinzelError, ProviderError, DatabaseError, ConfigError, SessionError, StrategyError, AddOnError, AddOnDependencyError, AddOnLoadError, CircuitOpenError
- `test/core/test_models.py` — 30 Unit-Tests (HookPoint, PipelineContext, ContextHistory, ContextDiff, Exceptions)
- `docs/TODO-HNZ-002-0001.md` — Story-TODO-Liste

### Designentscheidungen

- PipelineContext frozen=True — Mutation über evolve() erzeugt neuen Snapshot
- previous-Zeiger ermöglicht vollständige Denkgeschichte eines Turns
- Facts/Rules/Prompt-Assembly bewusst ausgelassen — kommen als AddOns (HNZ-003+)

---

## [MVP-002] — 2026-03-01 — HNZ-002-0004+0005: MCPToolsRouter AddOn

Commit: 5dc0aaa

### Architektur-Entscheid
- MCPToolsRouter ist ein AddOn (erbt von AddOn) — kein separates MCPAddOn nötig
- 0004 und 0005 in einem gebaut: Router IS das AddOn
- Neue Ordnerstruktur: src/addons/ als eigener Zweig neben src/core/
- Core bleibt sauber: kein MCP-Wissen im Core

### src/addons/mcp_router/ (neu)
**models.py:**
- ToolAddress: parsed 'target:server:tool', immutable, __str__ Roundtrip
- KnownTool: Discovery-Eintrag (kenne ich dieses Tool?)
- ToolCall: Aufruf mit address + args + context (Chaining)
- ToolResult: Ergebnis mit unknown=True als Discovery-Signal
- ApprovalPolicy: ALWAYS_ALLOW / ALWAYS_DENY / ASK_ONCE / ASK_ALWAYS
- ServerEntry: Server-Eintrag mit approval-Dict (tool -> Policy, _default als Fallback)

**router.py:**
- MCPToolsRouter(AddOn): zwei Registries — _tools (Discovery) + _servers (Approval)
- _tools: register/unregister/list_tools/find_tool
- _servers: register_server/get_server_entry/list_servers/set_approval/get_approval
- Approval-Flow: ALWAYS_ALLOW->run, ALWAYS_DENY->reject, ASK_ONCE->cache, ASK_ALWAYS->pending
- ASK_ONCE Session-Cache: record_ask_once_answer(), clear_ask_once_cache()
- call(): unknown->ToolResult(unknown=True), pending->metadata['approval_pending']
- chain(): Output[n] als prev_result in Call[n+1]
- on_tool_request(): unknown->metadata['unknown_tool_requests'], pending->metadata['approval_pending']
- NoopMCPToolsRouter: _execute() nie erreicht (leere Registry), Austauschpunkt HNZ-004

### test/addons/test_mcp_router.py (neu)
- 62 Tests: ToolAddress, KnownTool, ToolCall, ToolResult, ApprovalPolicy, ServerEntry
- Approval-Flow alle 4 Policies inkl. ASK_ONCE Cache-Lifecycle
- on_tool_request Hook Integration
- ABC-Verifizierung

### Abweichungen von Story HNZ-002-0004
- MCPServer/Tool aus Story entfallen — Architektur-Entscheid: kein Server-Bau
- Adressierung via target:server:tool statt MCPServer-Objekt
- Export unter addons.mcp_router statt heinzel_core.mcp (Core bleibt clean)
- Approval-System über Story-Scope hinaus — kandidiert für eigenes Epic

## HNZ-002-0006 — BaseHeinzel + Pipeline Engine

### Änderungen
- `src/core/base.py`: BaseHeinzel + LLMProvider — Lifecycle, Pipeline-Loop, chat/chat_stream
- `src/core/addon.py`: Alle Hook-Signaturen um `history: ContextHistory | None = None` erweitert
- `src/core/router.py`: dispatch() + _dispatch_internal() um `history`-Parameter erweitert
- `src/addons/mcp_router/router.py`: on_tool_request Signatur angepasst
- `src/core/__init__.py`: BaseHeinzel + LLMProvider exportiert
- `test/core/test_base_heinzel.py`: 21 Tests (Lifecycle, Pipeline, Loop, Chat, Halt, AddOn-Integration)
- `test/core/test_addon.py`, `test/core/test_router.py`: Hook-Signaturen in Test-Fixtures gefixt

### Design-Entscheide
- Initialer Snapshot: phase=ON_SESSION_START (Session startet vor Input)
- loop_done=True als Fallback in _call_provider (kein LoopControl-AddOn nötig)
- history optional (None = kein History-Kontext — für Backward-Compat)
- AddOnManager.dispatch() ebenfalls um history erweitert (Konsistenz)

### Testergebnis
323 Tests grün (vorher 302, +21 neue)
