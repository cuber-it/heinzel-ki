# Changelog

## [mvp-003] — 2026-03-05 — HNZ-003-0005 PromptBuilderAddOn (Commit e915cc6)

- src/addons/prompt_builder/addon.py: PromptBuilderAddOn — on_context_build, render(), set_template(), _get_working_prompt()
- src/addons/prompt_builder/addon.py: _format_now() Deutsch, _compress_blank_lines()
- src/addons/prompt_builder/templates/default.j2: Default-Template mit identity, Zeitkontext, Facts, Skills, Tools
- test/addons/test_prompt_builder_addon.py: 21 Tests

## [mvp-003] — 2026-03-05 — HNZ-003-0015 PromptAddOn (Commit 97e3fe7)

- src/addons/prompt/repository.py: PromptRepository (ABC), YamlPromptRepository — load_all/load_one/save/exists/list_names
- src/addons/prompt/addon.py: PromptAddOn — Registry, render(), hot_reload(), reload_one(), mutate(), Listener-Pattern
- src/addons/prompt/addon.py: PromptEventType (PROMPT_CHANGED/LOADED/REMOVED) — TODO EventBus
- src/addons/prompt/__init__.py: Exports
- test/addons/test_prompt_addon.py: 22 Tests — Repo, Lifecycle, Render, HotReload, Mutate, Events

## [mvp-003] — 2026-03-05 — HNZ-003-0009 AddOn Basis-Architektur (Commit b190d0c)

- requirements.txt: jinja2>=3.1.0 ergänzt
- src/core/addon_extension.py: BaseAddOnExtension (ABC) mit name/version/load()/unload()
- src/core/addon_extension.py: SkillBase — description, trigger_patterns, system_prompt_fragment, tools; load/unload als No-Op
- src/core/addon_extension.py: PromptBase — Jinja2-Template, variables, context (system/user/few-shot); StrictUndefined; render() mit Default-Merge
- src/core/__init__.py: BaseAddOnExtension, SkillBase, PromptBase exportiert
- test/core/test_addon_extension.py: 17 Tests — ABC-Enforcement, Defaults, Mutable-Isolation, Lifecycle, Rendering, StrictUndefined, Import

Alle nennenswerten Änderungen werden hier dokumentiert.
Format: `[MVP-XX] — Datum — Kurzbeschreibung`, Details darunter.

## [mvp-002] — 2026-03-04 — Core-Compaction-Monitor (Commit a00213a)

- base.py: _compaction_budget() — ResourceBudget aus Config
- base.py: _build_handover_summary() — echter LLM-Call fuer Rolling-Session-Handover
- base.py: _maybe_compact() — Monitor nach jedem Turn, Schwellen 80%/95% aus Config
- _pipeline.py: _maybe_compact() Aufruf nach add_turn(), ON_SESSION_ROLL bei Roll
- Config-Keys: memory.compact_threshold, memory.roll_threshold, memory.max_tokens
- 445 Tests gruen

## [mvp-002] — 2026-03-03 — HNZ-002-0018 Rolling Sessions Integration (Commit e497d2f)

- session.py: WorkingMemory.compaction_strategy ABC Property ergaenzt
- session.py: SessionManager.maybe_roll(budget) -> HandoverContext | None ergaenzt
- session_noop.py: NoopWorkingMemory.compact() nutzt CompactionRegistry statt FIFO
- session_noop.py: NoopSessionManager.maybe_roll() implementiert (Policy via Registry)
- compaction.py: Turn.raw_input/final_response statt user_message/assistant_message
- test_session.py: compact-Tests auf neues Verhalten aktualisiert (2 neue Tests)
- 412 Tests gruen

## [mvp-002] — 2026-03-03 — HNZ-002-0018 Compaction Interfaces (Commit f5fcafc)

**CompactionStrategy + Registry + SummarizingCompactionStrategy (Claude-Default)**

- `src/core/compaction.py` (neu, 425 Zeilen):
  - CompactionStrategy ABC: should_compact / compact / extract_critical / summarize / name
  - RollingSessionPolicy ABC: should_roll / create_handover / name
  - CompactionRegistry (Singleton): register/get/list_available/set_default/get_default
  - RollingSessionRegistry (Singleton): analog
  - SummarizingCompactionStrategy (DEFAULT, Claude-Stil): recency_window + kritische Turns, Rest -> Summary
  - TruncationCompactionStrategy (lossy, explizit dokumentiert): FIFO
  - NoopRollingSessionPolicy (DEFAULT): should_roll=False
  - Defaults beim Import registriert
