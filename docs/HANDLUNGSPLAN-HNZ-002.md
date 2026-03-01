# HANDLUNGSPLAN: HNZ-002 — Heinzel Core bauen
*Erstellt: 2026-03-01 | Überlebt Kontextkompaktifizierung*

---

## SITUATION — Was wir haben

### Repo: heinzel-projekt (unser Arbeitsrepo)
- **Pfad:** `~/Workspace/heinzel-ki/` (Cirrus und theBrain)
- **GitHub:** gepusht, beide Maschinen synchron
- **Branch:** `development` = `main` = Commit `b57948e`
- **Kein `mvp-002` Branch** — muss noch angelegt werden (lokal, NICHT pushen)

### Was im Repo liegt (MVP-001 Stand):
```
services/llm_provider/   ← vollständiger Provider (Anthropic/OpenAI/Google)
docker/                  ← zentrales docker-compose mit Profiles
src/llm-provider/        ← Paketstruktur (leer, Platzhalter)
src/frontend/            ← Paketstruktur (leer, Platzhalter)
docs/mvp-00.md, mvp-01.md
CHANGES.md
```

### Was NICHT im Repo liegt:
- `heinzel_core/` — das ist was wir jetzt bauen (HNZ-002)

---

## REFERENZ-IMPLEMENTIERUNG

### Pfad auf beiden Maschinen:
```
~/Workspace/theBrain/code/core/src/heinzel/
```

Dies ist ein **älterer Prototyp** mit vollständiger Implementierung. Dient als Referenz,
wird aber NICHT kopiert — wir bauen auf Basis der Wekan-Stories neu.

### Was dort existiert (als Referenz):
- `core.py` → BaseHeinzel (vollständig, ~500 Zeilen)
- `models/` → Message, Response, ToolCall, TokenUsage, Session, Exchange, Fact, Rule, ProviderIssue, IssueResolution, ErrorType
- `addon.py` → BaseAddon mit AddonState/AddonManager, Hooks, Lifecycle
- `commands/` → CommandRegistry, facts.py, rules.py, sessions.py, alias.py, skill.py, status.py, history.py, provider.py, tools.py
- `prompts/` → PromptEngine (Jinja2), PromptSectionRegistry
- `config.py` → HeinzelConfig (YAML-basiert)
- `log.py` → structlog + DB-Handler
- `cli/main.py` → CLI-Frontend

---

## WEKAN BOARD

- **URL:** services:12050 (Wekan)
- **Board-ID:** `Hv3TAojctD87SjWvh`
- **Tool:** `~/.local/bin/wekan` (curl-basiertes CLI)

### Relevante Listen:
| Liste | ID |
|-------|-----|
| Stories | `qCLtRxksnmt4r2SAh` |
| In Progress | `eirZs6afqu7NM33M9` |
| Icebox | `EGmGBhHPa4Gec87WW` |
| CLAUDE | `aL5Ngr7K3EC7D9YXE` |

### HNZ-002 Stories (18 Stück in "Stories"):
- **0001** → IN PROGRESS: `heinzel_core/models.py` — PipelineContext, ContextHistory, HookPoint-Enum, Basismodelle
- 0002 → ContextHistory API
- 0003 → HookPoint-Enum
- 0004 → AddOn-Interface + Lifecycle
- 0005 → AddOnRouter
- 0006 → MCPToolsRouter-Interface
- 0007 → ReasoningStrategy-Interface + PassthroughStrategy
- 0008 → BaseHeinzel-Klasse (Kern-Pipeline)
- 0009 → GoalTracker + ResourceBudget
- 0010 → Introspection/Evaluation-Interfaces
- 0011 → BaseHeinzel verbindet alles
- 0012 → SessionManager + CompactionStrategy
- 0013 → Config-System (YAML)
- 0014 → Provider-Transport-Layer
- 0015 → CLI-Testharness
- 0016 → ReasoningStrategy-Komplexitätsschätzung
- 0017 → Packaging + pyproject.toml
- 0018 → Integration-Tests

### Icebox (für später):
- CommandAddOn I + II → braucht HNZ-002 AddOn-Interface
- DialogLoggerAddOn → braucht HNZ-002
- HNZ-003 komplett: Database, SessionMemory, FactMemory, PromptBuilder, SkillLoader

---

## ARCHITEKTUR-ENTSCHEIDUNGEN (aus Chat, definitiv)

### Core ist eine Library:
```python
from heinzel_core import BaseHeinzel
class MeinHeinzel(BaseHeinzel):
    ...
```

### Command-Prefix: `!` (nicht `/`)
- `!fact set key wert`
- `!fact get key`
- `!fact list`
- `!fact delete key`
- Alias + Chaining geplant, aber in Icebox (CommandAddOn)

