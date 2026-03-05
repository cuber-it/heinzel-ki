"""HeinzelLoader — Startup-Loader: Config → Runner.

Liest heinzel.yaml, baut AddOns, verdrahtet Hooks, gibt fertigen Runner zurück.

Verwendung:
    from core.startup import HeinzelLoader

    loader = HeinzelLoader("config/heinzel.yaml")
    runner = await loader.build()
    await runner.connect()
    # runner ist bereit

Eigene AddOns registrieren:
    loader.register_addon_factory("my_addon", my_factory_fn)

Config-Struktur (heinzel.yaml):
    heinzel:
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
        backend: sqlite          # oder: postgres
        path: data/heinzel.db
        # dsn: postgresql://...  # für postgres
      dialog_logger:
        log_dir: logs/dialogs
        rotation_size_mb: 10
        retention_days: 90
      prompt:
        directory: prompts/
      prompt_builder: {}
      skills:
        directory: skills/
        active: []               # leer = alle
      skill_loader: {}
      web_search:
        backend: duckduckgo
        max_results: 5
        backends:
          searxng:
            url: http://services:12004
          duckduckgo: {}
        targets: {}
      mcp_tools_router: {}
      mattermost:
        url: http://services:8065
        token: "${MATTERMOST_TOKEN}"   # ENV-Substitution
        channel: heinzel-general
        mention_only: true
        reply_in_thread: true

AddOn-Reihenfolge (dependency-aware):
    database → dialog_logger → prompt → prompt_builder
    → skills → skill_loader → web_search → mcp_tools_router → mattermost
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any, Callable

import yaml

from core.models import HookPoint
from core.runner import Runner

logger = logging.getLogger(__name__)

# =============================================================================
# Hook-Sets pro AddOn-Typ
# =============================================================================

_HOOKS: dict[str, set[HookPoint]] = {
    "database": set(),  # kein eigener Hook — wird von anderen geholt
    "dialog_logger": {
        HookPoint.ON_INPUT,
        HookPoint.ON_OUTPUT,
        HookPoint.ON_THINKING_STEP,
        HookPoint.ON_TOOL_REQUEST,
        HookPoint.ON_TOOL_RESULT,
        HookPoint.ON_ERROR,
    },
    "prompt": {
        HookPoint.ON_CONTEXT_BUILD,
    },
    "prompt_builder": {
        HookPoint.ON_CONTEXT_READY,
    },
    "skills": set(),
    "skill_loader": {
        HookPoint.ON_CONTEXT_BUILD,
    },
    "web_search": {
        HookPoint.ON_CONTEXT_BUILD,
    },
    "mcp_tools_router": {
        HookPoint.ON_TOOL_REQUEST,
    },
    "mattermost": set(),  # Background-Task via on_attach
}

# Reihenfolge — dependencies zuerst
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


def _build_database(cfg: dict, runner_cfg: dict) -> Any:
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


def _build_dialog_logger(cfg: dict, runner_cfg: dict) -> Any:
    from addons.dialog_logger import DialogLoggerAddOn
    return DialogLoggerAddOn(
        log_dir=cfg.get("log_dir", "logs/dialogs"),
        rotation_size_mb=cfg.get("rotation_size_mb", 10.0),
        retention_days=cfg.get("retention_days", 90),
    )


def _build_prompt(cfg: dict, runner_cfg: dict) -> Any:
    from addons.prompt import PromptAddOn
    return PromptAddOn(directory=cfg.get("directory", "prompts/"))


def _build_prompt_builder(cfg: dict, runner_cfg: dict) -> Any:
    from addons.prompt_builder import PromptBuilderAddOn
    return PromptBuilderAddOn()


def _build_skills(cfg: dict, runner_cfg: dict) -> Any:
    from addons.skills import SkillsAddOn
    return SkillsAddOn(
        directory=cfg.get("directory", "skills/"),
        active=cfg.get("active", []),
    )


def _build_skill_loader(cfg: dict, runner_cfg: dict) -> Any:
    from addons.skills import SkillLoaderAddOn
    return SkillLoaderAddOn()


def _build_web_search(cfg: dict, runner_cfg: dict) -> Any:
    from addons.web_search import WebSearchAddOn
    return WebSearchAddOn(
        backend_name=cfg.get("backend", "duckduckgo"),
        max_results=cfg.get("max_results", 5),
        backends_config=cfg.get("backends", {}),
        targets=cfg.get("targets", {}),
    )


def _build_mcp_tools_router(cfg: dict, runner_cfg: dict) -> Any:
    from addons.mcp_router import NoopMCPToolsRouter
    return NoopMCPToolsRouter()


def _build_mattermost(cfg: dict, runner_cfg: dict) -> Any:
    from addons.mattermost import MattermostAddOn
    return MattermostAddOn(
        url=cfg["url"],
        token=_resolve_env(cfg["token"]),
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

    Ablauf:
        1. YAML lesen + ENV-Substitution
        2. Provider bauen
        3. Runner instanziieren
        4. AddOns in Reihenfolge bauen + registrieren
        5. runner.connect() aufrufen
        6. Fertigen Runner zurückgeben
    """

    def __init__(self, config_path: str | Path | None = None) -> None:
        self._config_path = Path(config_path) if config_path else _find_config()
        self._extra_factories: dict[str, Callable] = {}
        self._raw: dict = {}

    def register_addon_factory(self, name: str, factory: Callable) -> None:
        """Eigene AddOn-Factory registrieren — überschreibt Default."""
        self._extra_factories[name] = factory

    async def build(self) -> Runner:
        """Runner bauen und starten. Gibt verbundenen Runner zurück."""
        self._raw = self._load_yaml()
        runner = self._build_runner()
        self._register_addons(runner)
        await runner.connect()
        logger.info(
            f"[HeinzelLoader] Runner '{runner._name}' gestartet — "
            f"{len(runner._addons)} AddOn(s)"
        )
        return runner

    # -------------------------------------------------------------------------
    # Interna
    # -------------------------------------------------------------------------

    def _load_yaml(self) -> dict:
        if self._config_path and self._config_path.exists():
            raw = yaml.safe_load(self._config_path.read_text(encoding="utf-8")) or {}
            logger.info(f"[HeinzelLoader] Config geladen: {self._config_path}")
        else:
            logger.warning("[HeinzelLoader] Keine Config-Datei — verwende Defaults")
            raw = {}
        return _substitute_env(raw)

    def _build_runner(self) -> Runner:
        provider_cfg = self._raw.get("provider", {})
        heinzel_cfg = self._raw.get("heinzel", self._raw.get("agent", {}))

        provider = _build_provider(provider_cfg)
        return Runner(
            provider=provider,
            name=heinzel_cfg.get("name", "heinzel"),
            agent_id=heinzel_cfg.get("id"),
        )

    def _register_addons(self, runner: Runner) -> None:
        addons_cfg: dict = self._raw.get("addons", {})
        factories = {**_DEFAULT_FACTORIES, **self._extra_factories}

        # Reihenfolge: bekannte zuerst, dann unbekannte in Config-Reihenfolge
        ordered = _ADDON_ORDER + [
            k for k in addons_cfg if k not in _ADDON_ORDER
        ]

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
                addon = factory(cfg, self._raw)
            except Exception as exc:
                logger.error(f"[HeinzelLoader] Fehler beim Bauen von '{addon_name}': {exc}")
                raise

            # AddOn-Referenz im AddOn-Dict für Abhängigkeiten (z.B. MattermostAddOn → runner)
            registered[addon_name] = addon

            hooks = _HOOKS.get(addon_name, set())
            if hooks:
                runner.register_addon(addon, hooks=hooks)
                logger.debug(f"[HeinzelLoader] '{addon_name}' registriert ({len(hooks)} Hooks)")
            else:
                # Kein Hook — nur Lifecycle (on_attach/on_detach)
                if addon not in runner._addons:
                    runner._addons.append(addon)
                logger.debug(f"[HeinzelLoader] '{addon_name}' als Lifecycle-AddOn eingetragen")

        # MattermostAddOn braucht runner-Referenz — nachträglich setzen
        if "mattermost" in registered:
            registered["mattermost"]._runner = runner


