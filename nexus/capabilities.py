"""Tool Capability Declarations — machine-readable contracts for every tool.

Every tool (built-in, MCP, plugin, extension) must declare what it can do.
Tools without declared capabilities are rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import FrozenSet


class ToolCapability(str, Enum):
    """Capabilities a tool may require."""

    FS_READ = "fs_read"  # Read files from the workspace
    FS_WRITE = "fs_write"  # Create or modify files
    CMD_EXEC = "cmd_exec"  # Execute commands/processes
    NETWORK = "network"  # Make outbound network requests
    SECRET_ACCESS = "secret_access"  # Access credentials or secrets
    DEPLOYMENT = "deployment"  # Deploy to production/staging
    PKG_INSTALL = "pkg_install"  # Install system or language packages
    GIT_MUTATION = "git_mutation"  # Modify Git state (commit, push, branch)
    GIT_READ = "git_read"  # Read Git state (status, log, diff)
    EXTERNAL_EFFECTS = "external_effects"  # Side effects outside the workspace
    REPO_INDEX = "repo_index"  # Build or query repo graph index
    CONFIRMATION_REQUIRED = "confirmation_required"  # Needs user confirmation
    PURE = "pure"  # Deterministic computation without I/O or external effects


@dataclass(frozen=True)
class ToolCapabilityDeclaration:
    """Declares the capabilities required by a specific tool."""

    tool_name: str
    capabilities: FrozenSet[ToolCapability]
    description: str = ""
    confirmation_prompt: str = ""  # If CONFIRMATION_REQUIRED, what to show the user

    def requires(self, cap: ToolCapability) -> bool:
        return cap in self.capabilities

    def has_any(self, *caps: ToolCapability) -> bool:
        return bool(self.capabilities & frozenset(caps))

    def to_dict(self) -> dict:
        return {
            "tool_name": self.tool_name,
            "capabilities": sorted(c.value for c in self.capabilities),
            "description": self.description,
        }


# ── Built-in Tool Capability Registry ────────────────────────────────────────

TOOL_CAPABILITIES: dict[str, ToolCapabilityDeclaration] = {
    # File tools
    "read_file": ToolCapabilityDeclaration(
        "read_file",
        frozenset({ToolCapability.FS_READ}),
        "Read file contents",
    ),
    "write_file": ToolCapabilityDeclaration(
        "write_file",
        frozenset({ToolCapability.FS_WRITE}),
        "Create or overwrite files",
    ),
    "edit_file": ToolCapabilityDeclaration(
        "edit_file",
        frozenset({ToolCapability.FS_READ, ToolCapability.FS_WRITE}),
        "Surgical find-and-replace editing",
    ),
    "patch_file": ToolCapabilityDeclaration(
        "patch_file",
        frozenset({ToolCapability.FS_READ, ToolCapability.FS_WRITE}),
        "Line-range based editing",
    ),
    "multi_edit": ToolCapabilityDeclaration(
        "multi_edit",
        frozenset({ToolCapability.FS_READ, ToolCapability.FS_WRITE}),
        "Batch edits across multiple files",
    ),
    "file_info": ToolCapabilityDeclaration(
        "file_info",
        frozenset({ToolCapability.FS_READ}),
        "File metadata",
    ),
    "diff_files": ToolCapabilityDeclaration(
        "diff_files",
        frozenset({ToolCapability.FS_READ}),
        "Unified diff between two files",
    ),
    # Search tools
    "search_code": ToolCapabilityDeclaration(
        "search_code",
        frozenset({ToolCapability.FS_READ}),
        "Regex search across codebase",
    ),
    "list_directory": ToolCapabilityDeclaration(
        "list_directory",
        frozenset({ToolCapability.FS_READ}),
        "List directory contents",
    ),
    "find_files": ToolCapabilityDeclaration(
        "find_files",
        frozenset({ToolCapability.FS_READ}),
        "Glob-based file finder",
    ),
    "get_project_structure": ToolCapabilityDeclaration(
        "get_project_structure",
        frozenset({ToolCapability.FS_READ}),
        "Tree view of project",
    ),
    # Repo intelligence tools
    "repo_index": ToolCapabilityDeclaration(
        "repo_index",
        frozenset({ToolCapability.FS_READ, ToolCapability.REPO_INDEX}),
        "Build or refresh repo graph",
    ),
    "repo_symbols": ToolCapabilityDeclaration(
        "repo_symbols",
        frozenset({ToolCapability.FS_READ, ToolCapability.REPO_INDEX}),
        "Find declarations and callers",
    ),
    "repo_impact": ToolCapabilityDeclaration(
        "repo_impact",
        frozenset({ToolCapability.FS_READ, ToolCapability.REPO_INDEX}),
        "Find reverse dependencies and impacted tests",
    ),
    "repo_context": ToolCapabilityDeclaration(
        "repo_context",
        frozenset({ToolCapability.FS_READ, ToolCapability.REPO_INDEX}),
        "Rank task-relevant files",
    ),
    "repo_routes": ToolCapabilityDeclaration(
        "repo_routes",
        frozenset({ToolCapability.FS_READ, ToolCapability.REPO_INDEX}),
        "Inspect application routes",
    ),
    "repo_models": ToolCapabilityDeclaration(
        "repo_models",
        frozenset({ToolCapability.FS_READ, ToolCapability.REPO_INDEX}),
        "Inspect database models",
    ),
    # Shell tools
    "run_command": ToolCapabilityDeclaration(
        "run_command",
        frozenset({ToolCapability.CMD_EXEC}),
        "Shell command execution",
    ),
    "run_process": ToolCapabilityDeclaration(
        "run_process",
        frozenset({ToolCapability.CMD_EXEC}),
        "Sandboxed process execution",
    ),
    "process_run": ToolCapabilityDeclaration(
        "process_run",
        frozenset({ToolCapability.CMD_EXEC}),
        "Background process execution",
    ),
    # Git tools
    "git_status": ToolCapabilityDeclaration(
        "git_status",
        frozenset({ToolCapability.GIT_READ}),
        "Repository status",
    ),
    "git_diff": ToolCapabilityDeclaration(
        "git_diff",
        frozenset({ToolCapability.GIT_READ}),
        "View diffs",
    ),
    "git_log": ToolCapabilityDeclaration(
        "git_log",
        frozenset({ToolCapability.GIT_READ}),
        "View commit history",
    ),
    "git_commit": ToolCapabilityDeclaration(
        "git_commit",
        frozenset({ToolCapability.GIT_MUTATION}),
        "Stage and commit changes",
    ),
    "git_branch": ToolCapabilityDeclaration(
        "git_branch",
        frozenset({ToolCapability.GIT_READ, ToolCapability.GIT_MUTATION}),
        "Branch operations",
    ),
    # Web tools
    "web_fetch": ToolCapabilityDeclaration(
        "web_fetch",
        frozenset({ToolCapability.NETWORK}),
        "Fetch and read URLs",
    ),
    "web_search": ToolCapabilityDeclaration(
        "web_search",
        frozenset({ToolCapability.NETWORK}),
        "Web search via DuckDuckGo",
    ),
    # Behavioral verification tools
    "api_check": ToolCapabilityDeclaration(
        "api_check",
        frozenset({ToolCapability.NETWORK, ToolCapability.CMD_EXEC}),
        "Validate local HTTP contract",
    ),
    "database_check": ToolCapabilityDeclaration(
        "database_check",
        frozenset({ToolCapability.FS_READ}),
        "Validate SQLite integrity",
    ),
    "browser_check": ToolCapabilityDeclaration(
        "browser_check",
        frozenset({ToolCapability.NETWORK, ToolCapability.CMD_EXEC}),
        "Run Playwright workflow",
    ),
    "security_scan": ToolCapabilityDeclaration(
        "security_scan",
        frozenset({ToolCapability.FS_READ}),
        "Deterministic security scan",
    ),
    "process_status": ToolCapabilityDeclaration(
        "process_status",
        frozenset({ToolCapability.CMD_EXEC}),
        "Inspect a background process owned by the current run",
    ),
    "process_stop": ToolCapabilityDeclaration(
        "process_stop",
        frozenset({ToolCapability.CMD_EXEC}),
        "Stop a background process owned by the current run",
    ),
    "repo_navigate": ToolCapabilityDeclaration(
        "repo_navigate",
        frozenset({ToolCapability.FS_READ, ToolCapability.REPO_INDEX}),
        "Navigate repository symbols and relationships",
    ),
    "github_list_issues": ToolCapabilityDeclaration(
        "github_list_issues",
        frozenset({ToolCapability.NETWORK, ToolCapability.EXTERNAL_EFFECTS}),
        "List GitHub issues through the authenticated CLI",
    ),
    "github_view_issue": ToolCapabilityDeclaration(
        "github_view_issue",
        frozenset({ToolCapability.NETWORK, ToolCapability.EXTERNAL_EFFECTS}),
        "Read a GitHub issue through the authenticated CLI",
    ),
    "github_create_pr": ToolCapabilityDeclaration(
        "github_create_pr",
        frozenset({
            ToolCapability.NETWORK,
            ToolCapability.EXTERNAL_EFFECTS,
            ToolCapability.GIT_MUTATION,
            ToolCapability.CONFIRMATION_REQUIRED,
        }),
        "Create a GitHub pull request",
    ),
    "generate_dashboard": ToolCapabilityDeclaration(
        "generate_dashboard",
        frozenset({ToolCapability.FS_READ, ToolCapability.FS_WRITE}),
        "Generate a local benchmark dashboard",
    ),
}


def validate_tool_capabilities(tool_name: str) -> ToolCapabilityDeclaration | None:
    """Look up the capability declaration for a tool. Returns None if undeclared."""
    return TOOL_CAPABILITIES.get(tool_name)


def register_dynamic_tool(
    tool_name: str,
    capabilities: FrozenSet[ToolCapability],
    description: str = "",
) -> ToolCapabilityDeclaration:
    """Register a dynamic tool (MCP, plugin, extension) with its capabilities."""
    decl = ToolCapabilityDeclaration(tool_name, capabilities, description)
    TOOL_CAPABILITIES[tool_name] = decl
    return decl


def check_tool_allowed(
    tool_name: str,
    allowed_capabilities: FrozenSet[ToolCapability] | None = None,
    disallowed_capabilities: FrozenSet[ToolCapability] | None = None,
) -> tuple[bool, str]:
    """Check if a tool is allowed given capability constraints.

    Returns (allowed, reason).
    """
    decl = TOOL_CAPABILITIES.get(tool_name)
    if decl is None:
        return False, f"Tool '{tool_name}' has no capability declaration"

    if disallowed_capabilities:
        blocked = decl.capabilities & disallowed_capabilities
        if blocked:
            names = ", ".join(c.value for c in sorted(blocked))
            return False, f"Tool '{tool_name}' requires blocked capabilities: {names}"

    if allowed_capabilities is not None:
        missing = decl.capabilities - allowed_capabilities
        if missing:
            names = ", ".join(c.value for c in sorted(missing))
            return False, f"Tool '{tool_name}' requires ungranted capabilities: {names}"

    return True, ""