### PipelineContext: immutable mit evolve()
```python
ctx2 = ctx.evolve(last_response="...")
```

### ReasoningStrategy: pluggbar
- `PassthroughStrategy` = Default (kein Loop, direkt durch)
- Loop-Varianten kommen als AddOns (Icebox)

### Logging: NICHT in HNZ-002
- Dialog-Log (JSONL) und System-Log → DialogLoggerAddOn → Icebox/HNZ-003

### Skills: NICHT in HNZ-002
- Skills-System (YAML, Hot-Reload) → SkillLoaderAddOn → HNZ-003

---

## ERSTER SCHRITT (Story 0001)

### Was zu tun ist:
1. `mvp-002` Branch von `development` anlegen (lokal, nicht pushen)
2. Paketstruktur anlegen: `src/heinzel_core/__init__.py`
3. `src/heinzel_core/models.py` implementieren

### models.py Inhalt (laut Story + Referenz):
```python
# PipelineContext (immutable)
@dataclass(frozen=True)
class PipelineContext:
    session_id: str
    user_message: str
    system_prompt: str
    messages: tuple[Message, ...]
    last_response: str | None = None
    metadata: frozenset = frozenset()

    def evolve(self, **kwargs) -> "PipelineContext":
        from dataclasses import replace
        return replace(self, **kwargs)

# ContextHistory (Denkgeschichte der Pipeline)
@dataclass
class ContextHistory:
    entries: list[ContextEntry] = field(default_factory=list)

    def add(self, stage: str, context: PipelineContext) -> None: ...
    def latest(self) -> PipelineContext | None: ...

# HookPoint (Enum der Pipeline-Hooks)
class HookPoint(Enum):
    BEFORE_ROUTING = "before_routing"
    BEFORE_REASONING = "before_reasoning"
    AFTER_REASONING = "after_reasoning"
    BEFORE_RESPONSE = "before_response"
    AFTER_RESPONSE = "after_response"
    ON_ERROR = "on_error"

# Message (Provider-agnostisch)
@dataclass
class Message:
    role: str  # "user" | "assistant" | "system"
    content: str | list[dict]  # text oder content_blocks

# TokenUsage
@dataclass
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

# Fact (Platzhalter, Vollimplementierung in HNZ-003)
@dataclass
class Fact:
    key: str
    value: str
    category: str = "general"
```

### pyproject.toml für heinzel_core:
```toml
[project]
name = "heinzel-core"
version = "0.2.0"
requires-python = ">=3.12"
dependencies = []  # keine externen Deps im Core!

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

---

## PROJEKTSTRUKTUR (Ziel Ende HNZ-002)

```
src/
  heinzel_core/
    __init__.py
    models.py          ← Story 0001
    context_history.py ← Story 0002
    hooks.py           ← Story 0003 (HookPoint-Enum)
    addon.py           ← Story 0004 (BaseAddon + Lifecycle)
    addon_router.py    ← Story 0005
    mcp_router.py      ← Story 0006
    reasoning.py       ← Story 0007 (ReasoningStrategy + Passthrough)
    base_heinzel.py    ← Story 0008
    goals.py           ← Story 0009
    introspection.py   ← Story 0010
    session.py         ← Story 0012
    config.py          ← Story 0013
    provider.py        ← Story 0014
  heinzel_core/
    tests/
      test_models.py
      test_context.py
      test_reasoning.py
      ...
  heinzel_core/
    cli/
      main.py          ← Story 0015 (CLI-Testharness)
```

---

## WORKFLOW (Pairing-Modus)

1. **Ich (Navigator) sage:** Was ich vorhabe
2. **Du (Driver) sagst:** go oder korrigierst
3. **Ich führe aus:** einen klar definierten Schritt
4. **Wir reden über das Ergebnis**

### Git-Regeln:
- Commits: einzeilig, Details in CHANGES.md
- `mvp-002` Branch: lokal, NICHT pushen (MVP-Branches nie auf GitHub)
- `development` + `main`: beide auf `b57948e`, sauber

### Story-Workflow auf Wekan:
- Story von "Stories" → "In Progress" wenn wir anfangen
- Story → "Done" wenn Akzeptanzkriterien erfüllt und Tests grün

---

## OFFENE FRAGEN / ZU KLÄREN

- Soll `heinzel_core` unter `src/heinzel_core/` oder direkt als `heinzel_core/` im Root?
  → Im Chat: Core als Library → `src/heinzel_core/` macht mehr Sinn (pip-installierbar)
- Tests: pytest unter `src/heinzel_core/tests/` oder separates `tests/`-Verzeichnis?
  → Referenz-Impl hat separates `tests/` aber Story 0015 sagt CLI-Testharness → beides möglich
