"""reasoning_logger — Think-Logging als AddOn.

Nicht Teil von Core. Haengt sich via AddOn in die Pipeline ein
und schreibt pro Reasoning-Schritt eine JSONL-Zeile.

Jede Zeile enthaelt:
  session_id, turn, timestamp, iteration, phase,
  next_action, focus, prompt_addition (gekuerzt),
  response_len, response_preview, confidence,
  step_useful, insight, duration_ms

Dateipfad: <log_dir>/reasoning_<agent_id>_<date>.jsonl

Verwendung:
    from frontend.reasoning_logger import ReasoningLoggerAddOn
    logger_addon = ReasoningLoggerAddOn(log_dir="logs/reasoning")
    runner.register_addon(logger_addon, hooks={
        HookPoint.ON_LLM_REQUEST,
        HookPoint.ON_LLM_RESPONSE,
    })

Auswertung:
    jq . logs/reasoning/reasoning_*.jsonl
    python3 -m frontend.reasoning_logger --analyze logs/reasoning/
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.addon import AddOn, AddOnResult
from core.models import HookPoint, PipelineContext, ContextHistory

logger = logging.getLogger(__name__)

_PREVIEW_LEN = 300  # Zeichen fuer Response-Vorschau
_PROMPT_PREVIEW_LEN = 200  # Zeichen fuer Prompt-Addition-Vorschau


class ReasoningLoggerAddOn(AddOn):
    """Schreibt jeden Reasoning-Schritt als JSONL-Eintrag.

    Haengt sich in ON_LLM_REQUEST (Schritt-Start) und
    ON_LLM_RESPONSE (Schritt-Ende + Reflection-Daten) ein.

    Nur-Beobachter: modifiziert ctx nie.
    """

    name = "reasoning_logger"

    def __init__(self, log_dir: str | Path = "logs/reasoning") -> None:
        super().__init__()
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._pending: dict[str, dict[str, Any]] = {}  # snapshot_id → pending entry

    # ------------------------------------------------------------------
    # Interner Pfad
    # ------------------------------------------------------------------

    def _log_path(self, agent_id: str) -> Path:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        safe_id = agent_id[:8] if agent_id else "unknown"
        return self._log_dir / f"reasoning_{safe_id}_{date}.jsonl"

    def _write(self, agent_id: str, entry: dict[str, Any]) -> None:
        try:
            path = self._log_path(agent_id)
            with path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning("ReasoningLogger: Schreiben fehlgeschlagen: %s", exc)

    # ------------------------------------------------------------------
    # AddOn Hooks
    # ------------------------------------------------------------------

    async def on_llm_request(
        self, ctx: PipelineContext, history: ContextHistory | None = None
    ) -> AddOnResult:
        """Schritt-Start: Zeitstempel + Plan notieren."""
        plan = ctx.step_plan
        if plan is None:
            return AddOnResult()

        meta = ctx.metadata
        entry_start = {
            "event": "step_start",
            "ts": datetime.now(timezone.utc).isoformat(),
            "ts_ms": int(time.monotonic() * 1000),
            "session_id": ctx.session_id,
            "agent_id": ctx.agent_id,
            "iteration": ctx.loop_iteration,
            "phase": meta.get("hnz_rt_phase", "?"),
            "budget_used": meta.get("hnz_rt_budget_used", 0),
            "next_action": plan.next_action,
            "focus": plan.focus or "",
            "prompt_addition_preview": (plan.prompt_addition or "")[:_PROMPT_PREVIEW_LEN],
            "prompt_addition_len": len(plan.prompt_addition or ""),
        }
        # Pending speichern — wird bei ON_LLM_RESPONSE vervollstaendigt
        self._pending[ctx.snapshot_id] = entry_start
        return AddOnResult()

    async def on_llm_response(
        self, ctx: PipelineContext, history: ContextHistory | None = None
    ) -> AddOnResult:
        """Schritt-Ende: Response + Reflection ergaenzen, schreiben."""
        plan = ctx.step_plan
        if plan is None:
            return AddOnResult()

        # Passenden pending entry suchen (letzter bekannter)
        pending = self._pending.pop(ctx.snapshot_id, None)
        if pending is None and self._pending:
            # Fallback: letzten pending nehmen
            last_key = next(reversed(self._pending))
            pending = self._pending.pop(last_key)

        now_ms = int(time.monotonic() * 1000)
        start_ms = pending.get("ts_ms", now_ms) if pending else now_ms
        duration_ms = now_ms - start_ms

        meta = ctx.metadata
        reflection = ctx.reflection
        response = ctx.response or ctx.stream_buffer or ""

        entry: dict[str, Any] = {
            "event": "step_done",
            "ts": datetime.now(timezone.utc).isoformat(),
            "duration_ms": duration_ms,
            "session_id": ctx.session_id,
            "agent_id": ctx.agent_id,
            "iteration": ctx.loop_iteration,
            # Plan
            "next_action": plan.next_action,
            "focus": plan.focus or "",
            # Response
            "response_len": len(response),
            "response_preview": response[:_PREVIEW_LEN],
            # Reasoning-Metadaten (DeepReasoningStrategy)
            "phase": meta.get("hnz_rt_phase", "?"),
            "budget_used": meta.get("hnz_rt_budget_used", 0),
            "trace_len": len(meta.get("hnz_rt_trace", "")),
            # Confidence
            "confidence": meta.get("hnz_rt_confidence", None),
            # Reflection
            "step_useful": reflection.step_useful if reflection else None,
            "insight": reflection.insight if reflection else None,
            "reflection_confidence": reflection.confidence if reflection else None,
            "suggest_adaptation": reflection.suggest_adaptation if reflection else None,
        }
        self._write(ctx.agent_id, entry)
        return AddOnResult()


# ------------------------------------------------------------------
# CLI: einfache Auswertung
# ------------------------------------------------------------------

def _analyze(paths: list[Path]) -> None:
    """Gibt aggregierte Statistik aus allen JSONL-Dateien aus."""
    entries = []
    for p in paths:
        with p.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass

    done = [e for e in entries if e.get("event") == "step_done"]
    if not done:
        print("Keine step_done Eintraege gefunden.")
        return

    print(f"\n{'='*60}")
    print(f"Reasoning-Log Analyse — {len(done)} Schritte")
    print(f"{'='*60}")

    # Phasen-Verteilung
    from collections import Counter
    phases = Counter(e.get("phase", "?") for e in done)
    print("\nPhasen-Verteilung:")
    for phase, count in sorted(phases.items(), key=lambda x: -x[1]):
        print(f"  {phase:<20} {count:>4}x")

    # Konfidenz-Verlauf
    confidences = [e["confidence"] for e in done if e.get("confidence") is not None]
    if confidences:
        print(f"\nKonfidenz: min={min(confidences):.0%}  max={max(confidences):.0%}  "
              f"avg={sum(confidences)/len(confidences):.0%}")

    # Dauer
    durations = [e["duration_ms"] for e in done if e.get("duration_ms")]
    if durations:
        print(f"Dauer:     min={min(durations)}ms  max={max(durations)}ms  "
              f"avg={int(sum(durations)/len(durations))}ms")

    # Step useful
    useful = [e for e in done if e.get("step_useful") is True]
    print(f"Nuetzlich: {len(useful)}/{len(done)} Schritte")

    # Letzten Eintraege
    print(f"\nLetzte 5 Schritte:")
    for e in done[-5:]:
        conf = f"{e['confidence']:.0%}" if e.get("confidence") is not None else "   ?"
        print(f"  [{e.get('iteration', '?')}] {e.get('phase','?'):<15} "
              f"action={e.get('next_action','?'):<10} "
              f"conf={conf}  "
              f"{e.get('duration_ms','?')}ms  "
              f"response={e.get('response_len',0)}z")
    print()


if __name__ == "__main__":
    import sys
    import argparse

    parser = argparse.ArgumentParser(description="Reasoning-Log Analyse")
    parser.add_argument("--analyze", metavar="DIR", help="JSONL-Verzeichnis analysieren")
    parser.add_argument("files", nargs="*", help="Einzelne JSONL-Dateien")
    args = parser.parse_args()

    paths: list[Path] = []
    if args.analyze:
        paths = sorted(Path(args.analyze).glob("reasoning_*.jsonl"))
        if not paths:
            print(f"Keine Dateien in {args.analyze}")
            sys.exit(1)
    elif args.files:
        paths = [Path(f) for f in args.files]
    else:
        parser.print_help()
        sys.exit(1)

    _analyze(paths)
