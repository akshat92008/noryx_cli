"""Canonical set of recognized project instruction filenames.

Both :class:`~nexus.project_memory.ProjectMemory` and
:class:`~nexus.trust.TrustStore` / :class:`~nexus.trust.TrustReader` import
from this module so the two systems cannot diverge.

The order is significant: earlier entries take priority when multiple files
exist in the same directory.
"""

from __future__ import annotations

#: All filenames that Noryx treats as authoritative project-instruction files.
#: Any file whose name is in this tuple may contain commands, hooks, tool
#: definitions, or safety rules that will influence agent behaviour.  Every
#: such file MUST be approved by the user via TrustAuthority before its
#: content is acted upon.
PROJECT_INSTRUCTION_FILES: tuple[str, ...] = (
    "NORYX.md",
    "noryx.md",
    ".noryx.md",
    "NEXUS.md",
    "nexus.md",
    ".nexus.md",       # NOTE: was erroneously ".noryx.md" in project_memory.py
    "AGENTS.md",
    "AGENT.md",
    "CLAUDE.md",
    ".mcp.json",
    "mcp_servers.json",
    "settings.json",
    "settings.local.json",
    "hooks.json",
    "plugin.json",
    ".lsp.json",
)

#: Subset used by :class:`~nexus.project_memory.ProjectMemory` when searching
#: for human-readable rule documents (Markdown only, not JSON config).
PROJECT_RULE_FILENAMES: tuple[str, ...] = (
    "NORYX.md",
    "noryx.md",
    ".noryx.md",
    "NEXUS.md",
    "nexus.md",
    ".nexus.md",
    "AGENTS.md",
    "AGENT.md",
    "CLAUDE.md",
)

#: Glob patterns that point to trust-sensitive files inside `.noryx` / `.nexus`
#: control directories.  These are always included in trust scans regardless
#: of any higher-level ignore rules.
TRUST_SENSITIVE_CONTROL_PATTERNS: tuple[tuple[str, str], ...] = (
    # (control_dir, glob_pattern_within_control_dir)
    (".noryx", "skills/*.md"),
    (".noryx", "policies.*"),
    (".noryx", "config.*"),
    (".noryx", "verify.json"),
    (".nexus", "skills/*.md"),
    (".nexus", "policies.*"),
    (".nexus", "config.*"),
    (".nexus", "verify.json"),
)

#: Path *parts* that are pure runtime artefacts and should never appear in
#: workspace mutation diffs or trust scans.
IGNORED_RUNTIME_PARTS: frozenset[str] = frozenset(
    {
        ".git",
        ".nexusai",
        "node_modules",
        "venv",
        ".venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        # .noryx sub-directories that are runtime-only
        "cache",
        "logs",
        "tmp",
    }
)
