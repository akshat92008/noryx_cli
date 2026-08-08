"""
MCP Client — connects to MCP servers via stdio transport.

Disccovers available tools from MCP servers and exposes them as
standard tool definitions compatible with the agent's tool system.

Security:
  - MCP subprocesses receive a minimal filtered environment
  - PYTHONPATH is NOT inherited (prevents code injection)
  - Tool call results are size-bounded
  - All tool invocations are audit-logged
"""

import json
import logging
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from nexus.paths import nexus_home
from nexus.process_io import readline_with_timeout
from nexus.runtime.process_state import ProcessStateRegistry
from nexus.sandbox import CommandSpec, SandboxRunner

logger = logging.getLogger(__name__)

# Maximum bytes from a single MCP tool call response
_MAX_MCP_OUTPUT_BYTES = 2 * 1024 * 1024  # 2 MB
_MCP_TOOL_CALL_TIMEOUT = 60.0  # seconds


@dataclass
class MCPTool:
    """A tool discovered from an MCP server."""

    name: str
    description: str
    input_schema: dict = field(default_factory=dict)
    server_name: str = ""

    def to_tool_definition(self) -> dict:
        """Convert to OpenAI-compatible tool definition."""
        return {
            "type": "function",
            "function": {
                "name": f"mcp_{self.server_name}_{self.name}",
                "description": f"[MCP:{self.server_name}] {self.description}",
                "parameters": self.input_schema,
            },
        }


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server."""

    name: str
    command: list[str]
    env: dict[str, str] = field(default_factory=dict)
    description: str = ""
    enabled: bool = True
    workspace: str = ""
    network: bool = False
    require_os_isolation: bool = True
    allow_unisolated_host_process: bool = False


class MCPConnection:
    """
    A connection to a single MCP server via stdio.

    Handles the JSON-RPC protocol for tool discovery and execution.
    """

    def __init__(self, config: MCPServerConfig):
        self.config = config
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._request_id = 0
        self.tools: list[MCPTool] = []
        self.connected = False
        self._cleanup_path = ""

    def connect(self) -> bool:
        """Start the MCP server process and initialize the connection."""
        try:
            workspace = Path(self.config.workspace or Path.cwd()).expanduser().resolve()
            runner = SandboxRunner(workspace)
            spec = CommandSpec.create(
                self.config.command,
                workspace,
                timeout_seconds=_MCP_TOOL_CALL_TIMEOUT,
                network=self.config.network,
                env=self.config.env,
                max_output_bytes=_MAX_MCP_OUTPUT_BYTES,
                require_os_isolation=self.config.require_os_isolation,
                allow_unisolated_host_process=self.config.allow_unisolated_host_process,
                # MCP credentials are explicit, trusted configuration. Only the
                # exact configured names may cross the process boundary.
                allowed_sensitive_env_keys=tuple(self.config.env),
            )
            prepared = runner.prepare(spec)
            self._cleanup_path = prepared.cleanup_path
            self._process = subprocess.Popen(
                list(prepared.argv),
                cwd=prepared.cwd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=dict(prepared.env),
                start_new_session=True,
            )
            ProcessStateRegistry.register_process(self._process)

            # Initialize
            init_response = self._send_request(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}},
                    "clientInfo": {"name": "NexusAI", "version": "3.0"},
                },
            )

            if not init_response or "error" in init_response:
                return False

            # Send initialized notification
            self._send_notification("notifications/initialized", {})

            # Discover tools
            tools_response = self._send_request("tools/list", {})
            if tools_response and "result" in tools_response:
                for tool_data in tools_response["result"].get("tools", []):
                    self.tools.append(
                        MCPTool(
                            name=tool_data["name"],
                            description=tool_data.get("description", ""),
                            input_schema=tool_data.get("inputSchema", {}),
                            server_name=self.config.name,
                        )
                    )

            self.connected = True
            return True

        except (FileNotFoundError, OSError, ValueError, PermissionError, json.JSONDecodeError):
            self.disconnect()
            return False

    def call_tool(self, tool_name: str, arguments: dict) -> dict:
        """Call a tool on this MCP server."""
        if not self.connected or not self._process:
            return {"error": "Not connected"}

        logger.info(
            "MCP tool call: server=%s tool=%s",
            self.config.name,
            tool_name,
        )

        response = self._send_request(
            "tools/call",
            {
                "name": tool_name,
                "arguments": arguments,
            },
        )

        if response and "result" in response:
            content = response["result"].get("content", [])
            text_parts = [c.get("text", "") for c in content if c.get("type") == "text"]
            result_text = "\n".join(text_parts)
            # SECURITY: Bound output size
            if len(result_text) > _MAX_MCP_OUTPUT_BYTES:
                result_text = result_text[:_MAX_MCP_OUTPUT_BYTES]
                logger.warning(
                    "MCP tool %s/%s output truncated to %d bytes",
                    self.config.name,
                    tool_name,
                    _MAX_MCP_OUTPUT_BYTES,
                )
            return {"result": result_text, "success": True}

        error_msg = "Unknown error"
        if response and "error" in response:
            error_msg = response.get("error", {}).get("message", "Unknown error")
        logger.warning(
            "MCP tool call failed: server=%s tool=%s error=%s",
            self.config.name,
            tool_name,
            error_msg,
        )
        return {"error": error_msg, "success": False}

    def disconnect(self):
        """Disconnect from the MCP server."""
        if self._process:
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=2)
            except (OSError, ValueError):
                pass
            ProcessStateRegistry.unregister_process(self._process)
            self._process = None
            self.connected = False
            if self._cleanup_path:
                try:
                    Path(self._cleanup_path).unlink(missing_ok=True)
                except OSError:
                    pass
                self._cleanup_path = ""

    def _send_request(self, method: str, params: dict) -> Optional[dict]:
        """Send a JSON-RPC request and wait for the response."""
        if not self._process:
            return None

        with self._lock:
            self._request_id += 1
            request = {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": method,
                "params": params,
            }

            try:
                line = json.dumps(request) + "\n"
                self._process.stdin.write(line)
                self._process.stdin.flush()

                response_line = readline_with_timeout(self._process.stdout, 30.0)
                if response_line is None:
                    self.disconnect()
                    return None
                if response_line:
                    return json.loads(response_line)
            except (BrokenPipeError, json.JSONDecodeError, OSError):
                pass

        return None

    def _send_notification(self, method: str, params: dict):
        """Send a JSON-RPC notification (no response expected)."""
        if not self._process:
            return

        with self._lock:
            notification = {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
            }
            try:
                line = json.dumps(notification) + "\n"
                self._process.stdin.write(line)
                self._process.stdin.flush()
            except (BrokenPipeError, OSError):
                pass


class MCPClient:
    """
    MCP client managing connections to multiple MCP servers.

    Discovers and aggregates tools from all connected servers,
    exposing them as standard tool definitions to the agent.

    Usage:
        client = MCPClient()
        client.add_server(MCPServerConfig(
            name="filesystem",
            command=["npx", "-y", "@modelcontextprotocol/server-filesystem", "/path"],
        ))
        client.connect_all()
        tools = client.get_all_tool_definitions()
        result = client.call_tool("mcp_filesystem_read_file", {"path": "/foo"})
    """

    # Built-in server configurations
    BUILTIN_SERVERS: dict[str, MCPServerConfig] = {
        "filesystem": MCPServerConfig(
            name="filesystem",
            command=["npx", "-y", "@modelcontextprotocol/server-filesystem"],
            description="Enhanced file system operations",
            network=True,
        ),
        "sqlite": MCPServerConfig(
            name="sqlite",
            command=["npx", "-y", "@modelcontextprotocol/server-sqlite"],
            description="SQLite database operations",
            network=True,
        ),
        "github": MCPServerConfig(
            name="github",
            command=["npx", "-y", "@modelcontextprotocol/server-github"],
            env={"GITHUB_PERSONAL_ACCESS_TOKEN": ""},
            description="GitHub repository operations",
            network=True,
        ),
    }

    def __init__(self):
        self._connections: dict[str, MCPConnection] = {}
        self._configs: dict[str, MCPServerConfig] = {}

    def add_server(self, config: MCPServerConfig):
        """Add an MCP server configuration."""
        self._configs[config.name] = config

    def load_from_config(self, config_path: str):
        """Load server configurations from a JSON config file."""
        try:
            with open(config_path) as f:
                data = json.load(f)
            for name, server_data in data.get("mcpServers", {}).items():
                config = MCPServerConfig(
                    name=name,
                    command=server_data.get("command", []),
                    env=server_data.get("env", {}),
                    description=server_data.get("description", ""),
                    enabled=bool(server_data.get("enabled", True)),
                    workspace=str(server_data.get("workspace", "")),
                    network=bool(server_data.get("network", False)),
                    require_os_isolation=bool(
                        server_data.get("requireOsIsolation", True)
                    ),
                )
                self.add_server(config)
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass

    def load_default_config(self):
        """Load MCP config from ~/.nexusai/mcp_servers.json."""
        config_path = nexus_home() / "mcp_servers.json"
        if config_path.exists():
            self.load_from_config(str(config_path))

    def connect_all(self) -> dict[str, bool]:
        """Connect to all configured MCP servers."""
        results = {}
        for name, config in self._configs.items():
            if not config.enabled:
                results[name] = False
                continue
            conn = MCPConnection(config)
            success = conn.connect()
            if success:
                self._connections[name] = conn
            results[name] = success
        return results

    def connect(self, server_name: str) -> bool:
        """Connect to a specific MCP server."""
        config = self._configs.get(server_name)
        if not config:
            return False
        conn = MCPConnection(config)
        success = conn.connect()
        if success:
            self._connections[server_name] = conn
        return success

    def disconnect_all(self):
        """Disconnect all MCP servers."""
        for conn in self._connections.values():
            conn.disconnect()
        self._connections.clear()

    def get_all_tools(self) -> list[MCPTool]:
        """Get all tools from all connected servers."""
        tools = []
        for conn in self._connections.values():
            tools.extend(conn.tools)
        return tools

    def get_all_tool_definitions(self) -> list[dict]:
        """Get all tool definitions in OpenAI format."""
        return [tool.to_tool_definition() for tool in self.get_all_tools()]

    def call_tool(self, prefixed_name: str, arguments: dict) -> str:
        """
        Call an MCP tool by its prefixed name (mcp_{server}_{tool}).

        Returns the result as a string.
        """
        # Parse the prefix: mcp_{server_name}_{tool_name}
        if not prefixed_name.startswith("mcp_"):
            return f"❌ Not an MCP tool: {prefixed_name}"

        # Find which server this tool belongs to
        for server_name, conn in self._connections.items():
            prefix = f"mcp_{server_name}_"
            if prefixed_name.startswith(prefix):
                tool_name = prefixed_name[len(prefix) :]
                result = conn.call_tool(tool_name, arguments)
                if result.get("success"):
                    return result.get("result", "")
                return f"❌ MCP Error: {result.get('error', 'Unknown error')}"

        return f"❌ No connected MCP server found for tool: {prefixed_name}"

    def is_mcp_tool(self, tool_name: str) -> bool:
        """Check if a tool name is an MCP tool."""
        return tool_name.startswith("mcp_")

    def get_status(self) -> dict:
        """Get connection status for all servers."""
        return {
            name: {
                "connected": conn.connected,
                "tools": len(conn.tools),
                "tool_names": [t.name for t in conn.tools],
            }
            for name, conn in self._connections.items()
        }

    def get_summary(self) -> str:
        """Human-readable summary of MCP connections."""
        if not self._connections:
            return "🔌 MCP: No servers connected"
        lines = [f"🔌 MCP Servers ({len(self._connections)} connected)"]
        for name, conn in self._connections.items():
            lines.append(f"  ✅ {name} — {len(conn.tools)} tools")
        total = sum(len(c.tools) for c in self._connections.values())
        lines.append(f"  Total MCP tools: {total}")
        return "\n".join(lines)
