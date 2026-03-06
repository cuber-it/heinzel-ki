"""Persistenz-Abstraktion für Prompts.

YamlPromptRepository ist die aktuelle Implementierung.
DbPromptRepository folgt in einem späteren MVP — Interface bleibt gleich.

YAML-Format:
    name: my-prompt
    context: system          # system | user | few-shot | suffix
    variables:
      key: default_value
    template: |
      Jinja2-Template-Text...
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

if TYPE_CHECKING:
    from core.addon_extension import PromptBase


# =============================================================================
# PromptRepository — Interface (austauschbar gegen DB-Impl)
# =============================================================================


class PromptRepository(ABC):
    """Abstrakte Persistenz-Schicht für Prompts.

    Implementierungen: YamlPromptRepository (jetzt), DbPromptRepository (später).
    """

    @abstractmethod
    def load_all(self) -> list[dict]:
        """Alle Prompt-Definitionen laden. Gibt rohe Dicts zurück."""
        ...

    @abstractmethod
    def load_one(self, name: str) -> dict | None:
        """Einzelnen Prompt nach Name laden. None wenn nicht gefunden."""
        ...

    @abstractmethod
    def save(self, name: str, data: dict) -> None:
        """Prompt-Definition speichern (für mutate-Unterstützung)."""
        ...

    @abstractmethod
    def exists(self, name: str) -> bool:
        """Prüft ob ein Prompt mit diesem Namen existiert."""
        ...

    @abstractmethod
    def list_names(self) -> list[str]:
        """Namen aller verfügbaren Prompts."""
        ...


# =============================================================================
# YamlPromptRepository
# =============================================================================


class YamlPromptRepository(PromptRepository):
    """Lädt und speichert Prompts als YAML-Dateien in einem Verzeichnis.

    Eine Datei pro Prompt, Dateiname = prompt-name.yaml
    Verzeichnis wird beim ersten Zugriff erstellt falls nicht vorhanden.
    """

    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, name: str) -> Path:
        return self._dir / f"{name}.yaml"

    def load_all(self) -> list[dict]:
        """Alle .yaml-Dateien im Verzeichnis einlesen."""
        result = []
        for path in sorted(self._dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    # Name aus Dateiname ableiten wenn nicht in YAML
                    data.setdefault("name", path.stem)
                    result.append(data)
            except yaml.YAMLError:
                # Fehlerhafte Datei überspringen — wird geloggt vom AddOn
                pass
        return result

    def load_one(self, name: str) -> dict | None:
        path = self._path_for(name)
        if not path.exists():
            return None
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                data.setdefault("name", name)
                return data
        except yaml.YAMLError:
            return None
        return None

    def save(self, name: str, data: dict) -> None:
        """YAML-Datei schreiben. Überschreibt bestehende Datei."""
        path = self._path_for(name)
        path.write_text(
            yaml.dump(data, allow_unicode=True, default_flow_style=False),
            encoding="utf-8",
        )

    def exists(self, name: str) -> bool:
        return self._path_for(name).exists()

    def list_names(self) -> list[str]:
        return [p.stem for p in sorted(self._dir.glob("*.yaml"))]

    @property
    def directory(self) -> Path:
        return self._dir