- `src/core/models/types.py`: ON_SESSION_ROLL ergaenzt (24 HookPoints)
- `src/core/models/placeholders.py`: CompactionResult + HandoverContext (frozen Pydantic)
- `src/core/models/__init__.py`: neue Models exportiert
- `src/core/addon.py`: on_session_roll Hook ergaenzt
- Tests angepasst (24 HookPoints)
- 411 Tests gruen

**Noch offen (naechste Session):**
- session.py: WorkingMemory + SessionManager Integration
- test/test_compaction.py: volle Test-Suite
- __init__.py: Compaction-Exporte
- MEMORY_GUIDE.md

## [mvp-002] — 2026-03-03 — HNZ-002-0007: Config-System (Commit 2d2cb84)

**Config-System: YAML, ENV-Override, Singleton**

- `src/core/config.py`: HeinzelConfig (Pydantic), HeinzelIdentity, ProviderDefaults, ProviderEntry, DatabaseConfig, SessionConfig, LoggingConfig, SkillsConfig
- `get_config(path?)`: laedt YAML, ENV-Overrides (HEINZEL_*), Singleton-Cache
- `reset_config()`: Cache-Reset fuer Tests
- `find_config_file()`: sucht heinzel.yaml in CWD, config/, ~/.config/heinzel/, /etc/heinzel/
- ENV-Override: HEINZEL_SECTION_FIELD=value ueberschreibt YAML-Werte
- `heinzel.yaml.example`: vollstaendig kommentiert, im Repo-Root
- `test/test_config.py`: 12 Tests (Defaults, YAML, ENV-Override, Validation, Singleton, find_config_file)
- `src/core/__init__.py`: Config-Symbole exportiert
- 411 Tests gruen

## [mvp-002] — 2026-03-03 — Code-Review: unnoetige Konstrukte entfernt (Commit 4420b3b)

**Cleanup nach Refactoring — kein Verhaltensaenderung**

- `base.py`: Delegate-Methoden entfernt (chat/chat_stream rufen _pipeline/_provider_bridge direkt auf); _working_memory toter State entfernt; _run_pipeline bleibt als einziger Delegate (wird in Tests direkt aufgerufen)
- `_provider_bridge.py`: build_messages() toter Code entfernt; working_memory lazy — wird jetzt nur noch bei ContextLengthExceededError geholt statt bei jedem LLM-Call
- `_dialog_logger.py`: _enabled-Flag entfernt (redundant: _file=None genuegt als disabled-Marker)
- 399 Tests gruen, flake8 sauber

## [mvp-002] — 2026-03-03 — Tech-Debt: base.py aufgeteilt (Commit 1ebfae3)

**Refactoring: base.py in saubere Module aufgeteilt — keine Verhaltensaenderung**

- `provider.py`: LLMProvider ABC hierher verschoben (war Duplikat in base.py)
- `_dialog_logger.py` (neu): _DialogLogger Klasse ausgelagert, package-intern
- `_provider_bridge.py` (neu): build_messages(), build_messages_from_ctx(), call_provider() — reine Funktionen mit heinzel-Parameter (Option C, TYPE_CHECKING-Guard)
- `_pipeline.py` (neu): run_pre_phases(), run_post_phases(), run_pipeline(), phase(), dispatch_and_apply() — kein Code-Duplikat mehr zwischen chat() und chat_stream()
- `base.py`: 787 → 382 Zeilen (-51%); Methoden sind Delegate-One-Liner; chat_stream() nutzt run_pre_phases/run_post_phases
- PEP8: alle angefassten Dateien flake8-sauber (max 100 Zeichen)
- 399 Tests gruen

## [mvp-002] — 2026-03-03 — HNZ-002-0017 Abschluss (Commit 7684f9c)

**BaseHeinzel Session-Integration + token-basiertes Working Memory**

- `exceptions.py`: ContextLengthExceededError (tokens_sent, limit_discovered)
- `session.py`: WorkingMemory ABC erweitert (max_tokens/max_turns statt capacity, estimated_tokens(), compact()); SessionManager.create_session() mit optionaler session_id; MemoryGateInterface vollstaendig
- `session_noop.py`: NoopWorkingMemory token-basiert (Default 128k Token, 10k Turns); NoopSessionManager persistiert WorkingMemory-Instanz pro Session
- `provider.py`: context_window Property + Setter (lazy-discovery via 400er)
- `base.py`: _ensure_session() lazy, set_session_manager(), Working Memory in ON_MEMORY_QUERY prepended, Turn nach ON_STORED gespeichert, ContextLengthExceededError gefangen -> compact -> retry in _call_provider(); chat_stream() vollstaendig gleichgezogen; _build_messages_from_ctx() fixed (aktueller Input immer als letzte Message)
- `__init__.py`: Session-Exports ergaenzt
- `docs/MEMORY_GUIDE.md`: Schichtenmodell, Flow, Roadmap
- `src/frontend/heinzel_cli.py`: !memory, !session, Kontext-Fuellstand nach jeder Antwort
- `test/core/test_session.py`: 29 Tests (Session, Turn, Gate, WorkingMemory, SessionManager, BaseHeinzel-Integration)
- Tech-Debt auf Story: base.py aufteilen vor HNZ-003

