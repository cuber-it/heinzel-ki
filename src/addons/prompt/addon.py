"""PromptAddOn — Prompt-Templates verwalten, rendern und hot-reloaden.

Prompts sind passive Jinja2-Templates ohne eigene Logik.
Sie werden explizit aufgerufen — kein automatischer HookPoint.

Importpfad:
    from addons.prompt import PromptAddOn
    from addons.prompt.addon import PromptEventType

Architektur:
    PromptAddOn verwaltet eine Registry von PromptBase-Instanzen.
    Persistenz über PromptRepository (aktuell YAML, später DB austauschbar).
    Listener werden bei PROMPT_CHANGED benachrichtigt.
"""

from __future__ import annotations

import enum
import hashlib
import logging
from typing import Callable

from core.addon import AddOn, AddOnState
from core.addon_extension import PromptBase
from core.models import PipelineContext, ContextHistory

from .repository import PromptRepository, YamlPromptRepository

logger = logging.getLogger(__name__)


# =============================================================================
# Events — minimal, später durch EventBus ersetzbar
# =============================================================================


class PromptEventType(str, enum.Enum):
    """Prompt-spezifische Events.

    TODO: Wenn EventBus implementiert wird, diese Enum dort registrieren
          und _notify() durch event_bus.publish() ersetzen — eine Zeile.
    """
    PROMPT_CHANGED = "prompt_changed"
    PROMPT_LOADED = "prompt_loaded"
    PROMPT_REMOVED = "prompt_removed"


# Typ-Alias für Listener-Callbacks
PromptListener = Callable[[PromptEventType, str, "PromptEntry"], None]


# =============================================================================
# PromptEntry — interner Registry-Eintrag
# =============================================================================


class PromptEntry:
    """Registry-Eintrag für einen geladenen Prompt.

    Enthält die PromptBase-Instanz plus Metadaten für Hot-Reload.
    """

    def __init__(self, prompt: PromptBase, source_hash: str) -> None:
        self.prompt = prompt
        self.source_hash = source_hash  # SHA256 des YAML-Inhalts für Change-Detection

    def __repr__(self) -> str:
        return f"PromptEntry(name={self.prompt.name!r}, version={self.prompt.version!r})"


# =============================================================================
# PromptAddOn
# =============================================================================


