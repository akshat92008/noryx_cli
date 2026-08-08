"""Plugin and MCP Server Security Isolation Guard for Nexus CLI.

Validates manifests, permission declarations, prevents tool-name collision/spoofing,
and enforces execution isolation for extensions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

CORE_RESERVED_TOOLS = frozenset(
    {
        "read_file",
        "write_file",
        "edit_file",
        "patch_file",
        "run_command",
        "execute_command",
        "list_directory",
        "search_code",
        "git_commit",
        "git_push",
    }
)


@dataclass
class ExtensionPermissionDeclaration:
    plugin_id: str
    allowed_tools: tuple[str, ...] = ()
    allowed_paths: tuple[str, ...] = ()
    allowed_network_domains: tuple[str, ...] = ()
    allow_subprocess: bool = False
    timeout_seconds: float = 30.0


class PluginMCPGuard:
    """Enforces extension security isolation."""

    def __init__(self, reserved_tools: Sequence[str] = CORE_RESERVED_TOOLS):
        self.reserved_tools = set(reserved_tools)
        self.registered_extension_tools: dict[str, str] = {}  # tool_name -> plugin_id

    def validate_plugin_manifest(self, manifest: dict[str, Any]) -> ExtensionPermissionDeclaration:
        """Validate plugin manifest schema and declare permissions."""
        plugin_id = str(manifest.get("id") or manifest.get("name") or "").strip()
        if not plugin_id:
            raise ValueError("Plugin manifest missing 'id' or 'name'")

        requested_tools = tuple(manifest.get("tools", []))
        for tool in requested_tools:
            if tool in self.reserved_tools:
                raise ValueError(
                    f"Plugin {plugin_id!r} attempted to spoof reserved core tool name: {tool!r}"
                )
            if tool in self.registered_extension_tools:
                existing = self.registered_extension_tools[tool]
                raise ValueError(
                    f"Tool name collision: {tool!r} already registered by plugin {existing!r}"
                )

        # Register tools
        for tool in requested_tools:
            self.registered_extension_tools[tool] = plugin_id

        return ExtensionPermissionDeclaration(
            plugin_id=plugin_id,
            allowed_tools=requested_tools,
            allowed_paths=tuple(manifest.get("allowed_paths", [])),
            allowed_network_domains=tuple(manifest.get("allowed_network_domains", [])),
            allow_subprocess=bool(manifest.get("allow_subprocess", False)),
            timeout_seconds=float(manifest.get("timeout_seconds", 30.0)),
        )

    def validate_mcp_server(self, server_config: dict[str, Any]) -> None:
        """Validate MCP server config before spawning process."""
        name = server_config.get("name", "unnamed_mcp_server")
        command = server_config.get("command", "")
        if not command:
            raise ValueError(f"MCP Server {name!r} missing command string")

        # Check for dangerous command patterns
        if "rm -rf" in command or "sudo " in command:
            raise ValueError(f"MCP Server {name!r} command blocked by security policy: {command}")
