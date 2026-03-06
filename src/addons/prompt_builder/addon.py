"""PromptBuilderAddOn — Working Prompt bauen und System-Prompt pro Turn assemblieren.

Zwei Aufgaben:
  1. Session-Start: base + type + instance → mechanischer Merge → working prompt
     in PromptAddOn-Registry speichern. Wird auch bei PROMPT_CHANGED neu gebaut.
  2. Pro Turn (ON_CONTEXT_BUILD): working prompt + Zeitkontext + Facts + Skills + Tools
     → ctx.system_prompt

Namensschema (aus AgentIdentity):
  system                          → base layer
  {identity.role}                 → type layer  (z.B. 'researcher')
  {identity.name}                 → instance layer (z.B. 'riker')
  {identity.name}.working-prompt  → assemblierter working prompt

Importpfad:
    from addons.prompt_builder import PromptBuilderAddOn

Abhängigkeit: PromptAddOn muss vor diesem AddOn eingehängt sein.
ON_CONTEXT_BUILD → ctx.system_prompt setzen.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined, TemplateNotFound

from core.addon import AddOn
from core.models import PipelineContext, ContextHistory, AddOnResult

logger = logging.getLogger(__name__)

# Layer-Namen
SYSTEM_PROMPT_NAME = "system"          # base layer — immer gleich
WORKING_PROMPT_SUFFIX = "working-prompt"  # {name}.working-prompt

# Pfad zum mitgelieferten Default-Template
_DEFAULT_TEMPLATE_DIR = Path(__file__).parent / "templates"
_DEFAULT_TEMPLATE_NAME = "default.j2"

# Fallback wenn kein Heinzel-Name bekannt
WORKING_PROMPT_NAME = "working"


class PromptBuilderAddOn(AddOn):
    """Baut den working prompt und assembliert ctx.system_prompt bei jedem Turn.

    Konfiguration (in heinzel.yaml):
        addons:
          prompt_builder:
            template_path: prompts/templates/   # optional

    Working-Prompt-Aufbau (einmalig bei on_attach und bei PROMPT_CHANGED):
        system.yaml + {role}.yaml + {name}.yaml
        → mechanischer Merge (Konkatenation, Layer für Layer)
        → gespeichert als {name}.working-prompt in PromptAddOn-Registry

    Turn-Assembler (ON_CONTEXT_BUILD):
        working prompt + Zeitkontext + Facts + Skills + Tools → ctx.system_prompt
    """

    name = "prompt_builder"
    version = "0.1.0"
    dependencies = ["prompt"]

    def __init__(
        self,
        template_path: str | Path | None = None,
    ) -> None:
        self._template_path = Path(template_path) if template_path else None
        self._jinja_env: Environment | None = None
        self._template_name: str = _DEFAULT_TEMPLATE_NAME
        self._prompt_addon = None
        self._working_prompt_name: str = WORKING_PROMPT_NAME  # wird in on_attach gesetzt
        self._heinzel_name: str = ""
        self._heinzel_role: str = ""

    # -------------------------------------------------------------------------
    # AddOn Lifecycle
    # -------------------------------------------------------------------------

    async def on_attach(self, heinzel) -> None:
        """Jinja2-Environment init, Identity auslesen, working prompt bauen."""
        # PromptAddOn holen
        self._prompt_addon = heinzel.addons.get("prompt")
        if self._prompt_addon is None:
            logger.warning("[PromptBuilderAddOn] PromptAddOn nicht gefunden")

        # Identity aus Config
        try:
            identity = heinzel.config.agent
            self._heinzel_name = (identity.name or "heinzel").lower().replace(" ", "-")
            self._heinzel_role = (identity.role or "assistant").lower().replace(" ", "-")
        except Exception:
            self._heinzel_name = "heinzel"
            self._heinzel_role = "assistant"

        self._working_prompt_name = f"{self._heinzel_name}.{WORKING_PROMPT_SUFFIX}"

        # Template-Verzeichnis
        template_dir = self._template_path or _DEFAULT_TEMPLATE_DIR
        if not Path(template_dir).exists():
            logger.warning(
                f"[PromptBuilderAddOn] Template-Dir '{template_dir}' fehlt — nutze Default"
            )
            template_dir = _DEFAULT_TEMPLATE_DIR

        self._jinja_env = Environment(
            loader=FileSystemLoader(str(template_dir)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
        )

        # PROMPT_CHANGED Listener registrieren
        if self._prompt_addon is not None:
            self._prompt_addon.on_prompt_changed(self._on_prompt_changed)

        # Working prompt initial bauen
        await self.build_working_prompt()

        logger.info(
            f"[PromptBuilderAddOn] bereit — "
            f"heinzel='{self._heinzel_name}', role='{self._heinzel_role}', "
            f"working='{self._working_prompt_name}'"
        )

    async def on_detach(self, heinzel) -> None:
        self._jinja_env = None
        self._prompt_addon = None

    # -------------------------------------------------------------------------
    # Working Prompt aufbauen
    # -------------------------------------------------------------------------

    async def build_working_prompt(self) -> str:
        """base + type + instance → mechanischer Merge → working prompt.

        Liest die drei Layer aus der PromptAddOn-Registry (sofern vorhanden),
        konkateniert sie mit Trennzeile und speichert das Ergebnis als
        '{heinzel-name}.working-prompt' zurück in die Registry.

        Gibt den fertigen Text zurück.

        TODO: LLM-Pass als opt-in (llm_merge: true in Config).
        """
        if self._prompt_addon is None:
            return ""

        layers = []
        for layer_name in [
            SYSTEM_PROMPT_NAME,         # system
            self._heinzel_role,         # z.B. researcher
            self._heinzel_name,         # z.B. riker
        ]:
            text = self._render_layer(layer_name)
            if text:
                layers.append(text)

        if not layers:
            logger.debug("[PromptBuilderAddOn] Keine Layer gefunden — working prompt leer")
            return ""

        merged = "\n\n".join(layers)

        # Als working prompt in der Registry speichern
        if self._prompt_addon.get(self._working_prompt_name) is not None:
            await self._prompt_addon.mutate(self._working_prompt_name, "template", merged)
        else:
            await self._store_working_prompt(merged)

        logger.info(
            f"[PromptBuilderAddOn] working prompt gebaut aus {len(layers)} Layer(n)"
        )
        return merged

    def get_working_prompt_text(self) -> str:
        """Fertigen working prompt als Text zurückgeben.

        Wird vom CLI-Kommando !prompt aufgerufen.
        Gibt leeren String zurück wenn kein working prompt vorhanden.
        """
        if self._prompt_addon is None:
            return ""
        prompt = self._prompt_addon.get(self._working_prompt_name)
        if prompt is None:
            return ""
        try:
            return prompt.render()
        except Exception as exc:
            logger.warning(f"[PromptBuilderAddOn] Fehler beim Rendern: {exc}")
            return ""

    # -------------------------------------------------------------------------
    # Pipeline Hook
    # -------------------------------------------------------------------------

    async def on_context_ready(
        self, ctx: PipelineContext, history: ContextHistory | None = None
    ) -> AddOnResult:
        """ON_CONTEXT_BUILD — ctx.system_prompt setzen."""
        system_prompt = self.render(
            template_name=self._template_name,
            metadata=ctx.metadata if hasattr(ctx, "metadata") else {},
        )
        return AddOnResult(modified_ctx=ctx.model_copy(update={"system_prompt": system_prompt}))

    # -------------------------------------------------------------------------
    # Öffentliche API
    # -------------------------------------------------------------------------

    def render(
        self,
        template_name: str = _DEFAULT_TEMPLATE_NAME,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """System-Prompt rendern (working prompt + Turn-Kontext).

        Kann direkt aufgerufen werden (z.B. für Vorschau oder Tests).
        """
        if self._jinja_env is None:
            raise RuntimeError(
                "[PromptBuilderAddOn] nicht initialisiert — on_attach() aufrufen"
            )

        metadata = metadata or {}
        identity = self._get_working_prompt()

        try:
            template = self._jinja_env.get_template(template_name)
        except TemplateNotFound:
            logger.warning(
                f"[PromptBuilderAddOn] Template '{template_name}' nicht gefunden — nutze Default"
            )
            template = self._jinja_env.get_template(_DEFAULT_TEMPLATE_NAME)

        result = template.render(
            identity=identity,
            now=_format_now(),
            facts=metadata.get("facts") or [],
            skills=metadata.get("skills") or [],
            tools=metadata.get("tools") or [],
            search_results=metadata.get("search_results") or "",
        )
        return _compress_blank_lines(result)

    def set_template(self, template_name: str) -> None:
        """Aktives Template wechseln — wirkt ab dem nächsten render()."""
        self._template_name = template_name
        logger.info(f"[PromptBuilderAddOn] Template gewechselt zu '{template_name}'")

    # -------------------------------------------------------------------------
    # Interna
    # -------------------------------------------------------------------------

    def _render_layer(self, layer_name: str) -> str:
        """Einzelnen Layer aus PromptAddOn rendern. Leer wenn nicht vorhanden."""
        if self._prompt_addon is None:
            return ""
        prompt = self._prompt_addon.get(layer_name)
        if prompt is None:
            return ""
        try:
            return prompt.render().strip()
        except Exception as exc:
            logger.warning(f"[PromptBuilderAddOn] Layer '{layer_name}' Fehler: {exc}")
            return ""

    async def _store_working_prompt(self, text: str) -> None:
        """Neuen working prompt in Repository anlegen und laden."""
        if self._prompt_addon is None:
            return
        # Via Repository direkt speichern
        repo = self._prompt_addon._repository
        data = {
            "name": self._working_prompt_name,
            "template": text,
            "context": "system",
            "variables": {},
        }
        repo.save(self._working_prompt_name, data)
        await self._prompt_addon.reload_one(self._working_prompt_name)

    def _get_working_prompt(self) -> str:
        """Working prompt Text holen — intern für render()."""
        return self.get_working_prompt_text()

    def _on_prompt_changed(self, event_type, name: str, entry) -> None:
        """PROMPT_CHANGED Listener — working prompt neu bauen wenn Layer betroffen."""
        from addons.prompt.addon import PromptEventType
        if event_type != PromptEventType.PROMPT_CHANGED:
            return
        relevant = {SYSTEM_PROMPT_NAME, self._heinzel_role, self._heinzel_name}
        if name in relevant:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.build_working_prompt())
            except RuntimeError:
                pass  # Kein laufender Loop — beim nächsten on_attach gebaut


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
