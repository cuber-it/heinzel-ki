# MEMORY_GUIDE — Compaction und Rolling Sessions

Dieser Guide erklaert das Compaction-System in heinzel-core:
wie es funktioniert, wie man eigene Strategien schreibt,
und was Rolling Sessions sind.

---

## Das Problem: Context-Vergesslichkeit

Jedes LLM hat ein endliches Token-Budget. Wenn der Context voll wird,
gibt es zwei naive Loesungen:

1. **Abschneiden (Truncation)** — Die aeltesten Turns fallen einfach weg.
   Ergebnis: Der Heinzel "vergisst" Fakten, Entscheidungen, Ziele.
   Gefaehrlich in Produktivsystemen.

2. **Nichts tun** — Der Context waechst bis der Provider einen Fehler wirft.

heinzel-core waehlt einen dritten Weg: **strukturierte Verdichtung**.

---

## Compaction — Verdichtung statt Vergessen

### Grundprinzip

Eine `CompactionStrategy` entscheidet:
- Welche Turns sind **kritisch** (niemals loeschen)?
- Welche Turns kommen in das **recency window** (verbatim behalten)?
- Was wird zu einem **Summary-Turn** verdichtet?

Das Ergebnis ist ein `CompactionResult`:

```python
@dataclass
class CompactionResult:
    kept_turns: tuple[Turn, ...]     # Vollstaendig erhalten
    dropped_turns: tuple[Turn, ...]  # Verdichtet (nicht verloren)
    summary: str | None              # Destillat der dropped_turns
    tokens_before: int
    tokens_after: int
    tokens_saved: int
    critical_preserved: bool         # Immer True bei korrekter Impl
```

### Kritische Turns

Ein Turn gilt als kritisch wenn er bestimmte Keywords enthaelt:

```
"merk dir", "merke dir", "nicht vergessen", "wichtig:",
"entscheidung:", "ziel:", "fakt:", "remember", "important",
"never forget", "decision:"
```

Kritische Turns kommen **immer** in `kept_turns` — egal wie voll der Context ist.

---

## Die Default-Strategie: SummarizingCompactionStrategy

Der Default entspricht dem Claude-Ansatz:

1. Die letzten `recency_window` Turns (default: 10) bleiben verbatim
2. Alle kritischen Turns bleiben verbatim
3. Alles andere wird zu einem kurzen Summary zusammengefasst

**Kein Datenverlust** — die Information ist im Summary erhalten,
nicht einfach geloescht.

```python
from core.compaction import CompactionRegistry

strategy = CompactionRegistry.get_default()  # SummarizingCompactionStrategy
print(strategy.name)  # "summarizing"
```

---

## TruncationCompactionStrategy

Die alternative, explizit verlustbehaftete Strategie:

```python
from core.compaction import CompactionRegistry

strategy = CompactionRegistry.get("truncation")
```

**WARNUNG:** Diese Strategie loescht Turns unwiederbringlich.
Kritische Turns werden zwar behalten, aber nicht-kritische Turns
der aelteren Geschichte sind weg. Nur fuer Szenarien geeignet
wo Speicher wichtiger ist als Kontinuitaet.

---

## Eigene Strategie schreiben

Eine eigene Strategie implementiert `CompactionStrategy` und wird
per Registry eingehaengt — kein Core-Code muss veraendert werden.

