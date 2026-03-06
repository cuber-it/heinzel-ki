"""Persistenz-Abstraktion für Skills.

YAML-Format pro Skill-Datei:
    name: python-expert
    version: "1.0"
    description: "Python Code reviewen und verbessern"
    instructions: |
      Du schreibst Python nach PEP8...
    trigger_patterns:
      - python
      - code review
    tools: []
    examples:
      - user: "Schau dir diesen Code an"
        assistant: "Ich sehe folgende Probleme..."
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import yaml


# =============================================================================
# SkillRepository — Interface (analog zu PromptRepository)
# =============================================================================


class SkillRepository(ABC):
    """Abstrakte Persistenz-Schicht für Skills.

    Aktuell: YamlSkillRepository.
    Später: DbSkillRepository austauschbar über dieses Interface.
    """

    @abstractmethod
    def load_all(self) -> list[dict]:
        """Alle Skill-Definitionen laden."""
        ...

    @abstractmethod
    def load_one(self, name: str) -> dict | None:
        """Einzelnen Skill laden. None wenn nicht gefunden."""
        ...

    @abstractmethod
    def save(self, name: str, data: dict) -> None:
        """Skill-Definition speichern."""
        ...

    @abstractmethod
    def exists(self, name: str) -> bool:
        """Prüft ob ein Skill mit diesem Namen existiert."""
        ...

    @abstractmethod
    def list_names(self) -> list[str]:
        """Namen aller verfügbaren Skills."""
        ...


# =============================================================================
# YamlSkillRepository
# =============================================================================


class YamlSkillRepository(SkillRepository):
    """Lädt und speichert Skills als YAML-Dateien.

    Eine Datei pro Skill, Dateiname = skill-name.yaml
    """

    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path_for(self, name: str) -> Path:
        return self._dir / f"{name}.yaml"

    def load_all(self) -> list[dict]:
        result = []
        for path in sorted(self._dir.glob("*.yaml")):
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data.setdefault("name", path.stem)
                    result.append(data)
            except yaml.YAMLError:
                pass  # Fehlerhafte Datei überspringen
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
