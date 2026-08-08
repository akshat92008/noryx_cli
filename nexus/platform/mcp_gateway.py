"""MCP Gateway — unified MCP server management with permission layer."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexus.mcp.client import MCPClient, MCPConnection, MCPServerConfig
from nexus.platform.audit import AuditAction, AuditLogger
from nexus.platform.mcp_permissions import MCPPermissionLayer
from nexus.trust import TrustStore

logger = logging.getLogger(__name__)

MCP_CONFIG_NAMES = (".mcp.json", "mcp_servers.json")
DEFAULT_MCP_DIR = Path.home() / ".nexusai" / "mcp"


@dataclass
class MCPServerRecord:
    """Registered MCP server."""

    name: str
    command: list[str]
    enabled: bool = True
    description: str = ""
    env: dict[str, str] = field(default_factory=dict)
    network: bool = False
    workspace: str = ""
    source: str = "config"
    tool_count: int = 0
    connected: bool = False
    error: str = ""


class MCPGateway:
    """Unified MCP server gateway with permission enforcement."""

    def __init__(
        self,
        *,
        working_dir: str = "",
        state_dir: Path | None = None,
    ):
        self.working_dir = Path(working_dir).resolve() if working_dir else Path.cwd()
        self.state_dir = state_dir or DEFAULT_MCP_DIR
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._registry_path = self.state_dir / "servers.json"
        self._servers: dict[str, MCPServerRecord] = {}
        self._connections: dict[str, MCPConnection] = {}
        self._client: MCPClient | None = None
        self._permissions = MCPPermissionLayer(self.state_dir)
        self._audit = AuditLogger(self.state_dir.parent / "extensions")
        self._trust = TrustStore(str(self.working_dir))
        self._load_registry()
        self._load_project_config()

    def _load_registry(self) -> None:
        if not self._registry_path.is_file():
            return
        try:
            data = json.loads(self._registry_path.read_text(encoding="utf-8"))
            for entry in data.get("servers", []):
                self._servers[entry["name"]] = MCPServerRecord(
                    name=entry["name"],
                    command=entry.get("command", []),
                    enabled=entry.get("enabled", True),
                    description=entry.get("description", ""),
                    env=entry.get("env", {}),
                    network=entry.get("network", False),
                    workspace=entry.get("workspace", ""),
                    source=entry.get("source", "registry"),
                )
        except (json.JSONDecodeError, OSError, KeyError) as exc:
            logger.warning("Failed to load MCP registry: %s", exc)

    def _save_registry(self) -> None:
        data = {
            "version": "1.0.0",
            "servers": [
                {
                    "name": s.name,
                    "command": s.command,
                    "enabled": s.enabled,
                    "description": s.description,
                    "env": s.env,
                    "network": s.network,
                    "workspace": s.workspace,
                    "source": s.source,
                }
                for s in self._servers.values()
            ],
        }
        self._registry_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load_project_config(self) -> None:
        for config_name in MCP_CONFIG_NAMES:
            config_path = self.working_dir / config_name
            if not config_path.is_file():
                continue
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
                servers = data.get("mcpServers", data.get("servers", {}))
                for name, cfg in servers.items():
                    if name in self._servers:
                        continue
                    command = cfg.get("command", "")
                    args = cfg.get("args", [])
                    if isinstance(command, str):
                        cmd = [command] + args
                    else:
                        cmd = command
                    self._servers[name] = MCPServerRecord(
                        name=name,
                        command=cmd,
                        enabled=cfg.get("enabled", True),
                        description=cfg.get("description", ""),
                        env=cfg.get("env", {}),
                        network=cfg.get("network", False),
                        workspace=str(self.working_dir),
                        source="project",
                    )
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to load MCP config %s: %s", config_name, exc)

    def add_server(
        self,
        name: str,
        command: list[str],
        *,
        description: str = "",
        env: dict[str, str] | None = None,
        network: bool = False,
        enable: bool = False,
    ) -> MCPServerRecord:
        """Add an MCP server to the registry."""
        record = MCPServerRecord(
            name=name,
            command=command,
            enabled=enable,
            description=description,
            env=env or {},
            network=network,
            workspace=str(self.working_dir),
            source="registry",
        )
        self._servers[name] = record
        self._save_registry()
        self._audit.log(AuditAction.MCP_CONNECT, name, details={"command": command})
        return record

    def remove_server(self, name: str) -> bool:
        if name not in self._servers:
            return False
        self.disconnect(name)
        del self._servers[name]
        self._save_registry()
        self._permissions.revoke_all(name)
        return True

    def enable_server(self, name: str) -> tuple[bool, str]:
        record = self._servers.get(name)
        if not record:
            return False, f"MCP server '{name}' not found"
        if not self._permissions.is_approved(name):
            return False, f"MCP server '{name}' requires permission approval"
        record.enabled = True
        self._save_registry()
        return True, f"Enabled MCP server '{name}'"

    def disable_server(self, name: str) -> tuple[bool, str]:
        record = self._servers.get(name)
        if not record:
            return False, f"MCP server '{name}' not found"
        self.disconnect(name)
        record.enabled = False
        self._save_registry()
        return True, f"Disabled MCP server '{name}'"

    def connect(self, name: str) -> tuple[bool, str]:
        record = self._servers.get(name)
        if not record or not record.enabled:
            return False, f"MCP server '{name}' not found or disabled"

        if not self._permissions.check_tool_access(name, "*"):
            return False, f"MCP server '{name}' lacks permission"

        config = MCPServerConfig(
            name=record.name,
            command=record.command,
            env=record.env,
            description=record.description,
            enabled=True,
            workspace=record.workspace or str(self.working_dir),
            network=record.network,
        )
        conn = MCPConnection(config)
        if not conn.connect():
            record.error = "Connection failed"
            return False, f"Failed to connect to MCP server '{name}'"

        self._connections[name] = conn
        record.connected = True
        record.tool_count = len(conn.tools)
        record.error = ""
        self._audit.log(AuditAction.MCP_CONNECT, name, success=True)
        return True, f"Connected to '{name}' ({record.tool_count} tools)"

    def disconnect(self, name: str) -> None:
        conn = self._connections.pop(name, None)
        if conn:
            conn.disconnect()
        record = self._servers.get(name)
        if record:
            record.connected = False

    def disconnect_all(self) -> None:
        for name in list(self._connections):
            self.disconnect(name)

    def call_tool(self, server_name: str, tool_name: str, arguments: dict) -> dict:
        if not self._permissions.check_tool_access(server_name, tool_name):
            return {"error": f"Permission denied for MCP tool '{tool_name}' on '{server_name}'"}

        conn = self._connections.get(server_name)
        if not conn:
            return {"error": f"MCP server '{server_name}' not connected"}

        result = conn.call_tool(tool_name, arguments)
        self._audit.log(
            AuditAction.MCP_TOOL_CALL,
            server_name,
            details={"tool": tool_name, "arguments_keys": list(arguments.keys())},
        )
        return result

    def list_servers(self) -> list[MCPServerRecord]:
        return list(self._servers.values())

    def get_server(self, name: str) -> MCPServerRecord | None:
        return self._servers.get(name)

    @property
    def permissions(self) -> MCPPermissionLayer:
        return self._permissions

    def doctor(self) -> list[dict[str, Any]]:
        """Run diagnostics on all MCP servers."""
        results: list[dict[str, Any]] = []
        for record in self._servers.values():
            diag: dict[str, Any] = {
                "name": record.name,
                "enabled": record.enabled,
                "connected": record.connected,
                "command": record.command,
                "tool_count": record.tool_count,
                "source": record.source,
                "permission_approved": self._permissions.is_approved(record.name),
            }
            if record.error:
                diag["error"] = record.error
            results.append(diag)
        return results
