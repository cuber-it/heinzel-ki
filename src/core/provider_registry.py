"""ProviderRegistry — verwaltet alle konfigurierten LLM-Provider-Services.

Laedt Provider-Konfiguration aus providers.yaml, pingt alle beim Start,
verwaltet den aktiven Provider und ermoeglicht Fallback + Reload.

Config-Suchreihenfolge:
  1. Explizit per Konstruktor-Argument
  2. Env-Variable HEINZEL_PROVIDERS_CONFIG
  3. ./providers.yaml (CWD)

Format providers.yaml:
  providers:
    - name: openai
      url: http://thebrain:12101
      model: ""          # optional
      timeout: 120.0     # optional
    - name: anthropic
      url: http://thebrain:12102
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import yaml

from .exceptions import ConfigError, ProviderError
from .provider import HttpLLMProvider

logger = logging.getLogger(__name__)

_ENV_VAR = "HEINZEL_PROVIDERS_CONFIG"
_DEFAULT_PATH = Path("providers.yaml")


class ProviderRegistry:
    """Verwaltet alle konfigurierten LLM-Provider-Services.

    Beim Start: config laden + alle Provider pingen.
    Im Betrieb: aktiven Provider liefern, wechseln, Fallback, Reload.

    Verwendung:
        registry = ProviderRegistry()           # sucht providers.yaml
        await registry.startup()               # laden + alle pingen
        provider = registry.get_active()       # aktuell aktiver Provider
        ok = await registry.switch_to("anthropic")
        await registry.reload_config()         # hot-reload
    """

    def __init__(self, config_path: str | None = None) -> None:
        self._config_path: Path = self._resolve_config_path(config_path)
        self._providers: list[HttpLLMProvider] = []
        self._active: HttpLLMProvider | None = None
        self._health_status: dict[str, bool] = {}

    # -------------------------------------------------------------------------
    # Properties
    # -------------------------------------------------------------------------

    @property
    def providers(self) -> list[HttpLLMProvider]:
        """Alle konfigurierten Provider (unabhaengig vom Health-Status)."""
        return list(self._providers)

    @property
    def active(self) -> HttpLLMProvider | None:
        """Aktuell aktiver Provider — None wenn keiner verfuegbar."""
        return self._active

    @property
    def health_status(self) -> dict[str, bool]:
        """Letzter bekannter Health-Status je Provider-Name."""
        return dict(self._health_status)

    @property
    def config_path(self) -> Path:
        return self._config_path

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    async def startup(self) -> None:
        """Config laden + alle Provider pingen + ersten healthy aktivieren.

        Raises ConfigError wenn keine providers.yaml gefunden.
        Raises ProviderError wenn kein einziger Provider healthy ist.
        """
        self.load_config()
        await self.check_all()
        self._activate_first_healthy()

    async def reload_config(self) -> None:
        """Config neu einlesen, alle pingen, aktiven neu setzen.

        Behaelt aktiven Provider falls er weiterhin healthy ist.
        """
        previous_name = self._active.name if self._active else None
        self.load_config()
        await self.check_all()

        # Frueheren aktiven Provider wiederherstellen wenn moeglich
        if previous_name:
            prev = self._find_by_name(previous_name)
            if prev and self._health_status.get(previous_name):
                self._active = prev
                logger.info("Reload: aktiver Provider '%s' beibehalten", previous_name)
                return

        self._activate_first_healthy()

    # -------------------------------------------------------------------------
    # Config
    # -------------------------------------------------------------------------

    def load_config(self) -> None:
        """Laedt providers.yaml und baut Provider-Liste auf.

        Raises ConfigError bei fehlender oder fehlerhafter Datei.
        """
        if not self._config_path.exists():
            raise ConfigError(
                f"providers.yaml nicht gefunden: {self._config_path}",
                config_path=str(self._config_path),
            )

        try:
            with open(self._config_path, encoding="utf-8") as f:
                raw: dict[str, Any] = yaml.safe_load(f) or {}
        except Exception as exc:
            raise ConfigError(
                f"providers.yaml konnte nicht gelesen werden: {exc}",
                config_path=str(self._config_path),
            ) from exc

        entries = raw.get("providers", [])
        if not entries:
            raise ConfigError(
                "providers.yaml enthaelt keine Provider-Eintraege",
                config_path=str(self._config_path),
            )

        self._providers = []
        for entry in entries:
            name = entry.get("name", "")
            url = entry.get("url", "")
            if not name or not url:
                logger.warning("Provider-Eintrag ohne name/url uebersprungen: %s", entry)
                continue
            self._providers.append(
                HttpLLMProvider(
                    name=name,
                    base_url=url,
                    model=entry.get("model", ""),
                    timeout=float(entry.get("timeout", 120.0)),
                )
            )

        logger.info(
            "ProviderRegistry: %d Provider geladen aus %s",
            len(self._providers),
            self._config_path,
        )

    # -------------------------------------------------------------------------
    # Health
    # -------------------------------------------------------------------------

    async def check_all(self) -> dict[str, bool]:
        """Pingt alle konfigurierten Provider. Gibt Status-Dict zurueck."""
        results: dict[str, bool] = {}
        for provider in self._providers:
            ok = await provider.health()
            results[provider.name] = ok
            logger.info("Provider '%s' health: %s", provider.name, "OK" if ok else "FAIL")
        self._health_status = results
        return results

    # -------------------------------------------------------------------------
    # Aktiver Provider
    # -------------------------------------------------------------------------

    def get_active(self) -> HttpLLMProvider:
        """Gibt aktiven Provider zurueck.

        Raises ProviderError wenn kein Provider aktiv ist.
        """
        if self._active is None:
            raise ProviderError("Kein aktiver Provider verfuegbar — alle unhealthy oder keine Config geladen")
        return self._active

    async def switch_to(self, name: str) -> bool:
        """Wechselt auf benannten Provider nach health-Check.

        Returns True bei Erfolg, False wenn Provider unbekannt oder unhealthy.
        """
        provider = self._find_by_name(name)
        if provider is None:
            logger.warning("switch_to('%s') fehlgeschlagen: Provider nicht bekannt", name)
            return False

        ok = await provider.health()
        self._health_status[name] = ok
        if not ok:
            logger.warning("switch_to('%s') fehlgeschlagen: Provider unhealthy", name)
            return False

        self._active = provider
        logger.info("Aktiver Provider gewechselt auf '%s'", name)
        return True

    async def fallback(self) -> HttpLLMProvider | None:
        """Sucht naechsten healthy Provider (ausser dem aktuell aktiven).

        Aktiviert ihn direkt wenn gefunden.
        Returns Provider oder None wenn keiner verfuegbar.
        """
        current_name = self._active.name if self._active else None

        for provider in self._providers:
            if provider.name == current_name:
                continue
            ok = await provider.health()
            self._health_status[provider.name] = ok
            if ok:
                logger.warning(
                    "Fallback: wechsle von '%s' auf '%s'",
                    current_name or "(keiner)",
                    provider.name,
                )
                self._active = provider
                return provider

        logger.error("Fallback fehlgeschlagen: kein healthy Provider verfuegbar")
        return None

    # -------------------------------------------------------------------------
    # Hilfsmethoden
    # -------------------------------------------------------------------------

    def _find_by_name(self, name: str) -> HttpLLMProvider | None:
        for p in self._providers:
            if p.name == name:
                return p
        return None

    def _activate_first_healthy(self) -> None:
        """Setzt den ersten healthy Provider als aktiven."""
        for provider in self._providers:
            if self._health_status.get(provider.name):
                self._active = provider
                logger.info("Aktiver Provider: '%s'", provider.name)
                return

        self._active = None
        logger.error("Kein healthy Provider gefunden — _active ist None")

    @staticmethod
    def _resolve_config_path(config_path: str | None) -> Path:
        """Config-Pfad nach Prioritaet aufloesen."""
        if config_path is not None:
            return Path(config_path)
        env = os.environ.get(_ENV_VAR)
        if env:
            return Path(env)
        return _DEFAULT_PATH