## [mvp-002] — 2026-03-03 — HNZ-002-0017 Teilimplementierung (Commit 31c0ca0)

### Was gemacht wurde

**src/core/session.py** (neu)
- `SessionStatus` Enum: active / paused / ended
- `Session` Model (frozen Pydantic): id, heinzel_id, user_id, status, started_at, last_active_at, turn_count, metadata
- `Turn` Model (frozen Pydantic): id, session_id, timestamp, raw_input, final_response, strategy_used, complexity_level, history_depth, snapshot_ids, duration_ms, tokens_used
- `WorkingMemory` ABC: capacity, get_recent_turns, add_turn, get_context_messages, clear
- `SessionManager` ABC: active_session, create/get/resume/end_session, add_turn, get_turns, list_sessions, get_working_memory
- `MemoryGateInterface` ABC (Platzhalter HNZ-00x): forget, store, retrieve (LSTM-Gate-Konzept)

**src/core/session_noop.py** (neu)
- `NoopMemoryGate`: forget=passthrough, store=True, retrieve=leer
- `NoopWorkingMemory`: capacity=10, in-memory list[Turn], get_context_messages mit Token-Budget
- `NoopSessionManager`: dict-basiert, kein Persist, active_session-Tracking, turn_count + last_active_at Update

**src/core/exceptions.py**
- `SessionNotFoundError(SessionError)` ergaenzt, in __all__ eingetragen

**src/core/models/context.py**
- `working_memory_turns: int = 0` in PipelineContext (Meta-Sektion)
- `memory_tokens_used: int = 0` in PipelineContext (Meta-Sektion)

**docs/MEMORY_GUIDE.md** (neu, Platzhalter — wird in dieser Story befuellt)

**test/core/test_session.py** (neu, Skelett — Tests folgen in naechster Session)

### Noch offen (naechste Session)

- Block 5: BaseHeinzel Integration (base.py, 654 Zeilen!)
  - Session lazy init beim ersten Turn (nicht in __init__, da sync)
  - ON_MEMORY_QUERY Hook: working_memory.get_context_messages() -> ctx.messages prepend
  - ON_STORED Hook: Turn aus finalem ctx bauen -> session_manager.add_turn()
  - ctx.session_id beim ersten evolve auf active_session.id setzen
- Block 6: Tests (test_session.py befuellen, ~20 Tests)
- Block 7: Exports (__init__.py), MEMORY_GUIDE.md ausschreiben
- Block 8: Abschluss, Commit, Uebergabe

### Achtung / Hinweise fuer naechste Session

- base.py ist 654 Zeilen — nur relevante Abschnitte lesen (grep nach ON_MEMORY_QUERY, ON_STORED, __init__, _run_pipeline o.ae.)
- Session-Init MUSS lazy sein: BaseHeinzel.__init__ ist synchron, create_session() ist async
- get_context_messages() gibt (user, assistant, user, assistant, ...) zurueck — beim prepend in ctx.messages die Reihenfolge pruefen (Working Memory zuerst, dann aktueller Input)
- Token-Schaetzung in NoopWorkingMemory ist grob (len/4) — reicht fuer Noop, nicht fuer Produktion
- TECH-DEBT-Kommentar auf Story HNZ-002-0017: base.py refactoren vor HNZ-003
- Wekan-Bug: `wekan card <id> <board>` wirft JSON-Fehler — stattdessen `wekan --format json cards <board> <list>` + Python-Filter


---


## [MVP-002] — 2026-03-03 — HNZ-002-0008: Provider-Transport + Runtime-Switch

Commit: 977c49a

### src/core/provider.py (neu)
- HttpLLMProvider implementiert LLMProvider-ABC (chat, stream)
- Management-API: health(), list_models(), set_model()
- Properties: name, base_url, current_model
- ProviderError bei HTTP-Fehlern (status_code + detail)
- Kein LLM-spezifischer Code — spricht gegen unsere eigene Service-API

### src/core/provider_registry.py (neu)
- ProviderRegistry: laedt Provider-Liste aus providers.yaml
- Config-Pfad-Aufloesung: Konstruktor > HEINZEL_PROVIDERS_CONFIG > ./providers.yaml
- startup(): load_config + check_all + ersten healthy Provider aktivieren
- check_all(): pingt alle Provider per /health
- switch_to(name): health-Check + swap
- fallback(): naechsten healthy Provider aktivieren
- reload_config(): hot-reload mit Beibehaltung des aktiven Providers wenn moeglich
- ConfigError bei fehlender/leerer Config, ProviderError wenn kein Provider healthy