```python
from core.compaction import CompactionStrategy, CompactionRegistry
from core.models.placeholders import CompactionResult, ResourceBudget
from core.session import Turn

class ImportanceWeightedStrategy(CompactionStrategy):
    """Behaelt Turns nach gewichtetem Wichtigkeits-Score."""

    @property
    def name(self) -> str:
        return "importance_weighted"

    async def should_compact(self, history, budget) -> bool:
        ctx = history.current
        used = ctx.token_usage.total_tokens if ctx.token_usage else 0
        return used >= int(budget.max_tokens * 0.75)

    async def extract_critical(self, turns: list[Turn]) -> list[Turn]:
        # Eigene Kritikalitaets-Logik
        return [t for t in turns if len(t.raw_input) > 200]

    async def summarize(self, turns: list[Turn]) -> str:
        return f"{len(turns)} turns kompaktiert"

    async def compact(
        self, turns: list[Turn], budget: ResourceBudget
    ) -> CompactionResult:
        critical = await self.extract_critical(turns)
        recent = turns[-5:]
        kept_ids = {id(t) for t in critical + recent}
        kept = [t for t in turns if id(t) in kept_ids]
        dropped = [t for t in turns if id(t) not in kept_ids]
        summary = await self.summarize(dropped) if dropped else None
        return CompactionResult(
            kept_turns=tuple(kept),
            dropped_turns=tuple(dropped),
            summary=summary,
            tokens_before=sum(len(t.raw_input) for t in turns) // 4,
            tokens_after=sum(len(t.raw_input) for t in kept) // 4,
            tokens_saved=0,
            critical_preserved=True,
        )

# Einhaengen — z.B. im Addon oder beim Start
CompactionRegistry.register(ImportanceWeightedStrategy())
CompactionRegistry.set_default("importance_weighted")
```

Ab diesem Moment nutzt jeder `NoopWorkingMemory` automatisch
die neue Strategie — ohne Code-Aenderungen im Core.

---

## Rolling Sessions

Manchmal wird eine Session nicht nur zu gross fuer den Context,
sondern zu gross fuer sinnvolle Weiterarbeit. Rolling Sessions
loesen das durch einen **nahtlosen Session-Wechsel**.

### Ablauf

1. `SessionManager.maybe_roll(budget)` wird am Turn-Ende aufgerufen
2. Die aktive `RollingSessionPolicy` entscheidet via `should_roll()`
3. Bei Roll:
   - Aktuelle Turns werden kompaktiert
   - `HandoverContext` wird erstellt (Fakten, Ziele, kritische Turns)
   - Alte Session wird beendet
   - Neue Session startet mit `HandoverContext` in `metadata['handover']`
4. Der Aufrufer feuert `ON_SESSION_ROLL`

### HandoverContext

```python
@dataclass
class HandoverContext:
    from_session_id: str
    summary: str                      # Was war diese Session
    critical_turns: tuple[Turn, ...]  # Niemals verloren
    facts_extracted: tuple[str, ...]  # Destillierte Erkenntnisse
    goals_open: tuple[str, ...]       # Unerledigte Ziele
```

### Default: NoopRollingSessionPolicy

Der Default rollt niemals (`should_roll()` gibt immer `False` zurueck).
Fuer einfache Setups reicht das vollstaendig.

### Eigene Rolling Policy

```python
from core.compaction import RollingSessionPolicy, RollingSessionRegistry
from core.models.placeholders import HandoverContext, ResourceBudget

class TurnCountPolicy(RollingSessionPolicy):
    """Rollt wenn die Session mehr als N Turns hat."""

    def __init__(self, max_turns: int = 50) -> None:
        self._max_turns = max_turns

    @property
    def name(self) -> str:
        return "turn_count"

    def should_roll(self, session, budget: ResourceBudget) -> bool:
        return session.turn_count >= self._max_turns

    async def create_handover(self, session, compaction) -> HandoverContext:
        return HandoverContext(
            from_session_id=session.id,
            summary=f"Session mit {session.turn_count} Turns beendet.",
            critical_turns=compaction.kept_turns,
        )

# Registrieren
RollingSessionRegistry.register(TurnCountPolicy(max_turns=30))
RollingSessionRegistry.set_default("turn_count")
```

---

## Zusammenfassung

| Komponente | Zweck | Default |
|---|---|---|
| `CompactionStrategy` | Wie werden Turns verdichtet? | `SummarizingCompactionStrategy` |
| `RollingSessionPolicy` | Wann wird eine neue Session gestartet? | `NoopRollingSessionPolicy` |
| `CompactionRegistry` | Welche Strategien sind verfuegbar? | Singleton |
| `RollingSessionRegistry` | Welche Policies sind verfuegbar? | Singleton |
| `WorkingMemory.compaction_strategy` | Strategie pro Memory-Instanz | via Registry |
| `SessionManager.maybe_roll()` | Roll-Check am Turn-Ende | via Registry |

Alle Komponenten sind vollstaendig austauschbar ohne Core-Code anzufassen.
