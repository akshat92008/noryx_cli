"""MCP permission layer — controls MCP server and tool access."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class MCPPermissionScope(str, Enum):
    SERVER = "server"
    TOOL = "tool"
    ALL_TOOLS = "all_tools"


@dataclass
class MCPPermission:
    """Permission grant for an MCP server or tool."""

    server_name: str
    scope: MCPPermissionScope
    tool_name: str = ""
    granted_at: float = field(default_factory=time.time)
    granted_by: str = "user"
    content_hash: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "server_name": self.server_name,
            "scope": self.scope.value,
            "tool_name": self.tool_name,
            "granted_at": self.granted_at,
            "granted_by": self.granted_by,
            "content_hash": self.content_hash,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MCPPermission":
        return cls(
            server_name=data["server_name"],
            scope=MCPPermissionScope(data.get("scope", "server")),
            tool_name=data.get("tool_name", ""),
            granted_at=data.get("granted_at", time.time()),
            granted_by=data.get("granted_by", "user"),
            content_hash=data.get("content_hash", ""),
        )


class MCPPermissionLayer:
    """Permission layer for MCP server and tool access.

    MCP servers are untrusted by default. Users must explicitly approve
    server connections and individual tool invocations.
    """

    def __init__(self, state_dir: Path | None = None):
        if state_dir is None:
            state_dir = Path.home() / ".nexusai" / "mcp"
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._path = self._state_dir / "permissions.json"
        self._permissions: list[MCPPermission] = self._load()

    def _load(self) -> list[MCPPermission]:
        if not self._path.is_file():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return [MCPPermission.from_dict(p) for p in data.get("permissions", [])]
        except (json.JSONDecodeError, OSError, KeyError):
            return []

    def _save(self) -> None:
        data = {"permissions": [p.to_dict() for p in self._permissions]}
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def approve_server(
        self,
        server_name: str,
        *,
        all_tools: bool = False,
        granted_by: str = "user",
    ) -> MCPPermission:
        """Approve an MCP server connection."""
        perm = MCPPermission(
            server_name=server_name,
            scope=MCPPermissionScope.ALL_TOOLS if all_tools else MCPPermissionScope.SERVER,
            granted_by=granted_by,
        )
        self._permissions.append(perm)
        self._save()
        return perm

    def approve_tool(
        self,
        server_name: str,
        tool_name: str,
        *,
        granted_by: str = "user",
    ) -> MCPPermission:
        """Approve a specific MCP tool."""
        perm = MCPPermission(
            server_name=server_name,
            scope=MCPPermissionScope.TOOL,
            tool_name=tool_name,
            granted_by=granted_by,
        )
        self._permissions.append(perm)
        self._save()
        return perm

    def revoke_all(self, server_name: str) -> int:
        before = len(self._permissions)
        self._permissions = [p for p in self._permissions if p.server_name != server_name]
        self._save()
        return before - len(self._permissions)

    def is_approved(self, server_name: str) -> bool:
        for perm in self._permissions:
            if perm.server_name == server_name:
                return True
        return False

    def check_tool_access(self, server_name: str, tool_name: str) -> bool:
        for perm in self._permissions:
            if perm.server_name != server_name:
                continue
            if perm.scope == MCPPermissionScope.ALL_TOOLS:
                return True
            if perm.scope == MCPPermissionScope.SERVER:
                return True
            if perm.scope == MCPPermissionScope.TOOL and perm.tool_name == tool_name:
                return True
            if perm.scope == MCPPermissionScope.TOOL and tool_name == "*":
                return True
        return False

    def list_permissions(self, server_name: str = "") -> list[MCPPermission]:
        if server_name:
            return [p for p in self._permissions if p.server_name == server_name]
        return list(self._permissions)
