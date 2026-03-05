"""PromptBuilderAddOn — System-Prompt pro Turn assemblieren.

Nimmt den working prompt aus der PromptAddOn-Registry und reichert ihn
turn-spezifisch an: Zeitkontext, Facts, Skills, Tools.

Importpfad:
    from addons.prompt_builder import PromptBuilderAddOn

Abhängigkeit: PromptAddOn muss vor diesem AddOn eingehängt sein.
Der working prompt muss unter dem Namen 'working' in der Registry liegen.

ON_CONTEXT_BUILD → ctx.system_prompt setzen.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

from core.addon import AddOn
from core.models import PipelineContext, ContextHistory

logger = logging.getLogger(__name__)

# Standardname des working prompt in der PromptAddOn-Registry
WORKING_PROMPT_NAME = "working"

# Pfad zum mitgelieferten Default-Template
_DEFAULT_TEMPLATE_DIR = Path(__file__).parent / "templates"
_DEFAULT_TEMPLATE_NAME = "default.j2"


class PromptBuilderAddOn(AddOn):
    """Assembliert ctx.system_prompt bei jedem Turn.

    Konfiguration (in heinzel.yaml):
        addons:
          prompt_builder:
            template_path: prompts/templates/   # optional, default = eingebautes default.j2
            working_prompt_name: working         # optional

    Quellen für den System-Prompt (pro Turn):
        1. working prompt aus PromptAddOn-Registry (Kern)
        2. Zeitkontext (Deutsch, ISO-Format)
        3. ctx.metadata['facts']  — Liste von Strings
        4. ctx.metadata['skills'] — Liste von Strings
        5. ctx.metadata['tools']  — Liste von Strings (Platzhalter)
    """

    name = "prompt_builder"
    version = "0.1.0"
    dependencies = ["prompt"]  # PromptAddOn muss zuerst eingehängt sein

    def __init__(
        self,
        template_path: str | Path | None = None,
        working_prompt_name: str = WORKING_PROMPT_NAME,
    ) -> None:
        self._template_path = Path(template_path) if template_path else None
        self._working_prompt_name = working_prompt_name
        self._jinja_env: Environment | None = None
        self._template_name: str = _DEFAULT_TEMPLATE_NAME
        self._prompt_addon = None  # Referenz auf PromptAddOn, gesetzt in on_attach

    # -------------------------------------------------------------------------
    # AddOn Lifecycle
    # -------------------------------------------------------------------------

    async def on_attach(self, heinzel) -> None:
        """Jinja2-Environment initialisieren, PromptAddOn-Referenz holen."""
        # PromptAddOn aus dem AddOnManager holen
        self._prompt_addon = heinzel.addons.get("prompt")
        if self._prompt_addon is None:
            logger.warning(
                "[PromptBuilderAddOn] PromptAddOn nicht gefunden — "
                "working prompt wird nicht verfügbar sein"
            )

        # Template-Verzeichnis: custom oder eingebaut
        template_dir = self._template_path or _DEFAULT_TEMPLATE_DIR
        if not Path(template_dir).exists():
            logger.warning(
                f"[PromptBuilderAddOn] Template-Verzeichnis '{template_dir}' "
                "nicht gefunden — nutze eingebautes Default"
            )
            template_dir = _DEFAULT_TEMPLATE_DIR

        self._jinja_env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )
        logger.info(
            f"[PromptBuilderAddOn] bereit — Template-Dir: {template_dir}, "
            f"working prompt: '{self._working_prompt_name}'"
        )

    async def on_detach(self, heinzel) -> None:
        self._jinja_env = None
        self._prompt_addon = None

    # -------------------------------------------------------------------------
    # Pipeline Hook
    # -------------------------------------------------------------------------

    async def on_context_build(
        self, ctx: PipelineContext, history: ContextHistory | None = None
    ) -> PipelineContext:
        """ON_CONTEXT_BUILD — ctx.system_prompt setzen.

        Wird vom Runner vor dem LLM-Call aufgerufen.
        """
        system_prompt = self.render(
            template_name=self._template_name,
            metadata=ctx.metadata if hasattr(ctx, "metadata") else {},
        )
        # PipelineContext ist immutable — via model_copy ersetzen
        return ctx.model_copy(update={"system_prompt": system_prompt})

    # -------------------------------------------------------------------------
    # Öffentliche API
    # -------------------------------------------------------------------------

    def render(
        self,
        template_name: str = _DEFAULT_TEMPLATE_NAME,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """System-Prompt rendern.

        Kann direkt aufgerufen werden (z.B. für Vorschau oder Tests).
        template_name: Dateiname relativ zum Template-Verzeichnis.
        metadata: dict mit optionalen Keys 'facts', 'skills', 'tools'.
        """
        if self._jinja_env is None:
            raise RuntimeError(
                "[PromptBuilderAddOn] nicht initialisiert — on_attach() aufrufen"
            )

        metadata = metadata or {}

        # Working prompt aus PromptAddOn holen (hot-reload-fähig)
        identity = self._get_working_prompt()

        # Template laden — bei jedem Call neu (hot-reload)
        try:
            template = self._jinja_env.get_template(template_name)
        except TemplateNotFound:
            logger.warning(
                f"[PromptBuilderAddOn] Template '{template_name}' nicht gefunden "
                "— nutze Default"
            )
            template = self._jinja_env.get_template(_DEFAULT_TEMPLATE_NAME)

        result = template.render(
            identity=identity,
            now=_format_now(),
            facts=metadata.get("facts") or [],
            skills=metadata.get("skills") or [],
            tools=metadata.get("tools") or [],
        )

        # Leere Zeilen komprimieren (max. eine Leerzeile hintereinander)
        return _compress_blank_lines(result)

    def set_template(self, template_name: str) -> None:
        """Aktives Template wechseln — wirkt ab dem nächsten render()."""
        self._template_name = template_name
        logger.info(f"[PromptBuilderAddOn] Template gewechselt zu '{template_name}'")

    # -------------------------------------------------------------------------
    # Interna
    # -------------------------------------------------------------------------

    def _get_working_prompt(self) -> str:
        """Working prompt aus PromptAddOn-Registry holen.

        Fallback: leerer String (PromptBuilderAddOn funktioniert auch ohne).
        """
        if self._prompt_addon is None:
            return ""
        prompt = self._prompt_addon.get(self._working_prompt_name)
        if prompt is None:
            return ""
        try:
            return prompt.render()
        except Exception as exc:
            logger.warning(
                f"[PromptBuilderAddOn] Fehler beim Rendern von working prompt: {exc}"
            )
            return ""


# =============================================================================
# Hilfsfunktionen
# =============================================================================


def _format_now() -> str:
    """Aktuelles Datum/Uhrzeit auf Deutsch formatiert."""
    now = datetime.now()
    weekdays = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    months = [
        "Januar", "Februar", "März", "April", "Mai", "Juni",
        "Juli", "August", "September", "Oktober", "November", "Dezember",
    ]
    return (
        f"{weekdays[now.weekday()]}, {now.day}. {months[now.month - 1]} {now.year}, "
        f"{now.strftime('%H:%M')} Uhr"
    )


def _compress_blank_lines(text: str) -> str:
    """Mehrfache Leerzeilen auf eine reduzieren, trailing whitespace entfernen."""
    lines = text.splitlines()
    result = []
    prev_blank = False
    for line in lines:
        stripped = line.rstrip()
        is_blank = stripped == ""
        if is_blank and prev_blank:
            continue
        result.append(stripped)
        prev_blank = is_blank
    return "\n".join(result).strip()
