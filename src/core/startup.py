"""HeinzelLoader — Startup-Loader: Config → Runner.

Nutzt core.config.get_config() — kein eigenes YAML-Parsing.
Alle Config-Logik (ENV-Override, Pfadsuche, Singleton) liegt in config.py.

Verwendung:
    from core.startup import HeinzelLoader

    loader = HeinzelLoader("config/heinzel.yaml")
    runner = await loader.build()
    await runner.connect()

Eigene AddOns registrieren:
    loader.register_addon_factory("my_addon", my_factory_fn)

Config-Struktur (heinzel.yaml):
    agent:
      name: riker
      id: riker-01

    provider:
      default: anthropic
    providers:
      anthropic:
        url: http://thebrain:12501
        name: claude-3-5-sonnet

    addons:
      database:
        backend: sqlite
        path: data/heinzel.db
      dialog_logger:
        log_dir: logs/dialogs
        rotation_size_mb: 10
        retention_days: 90
      prompt:
        directory: prompts/
      prompt_builder: {}
      skills:
        directory: skills/
        active: []
      skill_loader: {}
      web_search:
        backend: duckduckgo
        max_results: 5
        backends:
          searxng:
            url: http://services:12004
        targets: {}
      mcp_tools_router: {}
      mattermost:
        url: http://services:8065
        token: "${MATTERMOST_TOKEN}"
        channel: heinzel-general
        mention_only: true
        reply_in_thread: true
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable

from core.config import get_config, reset_config, AgentConfig
from core.models import HookPoint
from core.runner import Runner

logger = logging.getLogger(__name__)

# =============================================================================
# Hook-Sets pro AddOn-Typ
# =============================================================================

_HOOKS: dict[str, set[HookPoint]] = {
    # AddOns ohne funktionale Hooks bekommen ON_SESSION_START damit sie
    # im Router-Registry auftauchen und Dependency-Checks funktionieren.
    "database": {HookPoint.ON_SESSION_START},
    "dialog_logger": {
        HookPoint.ON_INPUT,
        HookPoint.ON_OUTPUT,
        HookPoint.ON_THINKING_STEP,
        HookPoint.ON_TOOL_REQUEST,
        HookPoint.ON_TOOL_RESULT,
        HookPoint.ON_ERROR,
    },
    "prompt": {HookPoint.ON_CONTEXT_BUILD},
    "prompt_builder": {HookPoint.ON_CONTEXT_READY},
    "skills": {HookPoint.ON_SESSION_START},
    "skill_loader": {HookPoint.ON_CONTEXT_BUILD},
    "web_search": {HookPoint.ON_CONTEXT_BUILD},
    "mcp_tools_router": {HookPoint.ON_TOOL_REQUEST},
    "mattermost": {HookPoint.ON_SESSION_START},
}

_ADDON_ORDER = [
    "database",
    "dialog_logger",
    "prompt",
    "prompt_builder",
    "skills",
    "skill_loader",
    "web_search",
    "mcp_tools_router",
    "mattermost",
]


# =============================================================================
# AddOn-Factories
# =============================================================================


def _build_database(cfg: dict, config: AgentConfig) -> Any:
    backend = cfg.get("backend", "sqlite")
    if backend == "postgres":
        from addons.database import PostgreSQLAddOn
        return PostgreSQLAddOn(
            dsn=cfg["dsn"],
            min_size=cfg.get("min_size", 2),
            max_size=cfg.get("max_size", 10),
        )
    from addons.database import SQLiteAddOn
    return SQLiteAddOn(path=cfg.get("path", ":memory:"))


def _build_dialog_logger(cfg: dict, config: AgentConfig) -> Any:
    from addons.dialog_logger import DialogLoggerAddOn
    # Fallback auf logging-Section aus AgentConfig
    log_cfg = config.logging
    return DialogLoggerAddOn(
        log_dir=cfg.get("log_dir", log_cfg.dialog_log_path),
        rotation_size_mb=cfg.get("rotation_size_mb", log_cfg.rotation_size_mb),
        retention_days=cfg.get("retention_days", log_cfg.retention_days),
    )


def _build_prompt(cfg: dict, config: AgentConfig) -> Any:
    from addons.prompt import PromptAddOn
    return PromptAddOn(directory=cfg.get("directory", "prompts/"))


def _build_prompt_builder(cfg: dict, config: AgentConfig) -> Any:
    from addons.prompt_builder import PromptBuilderAddOn
    return PromptBuilderAddOn()


def _build_skills(cfg: dict, config: AgentConfig) -> Any:
    from addons.skills import SkillsAddOn
    # Fallback auf skills-Section aus AgentConfig
    return SkillsAddOn(
        directory=cfg.get("directory", config.skills.skills_dir),
        active=cfg.get("active", []),
    )


def _build_skill_loader(cfg: dict, config: AgentConfig) -> Any:
    from addons.skills import SkillLoaderAddOn
    return SkillLoaderAddOn()


def _build_web_search(cfg: dict, config: AgentConfig) -> Any:
    from addons.web_search import WebSearchAddOn
    return WebSearchAddOn(
        backend_name=cfg.get("backend", "duckduckgo"),
        max_results=cfg.get("max_results", 5),
        backends_config=cfg.get("backends", {}),
        targets=cfg.get("targets", {}),
    )


def _build_mcp_tools_router(cfg: dict, config: AgentConfig) -> Any:
    from addons.mcp_router import NoopMCPToolsRouter
    return NoopMCPToolsRouter()


def _build_mattermost(cfg: dict, config: AgentConfig) -> Any:
    from addons.mattermost import MattermostAddOn
    return MattermostAddOn(
        url=cfg["url"],
        token=cfg["token"],   # ENV-Substitution macht config.py
        channel=cfg["channel"],
        team=cfg.get("team", ""),
        mention_only=cfg.get("mention_only", True),
        reply_in_thread=cfg.get("reply_in_thread", True),
    )


_DEFAULT_FACTORIES: dict[str, Callable] = {
    "database": _build_database,
    "dialog_logger": _build_dialog_logger,
    "prompt": _build_prompt,
    "prompt_builder": _build_prompt_builder,
    "skills": _build_skills,
    "skill_loader": _build_skill_loader,
    "web_search": _build_web_search,
    "mcp_tools_router": _build_mcp_tools_router,
    "mattermost": _build_mattermost,
}


# =============================================================================
# HeinzelLoader
# =============================================================================


class HeinzelLoader:
    """Baut einen vollständig verdrahteten Runner aus heinzel.yaml.

    Nutzt core.config.get_config() — kein doppeltes YAML-Parsing.

    Ablauf:
        1. get_config(path) → AgentConfig
        2. Provider aus config.providers bauen
        3. Runner instanziieren
        4. AddOns aus config.addons in Reihenfolge bauen + registrieren
        5. runner.connect()
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        self._config_path = str(config_path) if config_path else None
        self._extra_factories: dict[str, Callable] = {}
        self._config: AgentConfig | None = None

    def register_addon_factory(self, name: str, factory: Callable) -> None:
        """Eigene AddOn-Factory registrieren — überschreibt Default."""
        self._extra_factories[name] = factory

    async def build(self) -> Runner:
        """Runner bauen und starten."""
        reset_config()  # Singleton zurücksetzen damit config_path greift
        self._config = get_config(self._config_path)
        runner = self._build_runner()
        self._register_addons(runner)
        await runner.connect()
        logger.info(
            f"[HeinzelLoader] '{runner._name}' gestartet — "
            f"{len(runner._addons)} AddOn(s)"
        )
        return runner

    # -------------------------------------------------------------------------
    # Interna
    # -------------------------------------------------------------------------

    def _build_runner(self) -> Runner:
        cfg = self._config
        provider = _build_provider(cfg)
        return Runner(
            provider=provider,
            name=cfg.agent.name,
            agent_id=cfg.agent.id,
        )

    def _register_addons(self, runner: Runner) -> None:
        addons_cfg: dict = self._config.addons
        factories = {**_DEFAULT_FACTORIES, **self._extra_factories}

        ordered = _ADDON_ORDER + [k for k in addons_cfg if k not in _ADDON_ORDER]
        registered: dict[str, Any] = {}

        for addon_name in ordered:
            if addon_name not in addons_cfg:
                continue
            cfg = addons_cfg[addon_name] or {}

            factory = factories.get(addon_name)
            if factory is None:
                logger.warning(f"[HeinzelLoader] Keine Factory für '{addon_name}' — übersprungen")
                continue

            try:
                addon = factory(cfg, self._config)
            except Exception as exc:
                logger.error(f"[HeinzelLoader] Fehler beim Bauen von '{addon_name}': {exc}")
                raise

            registered[addon_name] = addon
            hooks = _HOOKS.get(addon_name, {HookPoint.ON_SESSION_START})
            runner.register_addon(addon, hooks=hooks)
            logger.debug(f"[HeinzelLoader] '{addon_name}' registriert ({len(hooks)} Hooks)")

        # MattermostAddOn braucht runner-Referenz
        if "mattermost" in registered:
            registered["mattermost"]._runner = runner


# =============================================================================
# Provider-Builder
# =============================================================================


def _build_provider(config: AgentConfig) -> Any:
    default_name = config.provider.default
    providers = config.providers

    if default_name and default_name in providers:
        entry = providers[default_name]
        try:
            from core.provider import HttpLLMProvider
            logger.info(f"[HeinzelLoader] Provider: '{default_name}' → {entry.url} ({entry.name})")
            return HttpLLMProvider(name=default_name, base_url=entry.url, model=entry.name)
        except Exception as exc:
            logger.warning(f"[HeinzelLoader] HttpLLMProvider Fehler: {exc} — Noop-Fallback")

    from core.provider import NoopProvider
    logger.warning("[HeinzelLoader] Kein Provider konfiguriert — NoopProvider")
    return NoopProvider()