# =============================================================================
# Provider-Builder
# =============================================================================


def _build_provider(cfg: dict) -> Any:
    """Provider aus Config bauen.

    Versucht HttpLLMProvider, fällt auf NoopProvider zurück.
    """
    default_name = cfg.get("default", "")
    providers = cfg.get("providers", {})

    if default_name and default_name in providers:
        entry = providers[default_name]
        url = entry.get("url", "") if isinstance(entry, dict) else entry.url
        model = entry.get("name", "") if isinstance(entry, dict) else entry.name

        try:
            from core.provider import HttpLLMProvider
            logger.info(f"[HeinzelLoader] Provider: '{default_name}' → {url} ({model})")
            return HttpLLMProvider(url=url, model=model)
        except Exception as exc:
            logger.warning(f"[HeinzelLoader] HttpLLMProvider Fehler: {exc} — Noop-Fallback")

    # Fallback
    from core.provider import NoopProvider
    logger.warning("[HeinzelLoader] Kein Provider konfiguriert — NoopProvider")
    return NoopProvider()


# =============================================================================
# Hilfsfunktionen
# =============================================================================


_ENV_PATTERN = re.compile(r"\$\{(\w+)\}")


def _resolve_env(value: str) -> str:
    """${VAR} im String durch ENV-Variable ersetzen."""
    def _replace(m: re.Match) -> str:
        return os.environ.get(m.group(1), m.group(0))
    return _ENV_PATTERN.sub(_replace, value)


def _substitute_env(obj: Any) -> Any:
    """Rekursiv ${VAR} in YAML-Struktur ersetzen."""
    if isinstance(obj, str):
        return _resolve_env(obj)
    if isinstance(obj, dict):
        return {k: _substitute_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_substitute_env(i) for i in obj]
    return obj


def _find_config() -> Path | None:
    for candidate in [
        Path.cwd() / "heinzel.yaml",
        Path.cwd() / "config" / "heinzel.yaml",
        Path.home() / ".config" / "heinzel" / "heinzel.yaml",
    ]:
        if candidate.exists():
            return candidate
    return None
