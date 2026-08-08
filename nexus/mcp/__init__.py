"""
MCP (Model Context Protocol) Integration — connects to external MCP servers
for dynamic tool discovery and execution.

Supports stdio and SSE transports for communication with MCP servers
like GitHub, Filesystem, SQLite, and custom servers.
"""

from nexus.mcp.client import MCPClient, MCPConnection
from nexus.mcp.registry import MCPToolRegistry

__all__ = ["MCPClient", "MCPConnection", "MCPToolRegistry"]
