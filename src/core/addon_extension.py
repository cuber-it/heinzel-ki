"""Basis-Klassen für AddOn-Erweiterungen — Skills und Prompts.

Skills und Prompts sind konzeptionell verschieden, teilen aber denselben
Lifecycle (load/unload) und dieselbe Identität (name/version).

Importpfad:
    from core.addon_extension import BaseAddOnExtension, SkillBase, PromptBase

Hierarchie:
    BaseAddOnExtension (ABC)
        SkillBase       — Code + Tools + Prompt-Fragmente kombinierbar
        PromptBase      — Reine Jinja2-Templates, keine eigene Logik
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal


# =============================================================================
# BaseAddOnExtension — gemeinsamer Vertrag für Skills und Prompts
# =============================================================================


class BaseAddOnExtension(ABC):
    """Abstrakte Basis für alle AddOn-Erweiterungen.

    Jede konkrete Erweiterung muss name und version definieren
    sowie load() und unload() implementieren.
    """

    name: str
    version: str

    @abstractmethod
    async def load(self) -> None:
        """Erweiterung initialisieren — z.B. Templates kompilieren, Ressourcen laden."""
        ...

    @abstractmethod
    async def unload(self) -> None:
        """Erweiterung sauber beenden — Ressourcen freigeben."""
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r}, version={self.version!r})"


# =============================================================================
# SkillBase — Skill-Vertrag
# =============================================================================


class SkillBase(BaseAddOnExtension):
    """Basis für alle Skills.

    Ein Skill kombiniert:
      - trigger_patterns:        Wann wird dieser Skill aktiviert? (regex/keywords)
      - system_prompt_fragment:  Was wird dem System-Prompt hinzugefügt?
      - tools:                   Optionale Tool-Referenzen (MCP-Tool-Namen)

    Konkrete Skills erben von SkillBase und implementieren load()/unload().
    Komplexe Skills können zusätzlich eigene Methoden mitbringen.
    """

    description: str
    trigger_patterns: list[str]
    system_prompt_fragment: str
    tools: list[str]

    def __init__(
        self,
        name: str,
        version: str = "0.1.0",
        description: str = "",
        trigger_patterns: list[str] | None = None,
        system_prompt_fragment: str = "",
        tools: list[str] | None = None,
    ) -> None:
        self.name = name
        self.version = version
        self.description = description
        self.trigger_patterns = trigger_patterns or []
        self.system_prompt_fragment = system_prompt_fragment
        self.tools = tools or []

    async def load(self) -> None:
        """Standard-Implementierung: kein Setup nötig. Überschreiben wenn nötig."""

    async def unload(self) -> None:
        """Standard-Implementierung: kein Teardown nötig. Überschreiben wenn nötig."""


# =============================================================================
# PromptBase — Prompt-Template-Vertrag
# =============================================================================

# Erlaubte Kontexte für einen Prompt
PromptContext = Literal["system", "user", "few-shot"]


class PromptBase(BaseAddOnExtension):
    """Basis für alle Prompt-Templates.

    Ein Prompt ist ein reines Jinja2-Template ohne eigene Logik:
      - template:   Jinja2-Template-String
      - variables:  Default-Werte für Template-Variablen
      - context:    Wofür ist dieser Prompt — system, user oder few-shot

    render(**kwargs) rendert das Template mit den übergebenen Variablen,
    Default-Werte werden durch kwargs überschrieben.
    """

    template: str
    variables: dict
    context: PromptContext

    def __init__(
        self,
        name: str,
        template: str,
        version: str = "0.1.0",
        variables: dict | None = None,
        context: PromptContext = "system",
    ) -> None:
        self.name = name
        self.version = version
        self.template = template
        self.variables = variables or {}
        self.context = context
        self._jinja_env: object | None = None  # wird in load() gesetzt

    async def load(self) -> None:
        """Jinja2-Environment initialisieren und Template kompilieren."""
        from jinja2 import Environment, StrictUndefined

        env = Environment(undefined=StrictUndefined)
        self._compiled = env.from_string(self.template)

    async def unload(self) -> None:
        """Kompiliertes Template freigeben."""
        self._compiled = None

    def render(self, **kwargs) -> str:
        """Template mit Variablen rendern.

        kwargs überschreiben die Default-Werte aus self.variables.
        Wirft TemplateError wenn Variablen fehlen (StrictUndefined).
        """
        if not hasattr(self, "_compiled") or self._compiled is None:
            raise RuntimeError(f"PromptBase '{self.name}' wurde nicht geladen — load() aufrufen")
        merged = {**self.variables, **kwargs}
        return self._compiled.render(**merged)
