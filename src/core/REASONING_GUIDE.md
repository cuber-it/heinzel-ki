# REASONING_GUIDE — Austauschbare Reasoning-Strategien

## 1. Konzept: Warum austauschbare Strategien?

Kommerzielle Systeme hartkodieren ihren Reasoning-Loop:
"Nimm User-Input, rufe LLM auf, gib Antwort zurück." Das reicht fur
einfache Chatbots — aber nicht fur einen Agenten der planen, werkzeuge
nutzen und seine eigenen Gedanken reflektieren soll.

Heinzel trennt diese Verantwortlichkeiten:

- **BaseHeinzel** stellt Pipeline + ContextHistory
- **ReasoningStrategy** entscheidet WIE auf eine Anfrage geantwortet wird

Der entscheidende Unterschied zu anderen Systemen: Die Strategie sieht
nicht nur den aktuellen Zustand — sie hat Zugriff auf die gesamte
`ContextHistory` und kann zurueckblicken, Snapshots vergleichen
(`history.diff()`) und den gesamten Denkpfad lesen
(`history.to_reasoning_trace()`).

### Default: PassthroughStrategy

Kein Loop, kein Overhead. Eingabe -> LLM -> Ausgabe.
Entspricht MVP-001-Verhalten. Jeder neue Heinzel startet damit.

### Wann eine eigene Strategie?

- Aufgaben die mehrere Schritte oder Tool-Calls benoetigen (ReAct)
- Iterative Verbesserung einer Antwort (SelfRefine)
- Explizites Denken vor der Antwort (ChainOfThought)
- Verschiedene Loesungspfade parallel erkunden (TreeOfThoughts)

---

## 2. Die History als Schaltzentrale

`ContextHistory` ist das Gedaechtnis eines einzigen Turns — eine Liste
von `PipelineContext`-Snapshots, einer pro Pipeline-Phase.

```python
# Aktueller Zustand
ctx = history.current

# Erster Snapshot (Beginn des Turns)
initial = history.initial

# Fortschritt messen: was hat der letzte Schritt geaendert?
if len(history._snapshots) >= 2:
    diff = history.diff(history._snapshots[-2], history.current)
    print(diff.added_tool_results)  # neue Tool-Ergebnisse
    print(diff.response_changed)    # hat sich die Antwort veraendert?

# Gesamten Denkpfad als lesbare Strings
trace = history.to_reasoning_trace()
for step in trace:
    print(step)

# Snapshot zu einem bestimmten HookPoint holen
from core.models import HookPoint
snap = history.at_phase(HookPoint.ON_AFTER_LLM)
```

### Was steckt in ContextDiff?

`history.diff(snap_a, snap_b)` gibt ein `ContextDiff` zurueck:

```python
diff.added_tool_results    # neue ToolResults seit snap_a
diff.response_changed      # bool: hat final_response sich veraendert?
diff.goals_delta           # neue/abgeschlossene Goals
diff.token_delta           # Token-Differenz
```

---

## 3. Schritt-fuer-Schritt: Eigene Strategie implementieren

### 3.1 Klasse anlegen

```python
from core.reasoning import ReasoningStrategy, StrategyFeedback, StrategyMetrics, ToolResultAssessment
from core.models.placeholders import StepPlan, Reflection

class MyStrategy(ReasoningStrategy):

    @property
    def name(self) -> str:
        return "my_strategy"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "Meine Strategie fuer Aufgaben mit mehreren Schritten."
```

### 3.2 Methoden implementieren

```python
    async def initialize(self, ctx, history):
        # Optional: ctx anreichern bevor der Loop startet
        # z.B. Ziele aus user_input extrahieren
        return ctx  # oder ctx.evolve(goals=(...))

    async def should_continue(self, ctx, history):
        # Wann aufhoeren?
        if len(history._snapshots) > 10:
            return False  # Maximal 10 Snapshots
        if not ctx.goals:
            return False  # Keine Ziele mehr offen
        return True

    async def plan_next_step(self, ctx, history):
        # Was als naechstes?
        trace = history.to_reasoning_trace()
        # ... Entscheidung basierend auf Trace
        return StepPlan(
            next_action="tool",
            tool_name="web_search",
            tool_args={"query": "..."},
            focus="Informationen suchen",
        )

    async def reflect(self, ctx, history):
        # War der letzte Schritt nuetzlich?
        if len(history._snapshots) >= 2:
            diff = history.diff(history._snapshots[-2], ctx)
            useful = len(diff.added_tool_results) > 0
        else:
            useful = True
        return Reflection(
            step_useful=useful,
            insight="Neue Tool-Ergebnisse verfuegbar" if useful else "Kein Fortschritt",
            confidence=0.8 if useful else 0.3,
            compared_snapshot_ids=(
                history._snapshots[-2].id if len(history._snapshots) >= 2 else "",
                ctx.id,
            ),
        )

    async def adapt(self, feedback):
        pass  # In HNZ-002: No-Op

    async def metrics(self, ctx, history):
        return StrategyMetrics(
            iterations=len(history._snapshots),
            history_depth=len(history._snapshots),
        )

    async def on_tool_result(self, ctx, result, history):
        # Reicht das Tool-Ergebnis?
        if result.error:
            return ToolResultAssessment(
                verdict="needs_retry",
                reason=f"Tool-Fehler: {result.error}",
            )
        return ToolResultAssessment(verdict="sufficient")
```

