"""MCPRouterAddOn — Tool-Routing, Discovery und Approval via MCP-Protokoll.

Importpfad:
    from addons.mcp_router import MCPToolsRouter, NoopMCPToolsRouter
    from addons.mcp_router import ToolAddress, KnownTool, ToolCall, ToolResult
    from addons.mcp_router import ApprovalPolicy, ServerEntry
"""

from .models import ApprovalPolicy, KnownTool, ServerEntry, ToolAddress, ToolCall, ToolResult
from .router import MCPToolsRouter, NoopMCPToolsRouter

__all__ = [
    "ApprovalPolicy",
    "KnownTool",
    "MCPToolsRouter",
    "NoopMCPToolsRouter",
    "ServerEntry",
    "ToolAddress",
    "ToolCall",
    "ToolResult",
]
