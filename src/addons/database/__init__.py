"""DatabaseAddOn — zentraler DB-Zugriff für Heinzel."""

from .base import DatabaseAddOn, SCHEMA_SQL
from .sqlite import SQLiteAddOn
from .postgres import PostgreSQLAddOn

__all__ = [
    "DatabaseAddOn",
    "SQLiteAddOn",
    "PostgreSQLAddOn",
    "SCHEMA_SQL",
]