### src/core/base.py
- set_provider(provider): health-Check + sofortiger oder turn-safer Swap
- _pending_provider + _in_turn: Provider-Wechsel wird nach laufendem LLM-Turn aktiviert

### src/core/__init__.py
- HttpLLMProvider + ProviderRegistry exportiert

### src/frontend/heinzel_cli.py
- Temporaere Inline-Implementierung entfernt
- Import aus core: HttpLLMProvider
- Tote Imports entfernt (json, httpx, AsyncGenerator)
- Default-Port 12002 -> 12101 korrigiert

### test/core/test_provider.py (neu)
- 35 Tests: HttpLLMProvider (properties, chat, stream, health, list_models, set_model)
- ProviderRegistry (load_config, check_all, switch_to, fallback, reload_config, Pfad-Aufloesung)
- BaseHeinzel.set_provider (health-Check, unhealthy-Ablehnung, turn-safe swap)
- Kein echter Server — unittest.mock fuer alle HTTP-Calls

### Gesamt: 369 Tests gruen (vorher 334)

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

## HNZ-002-0006 — BaseHeinzel (Commit: 5724301)

### Original-Titel
## HNZ-002-0006 — BaseHeinzel + Pipeline Engine (Commit: 5724301)

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

## HNZ-002-0006 Nachtrag — chat_stream + config_path

- chat_stream() vollständig durch Pipeline verdrahtet (Vor- + Nachphasen)
- BaseHeinzel.__init__: config_path=None Parameter + _load_config() Hilfsmethode
- 6 neue Tests (TestChatStream + config-Tests), gesamt 27 fuer 0006
- 329 Tests gruen

## HNZ-002-0006 Nachtrag 2 — natives DialogLogging

- _DialogLogger: natives Dialoglogging im Core (keine AddOn-Option)
- Immer: USER bei ON_INPUT, HEINZEL bei ON_OUTPUT_SENT
- Optional (Config): log_addons, log_mcp
- Jeder Heinzel bekommt eigene Logdatei: {log_dir}/{heinzel_id}.log
- Config-Schema: logging.log_dir / log_addons / log_mcp
- 5 neue Tests (TestDialogLogger), gesamt 334 gruen

## HNZ-002-0006 Nachtrag 3 — DialogLogger lfd. Nummer + heinzel_cli.py

- _DialogLogger: laufende Turn-Nummer (#0001, #0002, ...) fuer USER+HEINZEL
- _DialogLogger: log_path Property fuer CLI-Zugriff
- src/frontend/heinzel_cli.py: erster lebender Heinzel
  - HttpLLMProvider gegen Provider-Service (http://localhost:12002)
  - REPL-Loop: !quit / !history / freier Chat via chat_stream()
  - Config aus YAML (--config) oder Hardcode-Defaults
  - Startup-Info: Name, Provider-URL, Log-Pfad
- 334 Tests gruen

## 7d75c53 — HNZ-002-0009: ReasoningStrategy Interface

### Neue Dateien
- `src/core/reasoning.py` — ReasoningStrategy ABC, PassthroughStrategy, StrategyRegistry + Models (StrategyFeedback, StrategyMetrics, ToolResultAssessment)
- `src/core/REASONING_GUIDE.md` — Konzept, History-Features, Code-Beispiele, Compliance-Fixture-Doku
- `test/test_reasoning.py` — 29 Tests (Passthrough, Registry, BaseHeinzel.set_strategy, Compliance-Fixture)

### Geaenderte Dateien
- `src/core/models/placeholders.py` — StepPlan erweitert (next_action/tool_name/tool_args/prompt_addition/focus), Reflection erweitert (step_useful/insight/confidence/suggest_adaptation/compared_snapshot_ids), alle Felder backward-compatible
- `src/core/base.py` — reasoning_strategy Property + set_strategy() + _reasoning_strategy_name + Import
- `src/core/__init__.py` — reasoning-Exports ergaenzt

### Designentscheidungen
- TYPE_CHECKING fuer PipelineContext/ContextHistory (kein zirkulaerer Import)
- PassthroughStrategy = MVP-001-Verhalten: 1 Durchlauf, kein Loop
- StrategyRegistry.set_default() wirft KeyError fuer unbekannte Namen (fail-fast)
- set_strategy(obj) registriert + setzt in einem Schritt
- assert_strategy_compliance() als wiederverwendbares Fixture fuer HNZ-003+ Strategien
