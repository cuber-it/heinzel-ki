"""Skills-Paket — SkillsAddOn (Verwaltung) und SkillLoaderAddOn (Turn-Hook).

SkillsAddOn:
    Registry, hot-reload, Aktivierungsfilter per Config oder Trigger-Pattern.

SkillLoaderAddOn:
    ON_CONTEXT_BUILD → aktive Skills → ctx.metadata['skills']
    Damit stehen sie dem PromptBuilderAddOn zur Verfügung.

Importpfad:
    from addons.skills import SkillsAddOn, SkillLoaderAddOn

YAML-Format:
    name: python-expert
    version: "1.0"
    description: "..."
    instructions: |
      Prompt-Fragment das dem System-Prompt hinzugefügt wird...
    trigger_patterns: [python, code]   # optional
    tools: []                          # MCP-Tool-Namen, optional
    examples: []                       # few-shot, optional
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

from core.addon import AddOn
from core.addon_extension import SkillBase
from core.exceptions import AddOnError
from core.models import PipelineContext, ContextHistory, AddOnResult

from .repository import SkillRepository, YamlSkillRepository

logger = logging.getLogger(__name__)


# =============================================================================
# SkillValidationError
# =============================================================================


class SkillValidationError(AddOnError):
    """Pflichtfeld fehlt oder YAML-Struktur ungültig."""


# =============================================================================
# SkillEntry — interner Registry-Eintrag
# =============================================================================


class SkillEntry:
    """Registry-Eintrag mit SkillBase-Instanz und Metadaten."""

    def __init__(self, skill: SkillBase, source_hash: str) -> None:
        self.skill = skill
        self.source_hash = source_hash

    def __repr__(self) -> str:
        return f"SkillEntry(name={self.skill.name!r})"


# =============================================================================
# SkillsAddOn — Verwaltungsschicht
# =============================================================================


class SkillsAddOn(AddOn):
    """Verwaltet Skills — laden, hot-reload, Aktivierungsfilter.

    Konfiguration (in heinzel.yaml):
        addons:
          skills:
            directory: skills/
            active: [python-expert, web-search]   # leer = alle laden

    get_active(input_text) gibt Skills zurück die zum Input passen:
        1. Wenn Config-Liste 'active' gesetzt → nur diese (in Reihenfolge)
        2. Wenn trigger_patterns vorhanden → Pattern-Match auf input_text
        3. Kein Pattern → Skill ist immer aktiv
    """

    name = "skills"
    version = "0.1.0"
    dependencies: list[str] = []

    def __init__(
        self,
        repository: SkillRepository | None = None,
        directory: str = "skills",
        active: list[str] | None = None,
    ) -> None:
        self._repository: SkillRepository = repository or YamlSkillRepository(directory)
        self._active_filter: list[str] = active or []  # leer = alle
        self._registry: dict[str, SkillEntry] = {}

    # -------------------------------------------------------------------------
    # AddOn Lifecycle
    # -------------------------------------------------------------------------

    async def on_attach(self, heinzel) -> None:
        await self._load_all()
        logger.info(f"[SkillsAddOn] {len(self._registry)} Skills geladen")

    async def on_detach(self, heinzel) -> None:
        for entry in self._registry.values():
            await entry.skill.unload()
        self._registry.clear()

    # -------------------------------------------------------------------------
    # Öffentliche API
    # -------------------------------------------------------------------------

    def get_active(self, input_text: str = "") -> list[SkillBase]:
        """Skills zurückgeben die zum Input passen.

        Logik:
          - active_filter gesetzt → nur diese Skills, in Config-Reihenfolge
          - kein Filter → alle Skills prüfen:
              * trigger_patterns vorhanden → Pattern-Match (case-insensitive)
              * keine trigger_patterns → immer aktiv
        """
        if self._active_filter:
            result = []
            for name in self._active_filter:
                entry = self._registry.get(name)
                if entry:
                    result.append(entry.skill)
                else:
                    logger.warning(f"[SkillsAddOn] aktiver Skill '{name}' nicht in Registry")
            return result

        # Kein Filter — alle prüfen
        result = []
        for entry in self._registry.values():
            if _matches(entry.skill, input_text):
                result.append(entry.skill)
        return result

    async def hot_reload(self) -> int:
        """Alle geänderten Skills neu laden. Gibt Anzahl zurück."""
        changed = 0
        for data in self._repository.load_all():
            name = data.get("name", "")
            if not name:
                continue
            source_hash = _hash_dict(data)
            existing = self._registry.get(name)
            if existing and existing.source_hash == source_hash:
                continue
            entry = _build_entry(data)
            if entry:
                if existing:
                    await existing.skill.unload()
                self._registry[name] = entry
                changed += 1
                logger.info(f"[SkillsAddOn] hot_reload: '{name}' neu geladen")
        return changed

    async def reload_one(self, name: str) -> bool:
        """Einzelnen Skill neu laden."""
        data = self._repository.load_one(name)
        if data is None:
            return False
        entry = _build_entry(data)
        if entry is None:
            return False
        existing = self._registry.get(name)
        if existing:
            await existing.skill.unload()
        self._registry[name] = entry
        logger.info(f"[SkillsAddOn] '{name}' neu geladen")
        return True

    async def load_skill(self, path: str) -> SkillBase:
        """Einzelnen Skill aus Pfad laden und in Registry aufnehmen."""
        from pathlib import Path
        import yaml
        raw = Path(path).read_text(encoding="utf-8")
        data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            raise SkillValidationError(f"Ungültiges YAML in {path}")
        data.setdefault("name", Path(path).stem)
        entry = _build_entry(data)
        if entry is None:
            raise SkillValidationError(f"Skill in {path} ungültig")
        self._registry[entry.skill.name] = entry
        return entry.skill

    async def unload_skill(self, name: str) -> bool:
        """Skill aus Registry entfernen."""
        entry = self._registry.pop(name, None)
        if entry:
            await entry.skill.unload()
            return True
        return False

    def get_skill(self, name: str) -> SkillBase | None:
        entry = self._registry.get(name)
        return entry.skill if entry else None

    def list_skills(self) -> list[str]:
        return sorted(self._registry.keys())

    # -------------------------------------------------------------------------
    # Interna
    # -------------------------------------------------------------------------

    async def _load_all(self) -> None:
        for data in self._repository.load_all():
            entry = _build_entry(data)
            if entry:
                self._registry[entry.skill.name] = entry
            else:
                logger.warning(
                    f"[SkillsAddOn] Skill '{data.get('name', '?')}' übersprungen"
                )


# =============================================================================
# SkillLoaderAddOn — Turn-Hook
# =============================================================================


class SkillLoaderAddOn(AddOn):
    """Setzt ctx.metadata['skills'] bei ON_CONTEXT_BUILD.

    Holt aktive Skills aus SkillsAddOn und legt deren instructions
    als String-Liste in ctx.metadata['skills'] ab — damit der
    PromptBuilderAddOn sie ins Template einbauen kann.

    Abhängigkeit: SkillsAddOn muss vor diesem AddOn eingehängt sein.
    """

    name = "skill_loader"
    version = "0.1.0"
    dependencies = ["skills"]

    def __init__(self) -> None:
        self._skills_addon: SkillsAddOn | None = None

    async def on_attach(self, heinzel) -> None:
        self._skills_addon = heinzel.addons.get("skills")
        if self._skills_addon is None:
            logger.warning("[SkillLoaderAddOn] SkillsAddOn nicht gefunden")

    async def on_detach(self, heinzel) -> None:
        self._skills_addon = None

    async def on_context_build(
        self, ctx: PipelineContext, history: ContextHistory | None = None
    ) -> AddOnResult:
        """Aktive Skills → ctx.metadata['skills']."""
        if self._skills_addon is None:
            return AddOnResult(modified_ctx=ctx)

        input_text = ctx.parsed_input or ""
        active_skills = self._skills_addon.get_active(input_text)

        # instructions-Fragmente sammeln
        skill_fragments = []
        for skill in active_skills:
            fragment = skill.system_prompt_fragment.strip()
            if fragment:
                skill_fragments.append(f"[{skill.name}] {fragment}")

        # metadata ist ein dict — immutable via model_copy
        metadata = dict(ctx.metadata) if ctx.metadata else {}
        metadata["skills"] = skill_fragments
        metadata["active_skill_names"] = [s.name for s in active_skills]

        return AddOnResult(modified_ctx=ctx.model_copy(update={"metadata": metadata}))


# =============================================================================
# Hilfsfunktionen
# =============================================================================


def _build_entry(data: dict) -> SkillEntry | None:
    """Dict → SkillEntry. None bei Validierungsfehler."""
    name = data.get("name", "").strip()
    if not name:
        logger.warning("[skills] Skill ohne Name übersprungen")
        return None

    # instructions ist Pflicht (alias: system_prompt_fragment)
    instructions = (
        data.get("instructions")
        or data.get("system_prompt_fragment")
        or ""
    ).strip()

    skill = _SkillFromYaml(
        name=name,
        version=str(data.get("version", "0.1.0")),
        description=str(data.get("description", "")),
        trigger_patterns=data.get("trigger_patterns") or [],
        system_prompt_fragment=instructions,
        tools=data.get("tools") or [],
    )
    return SkillEntry(skill=skill, source_hash=_hash_dict(data))


def _matches(skill: SkillBase, input_text: str) -> bool:
    """Prüft ob ein Skill zum Input passt.

    Kein trigger_pattern → immer aktiv.
    Mit Pattern → mindestens eines muss matchen (case-insensitive).
    """
    if not skill.trigger_patterns:
        return True
    lower = input_text.lower()
    return any(
        re.search(pattern, lower, re.IGNORECASE)
        for pattern in skill.trigger_patterns
    )


def _hash_dict(data: dict) -> str:
    content = json.dumps(data, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(content.encode()).hexdigest()


# =============================================================================
# Interne SkillBase-Implementierung
# =============================================================================


class _SkillFromYaml(SkillBase):
    """Konkrete SkillBase-Instanz aus YAML. Nur intern."""
    # load/unload sind No-Ops in SkillBase