### 3.3 Registrieren

```python
from core.reasoning import StrategyRegistry
StrategyRegistry.register(MyStrategy())

# Als Default setzen
StrategyRegistry.set_default("my_strategy")

# Oder: per Heinzel-Instanz setzen
heinzel.set_strategy("my_strategy")
heinzel.set_strategy(MyStrategy())  # registriert + setzt in einem Schritt
```

---

## 4. Code-Beispiel: MinimalStrategy (2 Iterationen)

Eine Strategie die maximal 2 Durchlaeufe macht — beim ersten denkt sie nach,
beim zweiten antwortet sie:

```python
class TwoPassStrategy(ReasoningStrategy):
    """Erst denken, dann antworten."""

    @property
    def name(self) -> str: return "two_pass"
    @property
    def version(self) -> str: return "1.0.0"
    @property
    def description(self) -> str:
        return "Erst einen Denkschritt, dann direkte Antwort."

    async def initialize(self, ctx, history):
        return ctx

    async def should_continue(self, ctx, history):
        # Weitermachen wenn noch kein think-Schritt war
        snaps = len(history._snapshots)
        return snaps <= 1   # Stopp ab dem 2. Snapshot

    async def plan_next_step(self, ctx, history):
        if len(history._snapshots) == 1:
            # Erster Durchlauf: nachdenken
            return StepPlan(
                next_action="think",
                focus="Aufgabe analysieren",
                prompt_addition="Denke Schritt fuer Schritt nach bevor du antwortest.",
            )
        # Zweiter Durchlauf: antworten
        return StepPlan(next_action="respond")

    async def reflect(self, ctx, history):
        return Reflection(step_useful=True, confidence=1.0)

    async def adapt(self, feedback): pass

    async def metrics(self, ctx, history):
        return StrategyMetrics(
            iterations=len(history._snapshots),
            history_depth=len(history._snapshots),
        )

    async def on_tool_result(self, ctx, result, history):
        return ToolResultAssessment(verdict="sufficient")
```

---

## 5. Compliance-Test nutzen

Jede neue Strategie sollte `assert_strategy_compliance()` nutzen:

```python
# In test/test_my_strategy.py
import pytest
from test_reasoning import assert_strategy_compliance
from core.models import PipelineContext
from core.models.context import ContextHistory

@pytest.fixture
def ctx():
    return PipelineContext(user_input="test")

@pytest.fixture
def history(ctx):
    h = ContextHistory()
    h.push(ctx)
    return h

@pytest.mark.asyncio
async def test_my_strategy_compliance(ctx, history):
    await assert_strategy_compliance(TwoPassStrategy(), ctx, history)
```

Der Compliance-Test prueft:
- name/version/description sind nicht-leere Strings
- initialize gibt PipelineContext zurueck
- should_continue gibt bool zurueck
- plan_next_step gibt StepPlan mit gueltiger next_action zurueck
- reflect gibt Reflection mit confidence 0.0-1.0 zurueck
- metrics gibt StrategyMetrics zurueck
- on_tool_result gibt ToolResultAssessment mit gueltigem verdict zurueck

---

## 6. Strategie via AddOn registrieren

Das empfohlene Pattern fuer Plugin-Strategien:

```python
from core.addon import AddOn
from core.models import HookPoint, PipelineContext
from core.models.context import ContextHistory
from core.reasoning import StrategyRegistry

class MyStrategyAddOn(AddOn):
    """Registriert MyStrategy und setzt sie als Default."""

    @property
    def name(self) -> str:
        return "my_strategy_addon"

    async def on_attach(self) -> None:
        StrategyRegistry.register(MyStrategy())

    async def execute(
        self,
        hook: HookPoint,
        ctx: PipelineContext,
        history: ContextHistory,
    ) -> PipelineContext:
        return ctx   # keine Modifikation noetig


# Registrierung beim Heinzel
heinzel.register_addon(
    MyStrategyAddOn(),
    hooks={HookPoint.ON_ATTACH},
)
# Nach connect():
heinzel.set_strategy("my_strategy")
```

---

## 7. Geplante Strategien (HNZ-003+)

| Strategie | Kern-Idee | History-Feature |
|---|---|---|
| `ChainOfThoughtStrategy` | Expliziter Denkpfad vor Antwort | `to_reasoning_trace()` als Kontext |
| `ReActStrategy` | Thought/Action/Observation-Zyklen | History als Zyklus-Log |
| `TreeOfThoughtsStrategy` | Verzweigung an unklaren Punkten | `at_phase()` fuer Branching |
| `SelfRefineStrategy` | Iterative Verbesserung | `diff()` ob Revision besser wurde |
| `MetaReasoningStrategy` | Waehlt Sub-Strategie automatisch | History-Pattern-Analyse |
| `EvolvingStrategy` | Lernt aus Feedback | `StrategyFeedback.reasoning_trace` |

Alle kuenftigen Strategien muessen `assert_strategy_compliance()` bestehen.
