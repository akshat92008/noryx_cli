"""
MCP Tool Registry — handles integration of dynamically discovered MCP tools
with the agent's central tool dispatch system.
"""

from nexus.mcp.client import MCPClient


class MCPToolRegistry:
    """
    Integrates dynamic MCP tools into the main agent tool registry.

    Allows the agent to view and execute MCP tools just like built-in tools.
    """

    def __init__(self, mcp_client: MCPClient):
        self.mcp_client = mcp_client

    def get_tool_definitions(self) -> list[dict]:
        """Get all registered MCP tools as OpenAI schemas."""
        return self.mcp_client.get_all_tool_definitions()

    def handle_tool_call(self, name: str, arguments: dict) -> str:
        """Dispatch a tool execution to the correct MCP connection."""
        return self.mcp_client.call_tool(name, arguments)

    def is_mcp_tool(self, name: str) -> bool:
        """Determine if a tool name belongs to an MCP server."""
        return self.mcp_client.is_mcp_tool(name)
