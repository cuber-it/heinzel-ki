# Heinzel Memory — Architektur & Guide

## Schichtenmodell

Das Heinzel-System verwendet ein vierschichtiges Memory-Modell, inspiriert von
kognitiven Gedächtnismodellen und LSTM-Netzwerken.

```
┌─────────────────────────────────────────────────────┐
│  Working Memory        (Core — diese Story)          │
│  Letzte N Turns der aktuellen Session               │
│  → direkt in ctx.messages eingespeist               │
│  → immer aktiv, immer angeheftet                    │
├─────────────────────────────────────────────────────┤
│  Episodic Memory       (AddOn — HNZ-003)            │
│  Alle vergangenen Sessions, querybar                │
│  → via ON_MEMORY_QUERY durch AddOn eingespeist      │
├─────────────────────────────────────────────────────┤
│  Semantic Memory       (AddOn — HNZ-003)            │
│  Destillierte Fakten über User/Welt                 │
│  → via ON_MEMORY_QUERY durch AddOn eingespeist      │
├─────────────────────────────────────────────────────┤
│  Procedural Memory     (AddOn — HNZ-00x)            │
│  Gelernte Strategien, Gate-System                   │
│  → MemoryGateInterface (Platzhalter in Core)        │
└─────────────────────────────────────────────────────┘
```

## Working Memory (Core)

### Was es ist

Das Working Memory hält alle Turns der aktuellen Session im RAM.
Es verhält sich wie das Kontextfenster von Claude oder ChatGPT:
der gesamte bisherige Gesprächsverlauf wird bei jedem Turn
vor der aktuellen Frage in den Kontext eingespielt.

### Wo es lebt

- ABC: `core.session.WorkingMemory`
- Default-Impl: `core.session_noop.NoopWorkingMemory` (in-memory, kein Persist)
- Verwaltet von: `SessionManager` (eine Instanz pro Session)

### Kontext-Füllstand

`PipelineContext` trägt immer mit:
- `working_memory_turns` — wie viele Turns gerade im Kontext sind
- `memory_tokens_used` — geschätzte Token-Anzahl (grob: Zeichen / 4)

Das ermöglicht dem CLI und zukünftigen UIs, den Füllstand anzuzeigen.

### Token-Budget & Kompaktifizierung

Wenn das Modell einen HTTP 400 zurückgibt (Kontext zu lang), greift
der automatische Recover-Mechanismus in `BaseHeinzel._call_provider()`:

```
1. ContextLengthExceededError fangen
2. Limit am Provider merken (context_window — lazy discovery)
3. working_memory.compact(keep_ratio=0.5) — älteste 50% entfernen
4. Request wiederholen
```

Die `NoopWorkingMemory.compact()` kürzt einfach. Eine echte Impl
(HNZ-003) würde die verworfenen Turns vorher via LLM zusammenfassen.

### Flow in der Pipeline

```
ON_MEMORY_QUERY
  └─ working_memory.get_context_messages()
  └─ ctx.messages = wm_messages + aktuelle messages
  └─ ctx.working_memory_turns, ctx.memory_tokens_used gesetzt

... LLM-Call ...

ON_STORED
  └─ Turn(raw_input, final_response) erstellt
  └─ working_memory.add_turn(turn)
  └─ session_manager.add_turn(session_id, turn)
```

## MemoryGateInterface (Platzhalter)

Inspiriert von LSTM-Gates — drei Tore kontrollieren den Informationsfluss:

| Gate | Funktion |
|---|---|
| **Forget Gate** | Welche Turns nicht ins Working Memory? |
| **Input Gate** | Ist dieser Turn es wert gespeichert zu werden? |
| **Output Gate** | Welche Turns sind jetzt relevant? |

Die `NoopMemoryGate`-Impl lässt alles durch.
Echte Implementierung: HNZ-00x (nach HNZ-003).

## SessionManager

Verwaltet Sessions und gibt Working Memory pro Session heraus.

```python
# Eigenen SessionManager injizieren (vor erstem chat()-Call)
heinzel.set_session_manager(MeinPersistenterSessionManager())

# Session explizit starten (optional — sonst lazy beim ersten Turn)
session = await heinzel.session_manager.create_session(heinzel.heinzel_id)

# Vorhandene Session fortsetzen
await heinzel.session_manager.resume_session(session_id)
```

## Roadmap

| Story | Was |
|---|---|
| HNZ-002-0017 | Working Memory Core (diese Story) ✅ |
| HNZ-003 | Episodic + Semantic Memory (persistente AddOns) |
| HNZ-00x | Procedural Memory, Gate-System, Heinzel-Genetik |
