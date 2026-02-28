"""
H.E.I.N.Z.E.L. Provider — Instanz-Konfiguration

Ladereihenfolge (hoeher = hoehere Prioritaet):
  1. Default-Werte
  2. config/instance.yaml  (gitignored, enthaelt Secrets)
  3. Umgebungsvariablen    (ueberschreiben alles)

Verwendung:
  from config import instance_config
  api_key = instance_config.api_key("ANTHROPIC_API_KEY")
"""
import os
import sys
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None


class InstanceConfig:
    """
    Laedt instance.yaml und stellt Werte mit ENV-Override bereit.
    Ist instance.yaml nicht vorhanden, arbeitet die Klasse mit
    Defaults und Env-Vars -- kein Fehler, kein Absturz.
    """

    def __init__(self, config_path: Optional[str] = None):
        self._data: dict = {}
        path = config_path or os.environ.get("INSTANCE_CONFIG", "/config/instance.yaml")
        self._load(path)

    def _load(self, path: str):
        if not os.path.exists(path):
            print(f"InstanceConfig: {path} nicht gefunden, nutze Env-Vars/Defaults",
                  file=sys.stderr)
            return
        if yaml is None:
            print("InstanceConfig: pyyaml nicht installiert, nutze Env-Vars/Defaults",
                  file=sys.stderr)
            return
        try:
            with open(path) as f:
                self._data = yaml.safe_load(f) or {}
            print(f"InstanceConfig: geladen aus {path}", file=sys.stderr)
        except Exception as e:
            print(f"InstanceConfig: Fehler beim Laden ({e}), nutze Defaults",
                  file=sys.stderr)

    def api_key(self, env_var: str, default: str = "") -> str:
        """ENV > instance.yaml[api_key] > default"""
        return (
            os.environ.get(env_var)
            or self._data.get("api_key", "")
            or default
        )

    def log_requests(self) -> bool:
        """Dialog-Logging aktiviert? ENV > YAML > True"""
        env = os.environ.get("LOG_REQUESTS", "").strip().lower()
        if env in ("false", "0", "no"):
            return False
        if env in ("true", "1", "yes"):
            return True
        val = self._data.get("log_requests")
        if val is not None:
            return bool(val)
        return True

    def database_url(self, default_data_dir: str = "/data") -> str:
        """ENV > YAML > SQLite-Default."""
        url = (
            os.environ.get("DATABASE_URL")
            or self._data.get("database", {}).get("url", "")
            or f"sqlite:///{default_data_dir}/costs.db"
        )
        return _normalize_sqlite_url(url, default_data_dir)

    def retention(self) -> dict:
        """Retention-Policy fuer Logs und Metriken."""
        defaults = {
            "log_max_age_days": 30,
            "log_max_size_mb": 500,
            "log_compress": True,
            "metrics_max_age_days": 90,
        }
        yaml_val = self._data.get("retention") or {}
        return {**defaults, **yaml_val}


def _normalize_sqlite_url(url: str, data_dir: str = "/data") -> str:
    """Macht relative sqlite-Pfade absolut."""
    if not url.startswith("sqlite:///"):
        return url
    rel = url[len("sqlite:///"):]
    if rel.startswith("/"):
        return url
    return f"sqlite:///{data_dir}/{rel.lstrip('data/').lstrip('/')}"


# Singleton
instance_config = InstanceConfig()
