"""MCP Datenmodelle — ToolAddress, KnownTool, ToolCall, ToolResult.

Adressierungskonvention: target:server:tool
    target  — DNS-Name oder IP des Zielhosts (z.B. 'thebrain', '192.168.1.5')
    server  — MCP-Server-Name wie registriert (z.B. 'shell-tools')
    tool    — Tool-Name auf dem Server (z.B. 'cd', 'file_read')

    Beispiel: 'thebrain:shell-tools:cd'

Importpfad:
    from core.mcp import ToolAddress, KnownTool, ToolCall, ToolResult
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ToolAddress(BaseModel, frozen=True):
    """Aufgeloeste Tool-Adresse nach dem Schema target:server:tool."""

    target: str   # DNS-Name oder IP
    server: str   # MCP-Server-Name
    tool: str     # Tool-Name

    @staticmethod
    def parse(address: str) -> "ToolAddress":
        """Parst einen 'target:server:tool' String.

        Args:
            address: z.B. 'thebrain:shell-tools:cd'

        Raises:
            ValueError: Wenn Format nicht stimmt
        """
        parts = address.split(":")
        if len(parts) != 3:
            raise ValueError(
                f"Ungueltige Tool-Adresse '{address}' — "
                f"Format: target:server:tool (z.B. thebrain:shell-tools:cd)"
            )
        target, server, tool = parts
        if not all([target, server, tool]):
            raise ValueError(
                f"Leere Segmente in Tool-Adresse '{address}'"
            )
        return ToolAddress(target=target, server=server, tool=tool)

    def __str__(self) -> str:
        return f"{self.target}:{self.server}:{self.tool}"


class KnownTool(BaseModel, frozen=True):
    """Ein dem Router bekanntes Tool mit Verbindungsinfos.

    Wird vom MCPDiscovererAddOn registriert (kommt in HNZ-004).
    Enthaelt alles was der Router braucht um den Call auszufuehren.
    """

    address: ToolAddress           # Vollstaendige Adresse
    endpoint_url: str              # HTTP-Endpunkt des MCP-Servers
    description: str = ""
    input_schema: dict[str, Any] = {}


class ToolCall(BaseModel, frozen=True):
    """Ein eingehender Tool-Aufruf an den Router.

    address ist der vollstaendige 'target:server:tool' String.
    context enthaelt Output des Vorgaengers bei chain()-Aufrufen.
    """

    address: str
    args: dict[str, Any] = {}
    context: dict[str, Any] = {}

    def parsed_address(self) -> ToolAddress:
        return ToolAddress.parse(self.address)


class ToolResult(BaseModel, frozen=True):
    """Ergebnis eines Tool-Aufrufs.

    unknown=True: Router kennt dieses Tool nicht.
    Aufrufer sollte dann MCPDiscovererAddOn befragen.
    """

    address: str
    result: Any = None
    error: str | None = None
    duration_ms: int = 0
    unknown: bool = False



# =============================================================================
# Approval System
# =============================================================================

import enum


class ApprovalPolicy(str, enum.Enum):
    """Approval-Policy fuer einen Tool-Aufruf.

    Wird pro Tool oder als Server-Default gesetzt.
    LLM und Commands koennen Policies zur Laufzeit aendern.
    """

    ALWAYS_ALLOW = "always_allow"   # Nie fragen — immer ausfuehren
    ALWAYS_DENY  = "always_deny"    # Nie ausfuehren
    ASK_ONCE     = "ask_once"       # Einmal fragen, Antwort fuer Session merken
    ASK_ALWAYS   = "ask_always"     # Jedes Mal fragen


_DEFAULT_KEY = "_default"           # Dict-Key fuer Server-weiten Fallback


class ServerEntry(BaseModel):
    """Registry-Eintrag fuer einen MCP-Server mit Approval-Regeln.

    approval ist ein flaches Dict:
        '_default' -> Policy fuer alle Tools ohne expliziten Eintrag
        'cd'       -> Policy speziell fuer das Tool 'cd'
        'file_delete' -> Policy speziell fuer 'file_delete'

    Hochdynamisch — LLM und Commands aendern approval zur Laufzeit.
    Deshalb NICHT frozen.
    """

    target: str                         # DNS-Name oder IP
    server: str                         # MCP-Server-Name
    endpoint_url: str
    approval: dict[str, ApprovalPolicy] = {}  # tool_name -> Policy

    def get_policy(self, tool: str) -> ApprovalPolicy:
        """Gibt die Policy fuer ein Tool zurueck.

        Reihenfolge: tool-spezifisch -> _default -> ASK_ALWAYS
        """
        return (
            self.approval.get(tool)
            or self.approval.get(_DEFAULT_KEY)
            or ApprovalPolicy.ASK_ALWAYS
        )

    def set_policy(self, policy: ApprovalPolicy, tool: str | None = None) -> None:
        """Setzt eine Policy.

        Args:
            policy: Die neue Policy
            tool:   Tool-Name oder None fuer Server-Default
        """
        key = tool if tool is not None else _DEFAULT_KEY
        self.approval[key] = policy

    @property
    def key(self) -> str:
        """Eindeutiger Schluessel: 'target:server'."""
        return f"{self.target}:{self.server}"


__all__ = [
    "ToolAddress",
    "KnownTool",
    "ToolCall",
    "ToolResult",
    "ApprovalPolicy",
    "ServerEntry",
]
