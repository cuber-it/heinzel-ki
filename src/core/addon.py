"""AddOn-System für Heinzel — Interface, Lifecycle und Dispatch.

Alle konkreten AddOns erben von AddOn (ABC) und implementieren
nur die Hooks die sie brauchen. Alle anderen bleiben No-Op.

Importpfad:
    from core.addon import AddOn, AddOnManager, AddOnState
    from .exceptions import AddOnError, AddOnDependencyError
"""

from __future__ import annotations

import enum
from abc import ABC
from typing import TYPE_CHECKING

from .exceptions import AddOnDependencyError, AddOnError, AddOnLoadError
from .models import (
    AddOnResult,
    ContextHistory,
    PipelineContext,
)

if TYPE_CHECKING:
    pass  # Zukünftige Forward-Refs hier


# =============================================================================
# State
# =============================================================================


class AddOnState(enum.Enum):
    """Lifecycle-Status eines AddOn."""

    UNLOADED = "unloaded"       # Noch nicht eingehängt
    ATTACHED = "attached"       # on_attach() wurde aufgerufen
    DETACHED = "detached"       # on_detach() wurde aufgerufen
    ERROR = "error"             # Fehler im Lifecycle


# =============================================================================
# AddOn ABC — der Vertrag
# =============================================================================


