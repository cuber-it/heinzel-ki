"""MCPToolsRouter — AddOn fuer Tool-Routing via MCP.

Zwei getrennte Registries:
    _tools:   dict[address_str, KnownTool]   — Discovery: kenne ich das Tool?
    _servers: dict[server_key, ServerEntry]  — Approval: darf ich es aufrufen?

Adressierungskonvention: target:server:tool
    Beispiel: thebrain:shell-tools:cd

Approval-Flow:
    ALWAYS_ALLOW -> direkt ausfuehren
    ALWAYS_DENY  -> ablehnen
    ASK_ONCE     -> Session-Cache pruefen, sonst approval_pending in metadata
    ASK_ALWAYS   -> approval_pending in metadata

Austauschpunkt HNZ-004:
    _execute() ueberschreiben mit echtem MCP SDK Call.

Importpfad:
    from addons.mcp_router import MCPToolsRouter, NoopMCPToolsRouter
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from core.addon import AddOn
from core.models import AddOnResult, ContextHistory, PipelineContext
from core.models.base import ToolCall as PipelineToolCall, ToolResult as PipelineToolResult
from .models import ApprovalPolicy, KnownTool, ServerEntry, ToolAddress, ToolCall, ToolResult


class MCPToolsRouter(AddOn):
    """Abstrakte Basisklasse fuer MCP Tool-Routing.

    Tool-Discovery via _tools-Registry.
    Approval-Management via _servers-Registry.
    """

    name = "mcp_tools_router"
    version = "0.1.0"

    def __init__(self) -> None:
        super().__init__()
        self._tools: dict[str, KnownTool] = {}       # address_str -> KnownTool
        self._servers: dict[str, ServerEntry] = {}   # 'target:server' -> ServerEntry
        self._ask_once_cache: dict[str, bool] = {}   # address_str -> bool (Session)

    # -------------------------------------------------------------------------
    # Tool-Registry (Discovery)
    # -------------------------------------------------------------------------

    def register(self, tool: KnownTool) -> None:
        """Registriert ein bekanntes Tool. Wird vom MCPDiscovererAddOn aufgerufen (HNZ-004)."""
        self._tools[str(tool.address)] = tool

    def unregister(self, address: str) -> None:
        """Entfernt ein Tool aus der Discovery-Registry."""
        self._tools.pop(address, None)

    def list_tools(self) -> list[KnownTool]:
        """Alle bekannten Tools."""
        return list(self._tools.values())

    def find_tool(self, address: str) -> KnownTool | None:
        """Sucht ein Tool nach Adresse ('target:server:tool')."""
        return self._tools.get(address)

    # -------------------------------------------------------------------------
    # Server-Registry (Approval)
    # -------------------------------------------------------------------------

    def register_server(self, entry: ServerEntry) -> None:
        """Registriert einen Server mit seinen Approval-Regeln."""
        self._servers[entry.key] = entry

    def get_server_entry(self, target: str, server: str) -> ServerEntry | None:
        """Gibt den ServerEntry fuer target:server zurueck."""
        return self._servers.get(f"{target}:{server}")

    def list_servers(self) -> list[ServerEntry]:
        """Alle registrierten Server mit ihren Approval-Regeln."""
        return list(self._servers.values())

    def set_approval(
        self,
        target: str,
        server: str,
        policy: ApprovalPolicy,
        tool: str | None = None,
    ) -> None:
        """Setzt eine Approval-Policy — tool=None bedeutet Server-Default.

        Erstellt einen ServerEntry wenn noch keiner existiert.
        Aufrufbar durch LLM (via Tool-Call) und Commands.

        Args:
            target: DNS-Name oder IP
            server: MCP-Server-Name
            policy: Neue Policy
            tool:   Tool-Name oder None fuer Server-Default
        """
        key = f"{target}:{server}"
        if key not in self._servers:
            self._servers[key] = ServerEntry(
                target=target,
                server=server,
                endpoint_url="",  # Wird beim echten Register gesetzt
            )
        self._servers[key].set_policy(policy, tool)
        # ASK_ONCE Cache invalidieren wenn Policy geaendert wird
        if tool is not None:
            self._ask_once_cache.pop(f"{target}:{server}:{tool}", None)

    def get_approval(
        self,
        target: str,
        server: str,
        tool: str,
    ) -> ApprovalPolicy:
        """Gibt die aktuelle Approval-Policy fuer ein Tool zurueck.

        Reihenfolge: tool-spezifisch -> server-default -> ASK_ALWAYS
        Aufrufbar durch LLM (via Tool-Call) und Commands.
        """
        entry = self._servers.get(f"{target}:{server}")
        if entry is None:
            return ApprovalPolicy.ASK_ALWAYS
        return entry.get_policy(tool)

    def clear_ask_once_cache(self) -> None:
        """Loescht den ASK_ONCE Session-Cache (z.B. bei Session-Ende)."""
        self._ask_once_cache.clear()

    # -------------------------------------------------------------------------
    # Approval-Entscheidung
    # -------------------------------------------------------------------------

    async def _resolve_approval(
        self, address: str, args: dict[str, Any]
    ) -> tuple[bool, bool]:
        """Wertet die Approval-Policy aus.

        Returns:
            (approved, pending) —
                approved=True  -> ausfuehren
                approved=False -> ablehnen
                pending=True   -> User-Rueckfrage noetig (ASK_ONCE/ASK_ALWAYS)
        """
        try:
            addr = ToolAddress.parse(address)
        except ValueError:
            return False, False

        policy = self.get_approval(addr.target, addr.server, addr.tool)

        if policy == ApprovalPolicy.ALWAYS_ALLOW:
            return True, False

        if policy == ApprovalPolicy.ALWAYS_DENY:
            return False, False

        if policy == ApprovalPolicy.ASK_ONCE:
            if address in self._ask_once_cache:
                return self._ask_once_cache[address], False
            # Noch nicht gefragt — pending
            return False, True

        # ASK_ALWAYS
        return False, True

    def record_ask_once_answer(self, address: str, approved: bool) -> None:
        """Speichert die User-Antwort fuer ASK_ONCE in der Session.

        Wird aufgerufen wenn der User auf eine Approval-Anfrage antwortet.
        """
        self._ask_once_cache[address] = approved

    # -------------------------------------------------------------------------
    # Ausfuehrung
    # -------------------------------------------------------------------------

    @abstractmethod
    async def _execute(self, tool: KnownTool, args: dict[str, Any]) -> ToolResult:
        """Fuehrt einen Tool-Call via MCP SDK aus.

        Austauschpunkt HNZ-004: echten MCP SDK Call implementieren.
        Wird nur aufgerufen wenn Tool bekannt UND Approval erteilt.
        """
        ...

    async def call(self, address: str, args: dict[str, Any]) -> ToolResult:
        """Fuehrt einen einzelnen Tool-Call aus.

        Unbekannt  -> ToolResult(unknown=True)
        Abgelehnt  -> ToolResult(error='abgelehnt')
        Pending    -> ToolResult(error='approval_pending')
        """
        tool = self.find_tool(address)
        if tool is None:
            return ToolResult(address=address, unknown=True)

        approved, pending = await self._resolve_approval(address, args)

        if pending:
            return ToolResult(address=address, error="approval_pending")
        if not approved:
            return ToolResult(address=address, error="abgelehnt")

        return await self._execute(tool, args)

    async def chain(self, calls: list[ToolCall]) -> list[ToolResult]:
        """Fuehrt mehrere Tool-Calls sequenziell aus.

        Output von call[n] fliesst als prev_result in call[n+1].
        """
        results: list[ToolResult] = []
        prev_context: dict[str, Any] = {}

        for tc in calls:
            merged_args = {**tc.args, **tc.context, **prev_context}
            result = await self.call(tc.address, merged_args)
            results.append(result)
            prev_context = {"prev_result": result.result} if result.result is not None else {}

        return results

    # -------------------------------------------------------------------------
    # AddOn Hook
    # -------------------------------------------------------------------------

    async def on_tool_request(self, ctx: PipelineContext, history: ContextHistory | None = None) -> AddOnResult:
        """Verarbeitet alle offenen Tool-Requests im PipelineContext.

        - Bekannte Tools + approved:  ausfuehren -> ctx.tool_results
        - Unbekannte Tools:           -> ctx.metadata['unknown_tool_requests']
        - Approval pending:           -> ctx.metadata['approval_pending']
        - Abgelehnt:                  -> ToolResult(error='abgelehnt')
        """
        if not ctx.tool_requests:
            return AddOnResult(modified_ctx=ctx)

        new_results: list[PipelineToolResult] = list(ctx.tool_results)
        new_unknown: list[str] = list(ctx.metadata.get("unknown_tool_requests", []))
        new_pending: list[str] = list(ctx.metadata.get("approval_pending", []))

        for pipeline_call in ctx.tool_requests:
            mcp_result = await self.call(pipeline_call.tool_name, pipeline_call.args)

            if mcp_result.unknown:
                new_unknown.append(pipeline_call.tool_name)
            elif mcp_result.error == "approval_pending":
                new_pending.append(pipeline_call.tool_name)
            elif mcp_result.error:
                new_results.append(PipelineToolResult(
                    call_id=pipeline_call.call_id,
                    error=mcp_result.error,
                ))
            else:
                new_results.append(PipelineToolResult(
                    call_id=pipeline_call.call_id,
                    result=mcp_result.result,
                    duration_ms=mcp_result.duration_ms,
                ))

        new_metadata = {
            **ctx.metadata,
            "unknown_tool_requests": new_unknown,
            "approval_pending": new_pending,
        }
        new_ctx = ctx.model_copy(update={
            "tool_results": tuple(new_results),
            "metadata": new_metadata,
        })
        return AddOnResult(modified_ctx=new_ctx)


class NoopMCPToolsRouter(MCPToolsRouter):
    """Noop-Implementierung.

    Kennt 0 Tools -> alles unknown.
    _execute() wird nie erreicht.

    Austauschpunkt HNZ-004:
        NoopMCPToolsRouter -> echter MCPToolsRouter mit _execute().
    """

    name = "mcp_tools_router"
    version = "0.1.0"

    async def _execute(self, tool: KnownTool, args: dict[str, Any]) -> ToolResult:
        """Wird im Noop nie erreicht — Registry ist immer leer."""
        return ToolResult(address=str(tool.address), error="MCP not configured")


__all__ = [
    "MCPToolsRouter",
    "NoopMCPToolsRouter",
]
