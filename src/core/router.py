"""AddOnRouter — Zentraler Dispatcher für das Heinzel-Core.

Verwaltet selektive Hook-Registrierung und dispatcht PipelineContext
durch alle registrierten AddOns eines HookPoints.

Importpfad:
    from core.router import AddOnRouter
    from .exceptions import AddOnError, AddOnDependencyError

Design-Entscheide:
    - dispatch() kettet ctx durch alle AddOns (Option A):
      Jedes AddOn bekommt den Output des Vorgängers.
      Rückgabe: list[AddOnResult] — Caller holt finalen Context via results[-1].modified_ctx
    - Kein on_attach()/on_detach() im Router — Lifecycle ist Sache des Heinzel (HNZ-003)
    - Fehler-Isolation: Exception → AddOnError → ON_ERROR-Dispatch (nicht rekursiv)
    - Sortierung: priority aufsteigend, dann Registrierungsreihenfolge (stabil)
    - Hot-Reload via unregister() ohne Neustart
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from .exceptions import AddOnDependencyError, AddOnError
from .models import AddOnResult, HookPoint, PipelineContext

if TYPE_CHECKING:
    from .addon import AddOn


# =============================================================================
# Internes Datenmodell
# =============================================================================


@dataclass(order=True)
class _RouterEntry:
    """Interner Registry-Eintrag. Sortierbar nach (priority, reg_order)."""

    priority: int
    reg_order: int
    addon: AddOn = field(compare=False)
    hooks: frozenset[HookPoint] = field(compare=False)


# =============================================================================
# AddOnRouter
# =============================================================================


class AddOnRouter:
    """Zentraler Dispatcher: verwaltet AddOns und leitet Hook-Aufrufe weiter.

    AddOns registrieren sich mit einer expliziten Liste von HookPoints —
    der Router ruft ein AddOn nur an den Hooks auf für die es registriert ist.

    Dispatch-Reihenfolge: priority aufsteigend, bei Gleichstand Registrierungsreihenfolge.

    Fehler-Isolation:
        Exception in einem AddOn → wird als AddOnError in results aufgenommen
        → ON_ERROR wird an alle für ON_ERROR registrierten AddOns dispatcht
        → ON_ERROR-Dispatch selbst ist nicht rekursiv (keine zweite Fehlerebene)

    Beispiel:
        router = AddOnRouter()
        router.register(my_addon, hooks=[HookPoint.ON_INPUT, HookPoint.ON_OUTPUT])
        results = await router.dispatch(HookPoint.ON_INPUT, ctx)
        final_ctx = results[-1].modified_ctx
    """

    def __init__(self) -> None:
        # name -> _RouterEntry (für O(1)-Lookup)
        self._entries: dict[str, _RouterEntry] = {}
        # Sortierte Liste für Dispatch — bleibt via bisect.insort stabil sortiert
        self._sorted: list[_RouterEntry] = []
        # Monoton steigender Zähler für Registrierungsreihenfolge
        self._reg_counter: int = 0

    # -------------------------------------------------------------------------
    # Abfragen
    # -------------------------------------------------------------------------

    def get(self, addon_name: str) -> AddOn | None:
        """AddOn nach Name suchen. Gibt None zurück wenn nicht registriert."""
        entry = self._entries.get(addon_name)
        return entry.addon if entry else None

    def list_registered(self) -> dict[str, list[HookPoint]]:
        """Alle registrierten AddOns mit ihren HookPoints zurückgeben.

        Returns:
            dict addon_name -> list[HookPoint], in Dispatch-Reihenfolge
        """
        return {
            entry.addon.name: sorted(entry.hooks, key=lambda h: h.value)
            for entry in self._sorted
        }

    def is_registered(self, addon_name: str) -> bool:
        """Prüft ob ein AddOn registriert ist."""
        return addon_name in self._entries

    # -------------------------------------------------------------------------
    # Registrierung
    # -------------------------------------------------------------------------

    def register(
        self,
        addon: AddOn,
        hooks: list[HookPoint],
        priority: int = 0,
    ) -> None:
        """AddOn für bestimmte HookPoints registrieren.

        Args:
            addon:    Die AddOn-Instanz (muss einen eindeutigen name haben)
            hooks:    Liste der HookPoints an denen dieses AddOn aktiv sein soll
            priority: Dispatch-Reihenfolge (niedriger = früher, default 0)

        Raises:
            AddOnError:           AddOn bereits registriert
            AddOnDependencyError: Abhängigkeit noch nicht registriert
            ValueError:           Leere hooks-Liste
        """
        if not hooks:
            raise ValueError(f"hooks darf nicht leer sein (addon={addon.name!r})")

        if addon.name in self._entries:
            raise AddOnError(
                "AddOn ist bereits registriert",
                addon_name=addon.name,
            )

        # Dependency-Check: alle dependencies müssen bereits registriert sein
        for dep in addon.dependencies:
            if dep not in self._entries:
                raise AddOnDependencyError(
                    f"Abhängigkeit '{dep}' ist nicht registriert",
                    addon_name=addon.name,
                )

        entry = _RouterEntry(
            priority=priority,
            reg_order=self._reg_counter,
            addon=addon,
            hooks=frozenset(hooks),
        )
        self._reg_counter += 1

        self._entries[addon.name] = entry
        bisect.insort(self._sorted, entry)

    def unregister(self, addon_name: str) -> None:
        """AddOn aus dem Router entfernen (Hot-Reload).

        Raises:
            AddOnError: AddOn nicht registriert
        """
        entry = self._entries.pop(addon_name, None)
        if entry is None:
            raise AddOnError("AddOn nicht registriert", addon_name=addon_name)

        self._sorted.remove(entry)

    # -------------------------------------------------------------------------
    # Dispatch
    # -------------------------------------------------------------------------

    async def dispatch(
        self,
        hook_point: HookPoint,
        ctx: PipelineContext,
    ) -> list[AddOnResult]:
        """Dispatcht einen HookPoint an alle dafür registrierten AddOns.

        ctx wird durch die Chain gereicht — jedes AddOn bekommt den Output
        des Vorgängers. Finaler Context: results[-1].modified_ctx

        Args:
            hook_point: Der HookPoint der dispatcht werden soll
            ctx:        Initialer PipelineContext

        Returns:
            list[AddOnResult] — Ein Result pro aufgerufenem AddOn.
            Leere Liste wenn kein AddOn für diesen Hook registriert ist.
        """
        return await self._dispatch_internal(hook_point, ctx, is_error_dispatch=False)

    async def _dispatch_internal(
        self,
        hook_point: HookPoint,
        ctx: PipelineContext,
        is_error_dispatch: bool,
    ) -> list[AddOnResult]:
        """Interne Dispatch-Logik. is_error_dispatch verhindert rekursive ON_ERROR-Kette."""
        results: list[AddOnResult] = []

        hook_name = hook_point.value  # z.B. "on_input"

        for entry in self._sorted:
            if hook_point not in entry.hooks:
                continue

            addon = entry.addon
            hook = getattr(addon, hook_name, None)
            if hook is None:
                continue  # Sollte nicht vorkommen — AddOn hat immer No-Op Defaults

            try:
                result: AddOnResult = await hook(ctx)
            except Exception as exc:
                # Fehler isolieren: als AddOnError in results aufnehmen
                error_result = AddOnResult(
                    modified_ctx=ctx,
                    ack=False,
                    error=str(AddOnError(
                        "Hook fehlgeschlagen",
                        addon_name=addon.name,
                        hook_point=hook_point.value,
                        original_exception=exc,
                    )),
                )
                results.append(error_result)

                # ON_ERROR dispatchen — aber nicht rekursiv
                if not is_error_dispatch and hook_point != HookPoint.ON_ERROR:
                    await self._dispatch_internal(
                        HookPoint.ON_ERROR, ctx, is_error_dispatch=True
                    )
                continue

            # ctx für nächstes AddOn weiterreichen
            if result.modified_ctx is not None:
                ctx = result.modified_ctx

            results.append(result)

            if result.halt:
                break

        return results

    # -------------------------------------------------------------------------
    # Repr
    # -------------------------------------------------------------------------

    def __repr__(self) -> str:
        names = [e.addon.name for e in self._sorted]
        return f"<AddOnRouter addons={names}>"


# =============================================================================
# Public API
# =============================================================================

__all__ = ["AddOnRouter"]