class AddOn(ABC):
    """
    Abstrakte Basisklasse für alle Heinzel-AddOns.

    Konventionen:
      - name:         Eindeutiger Bezeichner (PEP 8, snake_case, z.B. 'web_search')
      - version:      Semantic Versioning, default '0.1.0'
      - dependencies: Namen anderer AddOns die VOR diesem geladen sein müssen

    Lifecycle:
      on_attach(heinzel) → [Hooks werden dispatched] → on_detach(heinzel)

    Hooks:
      Alle Hook-Methoden sind optional (No-Op Default). Überschreiben
      nur was wirklich gebraucht wird. Signatur immer:
          async def on_<hookpoint>(self, ctx: PipelineContext) -> AddOnResult

    Beispiel:
        class MyAddOn(AddOn):
            name = 'my_addon'
            version = '0.2.0'
            dependencies = ['web_search']

            async def on_input(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
                # ctx modifizieren, dann zurückgeben
                new_ctx = ctx.model_copy(update={"raw_input": ctx.raw_input.strip()})
                return AddOnResult(modified_ctx=new_ctx)
    """

    # Klassenattribute — MÜSSEN in konkreten AddOns gesetzt werden
    name: str = ""
    version: str = "0.1.0"
    dependencies: list[str] = []

    def __init__(self) -> None:
        if not self.name:
            raise AddOnLoadError(
                "AddOn muss ein 'name' Klassenattribut haben",
                addon_name=self.__class__.__name__,
            )
        self._state: AddOnState = AddOnState.UNLOADED
        self._heinzel: object | None = None  # Gesetzt durch AddOnManager.attach()

    # -------------------------------------------------------------------------
    # State
    # -------------------------------------------------------------------------

    @property
    def state(self) -> AddOnState:
        """Aktueller Lifecycle-Status."""
        return self._state

    def is_available(self) -> bool:
        """Gibt an ob dieses AddOn einsatzbereit ist.

        Kann überschrieben werden um externe Abhängigkeiten (z.B. API-Keys,
        Netzwerk) zur Laufzeit zu prüfen. Standard: True wenn ATTACHED.
        """
        return self._state == AddOnState.ATTACHED

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def on_attach(self, heinzel: object) -> None:
        """Wird aufgerufen wenn das AddOn eingehängt wird.

        Hier: Verbindungen aufbauen, Config lesen, interne State initialisieren.
        Heinzel-Referenz wird NACH diesem Aufruf durch den Manager gesetzt.

        Args:
            heinzel: Die Heinzel-Instanz (noch untypisiert — BaseHeinzel kommt in HNZ-003)
        """
        pass  # No-Op — überschreiben bei Bedarf

    async def on_detach(self, heinzel: object) -> None:
        """Wird aufgerufen wenn das AddOn ausgehängt wird.

        Hier: Verbindungen trennen, Ressourcen freigeben, aufräumen.

        Args:
            heinzel: Die Heinzel-Instanz
        """
        pass  # No-Op — überschreiben bei Bedarf

    # -------------------------------------------------------------------------
    # Hooks — alle No-Op, Rückgabe: unveränderter Context
    # Naming folgt HookPoint-Enum (snake_case ohne Prefix)
    # -------------------------------------------------------------------------

    async def on_input(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Hook: Roheingabe empfangen."""
        return AddOnResult(modified_ctx=ctx)

    async def on_input_parsed(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Hook: Eingabe wurde geparst."""
        return AddOnResult(modified_ctx=ctx)

    async def on_memory_query(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Hook: Vor Gedächtnisabfrage."""
        return AddOnResult(modified_ctx=ctx)

    async def on_memory_hit(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Hook: Gedächtnis-Treffer gefunden."""
        return AddOnResult(modified_ctx=ctx)

    async def on_memory_miss(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Hook: Kein Gedächtnis-Treffer."""
        return AddOnResult(modified_ctx=ctx)

    async def on_context_build(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Hook: Kontext wird aufgebaut."""
        return AddOnResult(modified_ctx=ctx)

    async def on_context_ready(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Hook: Kontext ist fertig — kurz vor LLM-Call."""
        return AddOnResult(modified_ctx=ctx)

    async def on_llm_request(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Hook: LLM-Request wird abgeschickt."""
        return AddOnResult(modified_ctx=ctx)

    async def on_stream_chunk(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Hook: Ein gestreamter Chunk ist angekommen."""
        return AddOnResult(modified_ctx=ctx)

    async def on_thinking_step(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Hook: Ein Reasoning-Step ist abgeschlossen."""
        return AddOnResult(modified_ctx=ctx)

    async def on_llm_response(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Hook: LLM-Antwort vollständig empfangen."""
        return AddOnResult(modified_ctx=ctx)

    async def on_tool_request(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Hook: LLM möchte ein Tool aufrufen."""
        return AddOnResult(modified_ctx=ctx)

    async def on_tool_result(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Hook: Tool-Ergebnis ist zurück."""
        return AddOnResult(modified_ctx=ctx)

    async def on_tool_error(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Hook: Tool-Aufruf fehlgeschlagen."""
        return AddOnResult(modified_ctx=ctx)

    async def on_loop_iteration(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Hook: Eine Reasoning-Loop-Iteration beginnt."""
        return AddOnResult(modified_ctx=ctx)

    async def on_loop_end(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Hook: Reasoning-Loop beendet."""
        return AddOnResult(modified_ctx=ctx)

    async def on_output(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Hook: Ausgabe wird vorbereitet."""
        return AddOnResult(modified_ctx=ctx)

    async def on_output_sent(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Hook: Ausgabe wurde gesendet."""
        return AddOnResult(modified_ctx=ctx)

    async def on_store(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Hook: Kontext wird persistiert."""
        return AddOnResult(modified_ctx=ctx)

    async def on_stored(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Hook: Kontext wurde persistiert."""
        return AddOnResult(modified_ctx=ctx)

    async def on_session_start(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Hook: Session beginnt."""
        return AddOnResult(modified_ctx=ctx)

    async def on_session_end(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Hook: Session endet."""
        return AddOnResult(modified_ctx=ctx)

    async def on_session_roll(
        self, ctx: PipelineContext, history: ContextHistory | None = None
    ) -> AddOnResult:
        """Hook: Session-Roll ausgeloest."""
        return AddOnResult(modified_ctx=ctx)

    async def on_error(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Hook: Fehler in der Pipeline."""
        return AddOnResult(modified_ctx=ctx)

    def __repr__(self) -> str:
        return f"<AddOn name={self.name!r} version={self.version!r} state={self._state.value}>"


# =============================================================================
# AddOnManager — Lifecycle + Dispatch
# =============================================================================


class AddOnManager:
    """Verwaltet AddOn-Lifecycle und Hook-Dispatch.

    AddOns werden nach priority sortiert (niedriger = früher).
    Bei gleicher Priorität gilt Registrierungsreihenfolge (stabil).

    Dependency-Check bei attach(): Abhängigkeiten müssen bereits
    attached sein, sonst AddOnDependencyError.

    Dispatch-Verhalten:
      - Alle attached + available AddOns werden der Reihe nach aufgerufen
      - Jedes AddOn empfängt den (ggf. modifizierten) Kontext des Vorgängers
      - Setzt ein AddOn halt=True, bricht die Chain ab
      - Fehler in einem AddOn werden geloggt — Chain läuft weiter
    """

    def __init__(self) -> None:
        self._addons: list[AddOn] = []

    # -------------------------------------------------------------------------
    # Abfragen
    # -------------------------------------------------------------------------

    @property
    def addons(self) -> list[AddOn]:
        """Alle registrierten AddOns (sortiert nach Priorität)."""
        return list(self._addons)

    def get(self, name: str) -> AddOn | None:
        """AddOn nach Name suchen."""
        for addon in self._addons:
            if addon.name == name:
                return addon
        return None

    def is_attached(self, name: str) -> bool:
        """Prüft ob ein AddOn attached und verfügbar ist."""
        addon = self.get(name)
        return addon is not None and addon.state == AddOnState.ATTACHED

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def attach(self, addon: AddOn, heinzel: object, priority: int = 100) -> None:
        """Hängt ein AddOn ein und ruft on_attach() auf.

        Args:
            addon:    Die AddOn-Instanz
            heinzel:  Die Heinzel-Instanz (wird im AddOn hinterlegt)
            priority: Dispatch-Reihenfolge (niedriger = früher, default 100)

        Raises:
            AddOnError:           AddOn bereits registriert
            AddOnDependencyError: Abhängigkeit nicht attached
            AddOnLoadError:       on_attach() fehlgeschlagen
        """
        # Doppelte Registrierung verhindern
        if self.get(addon.name):
            raise AddOnError(
                f"AddOn ist bereits registriert",
                addon_name=addon.name,
            )

        # Dependency-Check
        for dep in addon.dependencies:
            if not self.is_attached(dep):
                raise AddOnDependencyError(
                    f"Abhängigkeit '{dep}' ist nicht attached",
                    addon_name=addon.name,
                )

        # Lifecycle
        try:
            await addon.on_attach(heinzel)
        except Exception as exc:
            addon._state = AddOnState.ERROR
            raise AddOnLoadError(
                "on_attach() fehlgeschlagen",
                addon_name=addon.name,
                original_exception=exc,
            ) from exc

        addon._heinzel = heinzel
        addon._state = AddOnState.ATTACHED

        # Einsortieren nach Priorität (stabil)
        addon._priority = priority  # type: ignore[attr-defined]
        self._addons.append(addon)
        self._addons.sort(key=lambda a: getattr(a, "_priority", 100))

    async def detach(self, name: str) -> None:
        """Hängt ein AddOn aus und ruft on_detach() auf.

        Args:
            name: Name des AddOn

        Raises:
            AddOnError: AddOn nicht gefunden
        """
        addon = self.get(name)
        if addon is None:
            raise AddOnError(f"AddOn nicht registriert", addon_name=name)

        try:
            await addon.on_detach(addon._heinzel)
        except Exception as exc:
            # Fehler loggen aber trotzdem entfernen
            addon._state = AddOnState.ERROR
        else:
            addon._state = AddOnState.DETACHED

        self._addons.remove(addon)

    async def detach_all(self) -> None:
        """Hängt alle AddOns aus (umgekehrte Prioritätsreihenfolge)."""
        for addon in reversed(list(self._addons)):
            try:
                await self.detach(addon.name)
            except AddOnError:
                pass  # Bereits entfernt oder Fehler — weiter

    # -------------------------------------------------------------------------
    # Hook-Dispatch
    # -------------------------------------------------------------------------

    async def dispatch(
        self,
        hook_name: str,
        ctx: PipelineContext,
        history: ContextHistory | None = None,
    ) -> PipelineContext:
        """Dispatcht einen Hook an alle attached + available AddOns.

        Jedes AddOn empfängt den (ggf. modifizierten) Kontext des Vorgängers.
        Setzt ein AddOn halt=True, bricht die Chain ab.
        Fehler werden geloggt — Chain läuft weiter.

        Args:
            hook_name: Name der Hook-Methode (z.B. 'on_input')
            ctx:       Initialer PipelineContext

        Returns:
            Finaler PipelineContext nach allen AddOns
        """
        for addon in self._addons:
            if not addon.is_available():
                continue

            hook = getattr(addon, hook_name, None)
            if hook is None:
                continue

            try:
                result: AddOnResult = await hook(ctx, history)
            except Exception as exc:
                # Fehler isolieren — andere AddOns nicht beeinträchtigen
                # TODO: Logging ergänzen wenn Logger-Infrastruktur steht (HNZ-003+)
                continue

            if result.modified_ctx is not None:
                ctx = result.modified_ctx

            if result.halt:
                break

        return ctx

    def __repr__(self) -> str:
        names = [a.name for a in self._addons]
        return f"<AddOnManager addons={names}>"


# =============================================================================
# Public API
# =============================================================================

__all__ = [
    "AddOn",
    "AddOnManager",
    "AddOnState",
]