class PromptAddOn(AddOn):
    """Verwaltet Prompt-Templates — laden, rendern, hot-reload, mutieren.

    Konfiguration (in heinzel.yaml):
        addons:
          prompt:
            directory: prompts/          # Verzeichnis mit YAML-Dateien

    Drei Layer werden von außen befüllt (durch PromptBuilderAddOn):
        layer 'base'     → Basis-Prompt für alle Heinzels
        layer 'type'     → Typ-spezifischer Prompt
        layer 'instance' → Heinzel-spezifischer Prompt
    """

    name = "prompt"
    version = "0.1.0"
    dependencies: list[str] = []

    def __init__(
        self,
        repository: PromptRepository | None = None,
        directory: str = "prompts",
    ) -> None:
        self._repository: PromptRepository = repository or YamlPromptRepository(directory)
        self._registry: dict[str, PromptEntry] = {}
        self._listeners: list[PromptListener] = []

    # -------------------------------------------------------------------------
    # AddOn Lifecycle
    # -------------------------------------------------------------------------

    async def on_attach(self, heinzel) -> None:
        """Alle Prompts aus Repository laden beim Einhängen."""
        await self._load_all()
        logger.info(f"[PromptAddOn] {len(self._registry)} Prompts geladen")

    async def on_detach(self, heinzel) -> None:
        """Alle Prompts entladen."""
        for entry in self._registry.values():
            await entry.prompt.unload()
        self._registry.clear()
        logger.info("[PromptAddOn] Alle Prompts entladen")

    # -------------------------------------------------------------------------
    # Öffentliche API
    # -------------------------------------------------------------------------

    async def load_from_dir(self, path: str) -> None:
        """Prompts aus zusätzlichem Verzeichnis nachladen.

        Nützlich wenn zur Laufzeit ein neues Prompt-Verzeichnis eingebunden wird.
        """
        repo = YamlPromptRepository(path)
        await self._load_from_repository(repo)

    def render(self, prompt_name: str, **variables) -> str:
        """Prompt mit gegebenem Namen rendern.

        variables überschreiben die Default-Werte des Prompts.
        Wirft KeyError wenn Prompt nicht bekannt.
        Wirft RuntimeError wenn Prompt nicht geladen (load() nicht aufgerufen).
        """
        entry = self._registry.get(prompt_name)
        if entry is None:
            raise KeyError(f"Prompt '{prompt_name}' nicht gefunden. Bekannte Prompts: {self.list_names()}")
        return entry.prompt.render(**variables)

    async def hot_reload(self) -> int:
        """Alle Prompts neu laden — nur geänderte werden ersetzt.

        Gibt Anzahl der geänderten Prompts zurück.
        Change-Detection via SHA256-Hash des YAML-Inhalts.
        """
        changed = 0
        raw_list = self._repository.load_all()
        for data in raw_list:
            name = data.get("name", "")
            if not name:
                continue
            source_hash = _hash_dict(data)
            existing = self._registry.get(name)
            if existing and existing.source_hash == source_hash:
                continue  # Keine Änderung
            # Geänderter oder neuer Prompt
            entry = await self._build_entry(data)
            if entry:
                if existing:
                    await existing.prompt.unload()
                self._registry[name] = entry
                self._notify(PromptEventType.PROMPT_CHANGED, name, entry)
                changed += 1
                logger.info(f"[PromptAddOn] hot_reload: '{name}' neu geladen")
        return changed

    async def reload_one(self, name: str) -> bool:
        """Einzelnen Prompt neu laden.

        Gibt True zurück wenn Prompt gefunden und geladen wurde.
        """
        data = self._repository.load_one(name)
        if data is None:
            logger.warning(f"[PromptAddOn] reload_one: '{name}' nicht im Repository")
            return False
        entry = await self._build_entry(data)
        if entry is None:
            return False
        existing = self._registry.get(name)
        if existing:
            await existing.prompt.unload()
        self._registry[name] = entry
        self._notify(PromptEventType.PROMPT_CHANGED, name, entry)
        logger.info(f"[PromptAddOn] '{name}' neu geladen")
        return True

    async def mutate(self, name: str, section: str, content: str) -> None:
        """Einzelne Sektion eines Prompts ändern und persistieren.

        section ist ein freier Key — z.B. 'template', 'variables', oder ein
        custom-Topic. Schreibt zurück ins Repository (YAML).

        TODO: Wenn DbPromptRepository kommt, hier nichts ändern — Repository-Interface
              bleibt gleich.
        """
        data = self._repository.load_one(name)
        if data is None:
            raise KeyError(f"Prompt '{name}' nicht gefunden")
        data[section] = content
        self._repository.save(name, data)
        await self.reload_one(name)
        logger.info(f"[PromptAddOn] '{name}'.{section} mutiert und gespeichert")

    def get(self, name: str) -> PromptBase | None:
        """PromptBase-Instanz nach Name holen. None wenn nicht bekannt."""
        entry = self._registry.get(name)
        return entry.prompt if entry else None

    def list_names(self) -> list[str]:
        """Namen aller geladenen Prompts."""
        return sorted(self._registry.keys())

    def on_prompt_changed(self, callback: PromptListener) -> None:
        """Listener für Prompt-Events registrieren.

        TODO: Wenn EventBus kommt, durch event_bus.subscribe() ersetzen.
        """
        self._listeners.append(callback)

    def remove_listener(self, callback: PromptListener) -> None:
        """Listener wieder entfernen."""
        self._listeners.discard(callback) if hasattr(self._listeners, 'discard') else None
        if callback in self._listeners:
            self._listeners.remove(callback)

    # -------------------------------------------------------------------------
    # Interna
    # -------------------------------------------------------------------------

    async def _load_all(self) -> None:
        raw_list = self._repository.load_all()
        for data in raw_list:
            entry = await self._build_entry(data)
            if entry:
                self._registry[entry.prompt.name] = entry
                self._notify(PromptEventType.PROMPT_LOADED, entry.prompt.name, entry)

    async def _load_from_repository(self, repo: PromptRepository) -> None:
        for data in repo.load_all():
            entry = await self._build_entry(data)
            if entry:
                self._registry[entry.prompt.name] = entry
                self._notify(PromptEventType.PROMPT_LOADED, entry.prompt.name, entry)

    async def _build_entry(self, data: dict) -> PromptEntry | None:
        """Dict aus YAML → PromptEntry (mit geladenem PromptBase)."""
        name = data.get("name", "")
        template = data.get("template", "")
        if not name or not template:
            logger.warning(f"[PromptAddOn] Ungültiger Prompt-Eintrag: name={name!r}")
            return None
        prompt = _PromptFromYaml(
            name=name,
            template=template,
            version=data.get("version", "0.1.0"),
            variables=data.get("variables") or {},
            context=data.get("context", "system"),
        )
        try:
            await prompt.load()
        except Exception as exc:
            logger.error(f"[PromptAddOn] Fehler beim Laden von '{name}': {exc}")
            return None
        return PromptEntry(prompt=prompt, source_hash=_hash_dict(data))

    def _notify(self, event_type: PromptEventType, name: str, entry: PromptEntry) -> None:
        """Alle Listener benachrichtigen.

        TODO: Durch event_bus.publish(event_type, name, entry) ersetzen.
        """
        for listener in self._listeners:
            try:
                listener(event_type, name, entry)
            except Exception as exc:
                logger.error(f"[PromptAddOn] Listener-Fehler bei {event_type}: {exc}")

    # AddOn-Hooks — PromptAddOn hat keine Pipeline-Hooks (passives AddOn)
    # on_pre_turn / on_post_turn werden nicht überschrieben


# =============================================================================
# Interne PromptBase-Implementierung aus YAML
# =============================================================================


class _PromptFromYaml(PromptBase):
    """Konkrete PromptBase-Instanz aus YAML-Daten.

    Nicht für externe Nutzung — nur intern im PromptAddOn.
    """
    # load() und unload() sind in PromptBase implementiert


# =============================================================================
# Hilfsfunktionen
# =============================================================================


def _hash_dict(data: dict) -> str:
    """SHA256-Hash eines Dicts für Change-Detection."""
    import json
    content = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(content.encode()).hexdigest()
