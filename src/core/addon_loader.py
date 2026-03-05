"""AddOnLoader — Hot-Reload von AddOns zur Laufzeit.

AddOns laden, registrieren und entfernen ohne Neustart.

Verwendung:
    loader = AddOnLoader(runner)
    ok = await loader.load_and_register(my_addon, hooks={HookPoint.ON_INPUT})
    ok = await loader.unload_and_detach("my_addon")

Fehler-Isolation:
    AddOnLoadError bei gescheitertem Load — Heinzel läuft weiter.
    Laufende Hooks werden beim Unload abgewartet (max. 5s).
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
from pathlib import Path
from typing import Any

from core.addon import AddOn
from core.exceptions import AddOnError
from core.models import HookPoint

logger = logging.getLogger(__name__)

_UNLOAD_TIMEOUT = 5.0   # Sekunden warten bis force-unload


class AddOnLoadError(AddOnError):
    """Fehler beim Laden eines AddOns."""


class AddOnLoader:
    """Lädt und entfernt AddOns zur Laufzeit.

    Hält Tracking welche AddOns dynamisch geladen wurden
    und wie viele aktive Hook-Calls gerade laufen.
    """

    def __init__(self, runner: Any) -> None:
        self._runner = runner
        self._active_calls: dict[str, int] = {}     # addon_name → laufende Calls
        self._unloading: set[str] = set()           # addon_name → wird gerade entladen
        self._lock = asyncio.Lock()

    # -------------------------------------------------------------------------
    # Laden
    # -------------------------------------------------------------------------

    def load_from_file(self, path: str | Path, addon_class: str) -> AddOn:
        """Python-Modul aus Dateipfad laden und AddOn-Klasse instanziieren.

        Args:
            path:        Pfad zur .py-Datei
            addon_class: Name der AddOn-Klasse im Modul
        """
        path = Path(path)
        if not path.exists():
            raise AddOnLoadError(f"Datei nicht gefunden: {path}")

        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            raise AddOnLoadError(f"Modul konnte nicht geladen werden: {path}")

        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            raise AddOnLoadError(f"Fehler beim Ausführen von '{path}': {exc}") from exc

        cls = getattr(module, addon_class, None)
        if cls is None:
            raise AddOnLoadError(f"Klasse '{addon_class}' nicht in '{path}' gefunden")
        if not (isinstance(cls, type) and issubclass(cls, AddOn)):
            raise AddOnLoadError(f"'{addon_class}' ist kein AddOn")

        try:
            return cls()
        except Exception as exc:
            raise AddOnLoadError(f"Instanziierung von '{addon_class}' fehlgeschlagen: {exc}") from exc

    def load_from_package(self, package: str, class_name: str) -> AddOn:
        """AddOn aus installiertem Package laden.

        Args:
            package:    Python-Package-Pfad, z.B. 'addons.web_search'
            class_name: Name der AddOn-Klasse
        """
        try:
            module = importlib.import_module(package)
        except ImportError as exc:
            raise AddOnLoadError(f"Package '{package}' nicht gefunden: {exc}") from exc

        cls = getattr(module, class_name, None)
        if cls is None:
            raise AddOnLoadError(f"Klasse '{class_name}' nicht in '{package}' gefunden")
        if not (isinstance(cls, type) and issubclass(cls, AddOn)):
            raise AddOnLoadError(f"'{class_name}' ist kein AddOn")

        try:
            return cls()
        except Exception as exc:
            raise AddOnLoadError(f"Instanziierung fehlgeschlagen: {exc}") from exc

    async def load_and_register(
        self,
        addon: AddOn,
        hooks: set[HookPoint],
        priority: int = 0,
    ) -> bool:
        """AddOn registrieren und on_attach aufrufen.

        Returns:
            True bei Erfolg, False bei Fehler (Heinzel läuft weiter).
        """
        # Dependency-Check
        for dep in getattr(addon, "dependencies", []):
            if not any(a.name == dep for a in self._runner._addons):
                raise AddOnLoadError(
                    f"Dependency '{dep}' für '{addon.name}' nicht geladen"
                )

        try:
            self._runner.register_addon(addon, hooks=hooks, priority=priority)
            await addon.on_attach(self._runner)
            self._active_calls[addon.name] = 0
            logger.info(f"[AddOnLoader] '{addon.name}' geladen und registriert")
            return True
        except Exception as exc:
            # Rollback — AddOn wieder aus Liste entfernen
            try:
                self._runner._addons.remove(addon)
                self._runner._router._entries.pop(addon.name, None)
            except Exception:
                pass
            logger.error(f"[AddOnLoader] Fehler beim Laden von '{addon.name}': {exc}")
            return False

    # -------------------------------------------------------------------------
    # Entladen
    # -------------------------------------------------------------------------

    async def unload_and_detach(self, addon_name: str) -> bool:
        """AddOn sauber entladen.

        Wartet auf laufende Hook-Calls (max. 5s), dann force.

        Returns:
            True bei Erfolg, False wenn nicht gefunden.
        """
        addon = self._runner.addons.get(addon_name)
        if addon is None:
            logger.warning(f"[AddOnLoader] '{addon_name}' nicht gefunden")
            return False

        async with self._lock:
            self._unloading.add(addon_name)

        # Laufende Calls abwarten
        waited = 0.0
        while self._active_calls.get(addon_name, 0) > 0 and waited < _UNLOAD_TIMEOUT:
            await asyncio.sleep(0.1)
            waited += 0.1

        if waited >= _UNLOAD_TIMEOUT:
            logger.warning(
                f"[AddOnLoader] Force-Unload '{addon_name}' "
                f"nach {_UNLOAD_TIMEOUT}s ({self._active_calls.get(addon_name, 0)} offene Calls)"
            )

        # Aus Router entfernen
        try:
            self._runner._router._entries.pop(addon_name, None)
        except AttributeError:
            pass

        try:
            self._runner._addons.remove(addon)
        except ValueError:
            pass

        # on_detach aufrufen
        try:
            await addon.on_detach(self._runner)
        except Exception as exc:
            logger.error(f"[AddOnLoader] on_detach Fehler für '{addon_name}': {exc}")

        self._active_calls.pop(addon_name, None)
        self._unloading.discard(addon_name)
        logger.info(f"[AddOnLoader] '{addon_name}' entladen")
        return True

    # -------------------------------------------------------------------------
    # Status
    # -------------------------------------------------------------------------

    def list_loaded(self) -> list[dict]:
        """Liste aller geladenen AddOns."""
        return [
            {
                "name": a.name,
                "version": getattr(a, "version", "?"),
                "active_calls": self._active_calls.get(a.name, 0),
                "unloading": a.name in self._unloading,
            }
            for a in self._runner._addons
        ]

    def is_unloading(self, addon_name: str) -> bool:
        """True wenn AddOn gerade entladen wird — neue Requests ignorieren."""
        return addon_name in self._unloading
