"""
Coding tools — the agent's hands for files, repository intelligence, shell, Git, and web.

Tools:
  File:    read_file, write_file, edit_file, patch_file, multi_edit, file_info, diff_files
  Search:  search_code, list_directory, find_files, get_project_structure,
           repo_index, repo_symbols, repo_impact
  Shell:   run_command, process_run
  Git:     git_status, git_diff, git_commit, git_log, git_branch
  Web:     web_fetch, web_search
"""

import atexit
import contextvars
import fnmatch
import hashlib
import html
import http.client
import json
import mimetypes
import os
import re
import shlex
import signal
import socket
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from nexus.paths import nexus_home

_tool_working_dir = contextvars.ContextVar("tool_working_dir", default=None)
_tool_history = contextvars.ContextVar("tool_history", default=None)
_tool_owner = contextvars.ContextVar("tool_owner", default="")


@contextmanager
def tool_context(working_dir: str, history=None, owner: str = ""):
    token_dir = _tool_working_dir.set(str(working_dir))
    token_owner = _tool_owner.set(owner)
    token_hist = None
    if history is not None:
        token_hist = _tool_history.set(history)
    try:
        yield
    finally:
        _tool_working_dir.reset(token_dir)
        _tool_owner.reset(token_owner)
        if token_hist is not None:
            _tool_history.reset(token_hist)


def get_history():
    history = _tool_history.get()
    if history is None:
        from nexus.history import FileHistory

        return FileHistory()
    return history


from dataclasses import dataclass, field
from typing import Any, Callable
from enum import Enum

class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    DANGEROUS = "dangerous"

class PermissionLevel(Enum):
    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    NETWORK = "network"

class ToolStatus(Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    BLOCKED = "blocked"
    PERMISSION_DENIED = "permission_denied"
    INVALID_INPUT = "invalid_input"
    ENVIRONMENT_UNAVAILABLE = "environment_unavailable"
    PARTIAL = "partial"

@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] = field(default_factory=dict)
    risk_level: RiskLevel = RiskLevel.MEDIUM
    permission: PermissionLevel = PermissionLevel.READ
    mutates_workspace: bool = False
    requires_network: bool = False
    default_timeout_seconds: float = 120.0
    handler: Callable | None = None
    
    def to_openai_format(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            }
        }

@dataclass
class ToolResult:
    status: ToolStatus
    output: str
    evidence: str = ""
    error: str = ""
    duration: float = 0.0
    
    def __post_init__(self) -> None:
        """Normalize legacy statuses into the canonical enum and fail closed."""
        if isinstance(self.status, ToolStatus):
            return
        if isinstance(self.status, bool):
            invalid = str(self.status)
            self.status = ToolStatus.FAILURE
            if not self.error:
                self.error = f"Invalid structured tool status: {invalid}"
            return
        if self.status == 0:
            self.status = ToolStatus.SUCCESS
            return
        if self.status == 1:
            self.status = ToolStatus.FAILURE
            return
        raw = str(self.status).strip().lower().removeprefix("toolstatus.")
        try:
            self.status = ToolStatus(raw)
        except ValueError:
            invalid = str(self.status)
            self.status = ToolStatus.FAILURE
            if not self.error:
                self.error = f"Invalid structured tool status: {invalid}"

    @property
    def success(self) -> bool:
        return self.status == ToolStatus.SUCCESS

class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, ToolDefinition] = {}
        
    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool
        
    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)
        
    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

# Global registry instance
registry = ToolRegistry()

# ── Tool definitions (OpenAI function-calling format) ────────────────────────

RAW_TOOL_DEFINITIONS = [
    # ─── FILE TOOLS ──────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file at the given path. Returns the file text with line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative file path to read.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Optional 1-based start line (inclusive).",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Optional 1-based end line (inclusive).",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file with the given content. Parent directories are created automatically. Tracked for undo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to write to.",
                    },
                    "content": {
                        "type": "string",
                        "description": "Full file content to write.",
                    },
                    "scope_reason": {
                        "type": "string",
                        "description": "Required when repository evidence justifies expanding beyond the pre-approved file scope.",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Replace a specific string in a file with new content. Use for surgical edits. The old_text must match exactly and be unique. Tracked for undo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to edit.",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "The exact text to find and replace (must be unique in the file).",
                    },
                    "new_text": {
                        "type": "string",
                        "description": "The replacement text.",
                    },
                    "scope_reason": {
                        "type": "string",
                        "description": "Required when repository evidence justifies expanding beyond the pre-approved file scope.",
                    },
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch_file",
            "description": "Apply a line-range based edit to a file. Can insert, replace, or delete lines by line number. Tracked for undo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path to patch.",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "1-based start line number for the edit range.",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "1-based end line number (inclusive). Use same as start_line to replace one line. Use 0 for end_line to insert before start_line.",
                    },
                    "new_content": {
                        "type": "string",
                        "description": "The new content to insert/replace. Use empty string to delete lines.",
                    },
                    "scope_reason": {
                        "type": "string",
                        "description": "Required when repository evidence justifies expanding beyond the pre-approved file scope.",
                    },
                },
                "required": ["path", "start_line", "end_line", "new_content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "multi_edit",
            "description": "Apply multiple edits across one or more files in a single call. Each edit specifies a file, old_text, and new_text. All edits are atomic per-file. Tracked for undo.",
            "parameters": {
                "type": "object",
                "properties": {
                    "scope_reason": {
                        "type": "string",
                        "description": "Required when repository evidence justifies expanding beyond the pre-approved file scope.",
                    },
                    "edits": {
                        "type": "array",
                        "description": "Array of edit objects.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "path": {"type": "string", "description": "File path"},
                                "old_text": {"type": "string", "description": "Text to find"},
                                "new_text": {"type": "string", "description": "Replacement text"},
                            },
                            "required": ["path", "old_text", "new_text"],
                        },
                    },
                },
                "required": ["edits"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_notebook",
            "description": "Read a Jupyter Notebook (.ipynb) and display its cells.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative file path to the notebook.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_notebook_cell",
            "description": "Edit a specific cell in a Jupyter Notebook.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Absolute or relative file path to the notebook.",
                    },
                    "cell_index": {
                        "type": "integer",
                        "description": "The 0-based index of the cell to edit.",
                    },
                    "new_source": {
                        "type": "string",
                        "description": "The new content for the cell.",
                    },
                },
                "required": ["path", "cell_index", "new_source"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "schedule_routine",
            "description": "Schedule a routine task to run in the background (e.g., monitor a log, poll an API).",
            "parameters": {
                "type": "object",
                "properties": {
                    "interval": {
                        "type": "integer",
                        "description": "The interval in seconds between runs.",
                    },
                    "task": {
                        "type": "string",
                        "description": "The task instruction for the background subagent.",
                    },
                },
                "required": ["interval", "task"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "message_peer",
            "description": "Message an active peer subagent by name.",
            "parameters": {
                "type": "object",
                "properties": {
                    "peer_name": {
                        "type": "string",
                        "description": "The name of the peer subagent.",
                    },
                    "message": {
                        "type": "string",
                        "description": "The message to send.",
                    },
                },
                "required": ["peer_name", "message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "file_info",
            "description": "Get detailed metadata about a file: size, permissions, last modified, type, encoding, line count.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File or directory path.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "diff_files",
            "description": "Show a unified diff between two files.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_a": {"type": "string", "description": "Path to the first file."},
                    "file_b": {"type": "string", "description": "Path to the second file."},
                },
                "required": ["file_a", "file_b"],
            },
        },
    },
    # ─── SEARCH TOOLS ────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search for a pattern across files in a directory (like grep -rn). Returns matching lines with file paths and line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Search pattern (regex supported).",
                    },
                    "directory": {
                        "type": "string",
                        "description": "Directory to search in (default: current directory).",
                    },
                    "file_pattern": {
                        "type": "string",
                        "description": "Glob pattern to filter files, e.g. '*.py' or '*.js'.",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List contents of a directory with file sizes and types.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Directory path to list (default: current directory).",
                    },
                    "recursive": {
                        "type": "boolean",
                        "description": "If true, list recursively (default: false).",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Max depth for recursive listing (default: 3).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": "Find files matching a glob pattern in a directory tree.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern, e.g. '**/*.py' or 'config.*'.",
                    },
                    "directory": {
                        "type": "string",
                        "description": "Root directory to search (default: current directory).",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_project_structure",
            "description": "Get a tree view of the project directory structure, respecting .gitignore patterns.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Root path (default: current directory).",
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "Maximum depth to traverse (default: 4).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "repo_index",
            "description": (
                "Build or incrementally refresh Nexus' persistent repository graph. "
                "Returns file, symbol, import, language, test, and parse-error counts."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "force": {
                        "type": "boolean",
                        "description": "Reparse every supported file instead of reusing cache.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "repo_symbols",
            "description": "Find symbol declarations and parsed callers in the repository graph.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Function, class, method, or symbol name.",
                    },
                    "include_callers": {
                        "type": "boolean",
                        "description": "Also return files with parsed calls to the symbol.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum declaration and caller results (default 50).",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "repo_impact",
            "description": (
                "Return direct imports, reverse importers, and impacted tests for changed files."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Repository-relative changed file paths.",
                    },
                },
                "required": ["paths"],
            },
        },
    },
    # ─── SHELL TOOLS ─────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a shell command and return stdout/stderr. Use for running code, tests, git, package managers, etc. Blocks until completion.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to execute.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Optional working directory for the command.",
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 120).",
                    },
                    "network": {
                        "type": "boolean",
                        "description": "Request explicit network access (default: false).",
                        "default": False,
                    },
                    "require_os_isolation": {
                        "type": "boolean",
                        "description": ("Fail closed unless a native OS sandbox is available."),
                        "default": True,
                    },
                    "allow_unisolated_host_process": {
                        "type": "boolean",
                        "description": (
                            "Explicit trusted-host escape hatch. Valid only when native isolation "
                            "is disabled by a human-controlled caller."
                        ),
                        "default": False,
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "process_run",
            "description": "Start a background process (non-blocking). Returns the PID. Useful for starting servers or long-running tasks. Output is captured and can be retrieved later.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "The shell command to run in the background.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Optional working directory.",
                    },
                    "network": {
                        "type": "boolean",
                        "description": (
                            "Declare that the background process requires network access."
                        ),
                        "default": False,
                    },
                    "require_os_isolation": {
                        "type": "boolean",
                        "description": "Fail closed unless a native OS sandbox is available.",
                        "default": True,
                    },
                    "allow_unisolated_host_process": {
                        "type": "boolean",
                        "description": (
                            "Explicit trusted-host escape hatch. Valid only when native isolation "
                            "is disabled by a human-controlled caller."
                        ),
                        "default": False,
                    },
                    "timeout": {
                        "type": "integer",
                        "description": "Maximum lifetime in seconds (default 3600, max 86400).",
                        "default": 3600,
                    },
                    "max_output_bytes": {
                        "type": "integer",
                        "description": "Maximum retained bytes per output stream.",
                        "default": 1000000,
                    },
                },
                "required": ["command"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "process_status",
            "description": "Poll a Nexus-started background process and read its complete stdout/stderr logs.",
            "parameters": {
                "type": "object",
                "properties": {"pid": {"type": "integer"}},
                "required": ["pid"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "process_stop",
            "description": "Terminate a background process previously started by Nexus. Cannot target arbitrary system PIDs.",
            "parameters": {
                "type": "object",
                "properties": {"pid": {"type": "integer"}},
                "required": ["pid"],
            },
        },
    },
    # ─── GIT TOOLS ───────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Show the current git status: branch name, staged files, modified files, untracked files. Provides a comprehensive overview of the repository state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "cwd": {
                        "type": "string",
                        "description": "Repository path (default: current directory).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show git diffs. By default shows unstaged changes. Use staged=true for staged changes, or provide a commit hash/range.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "description": "Optional: commit hash, branch name, or range like 'HEAD~3..HEAD'. Omit for working directory changes.",
                    },
                    "staged": {
                        "type": "boolean",
                        "description": "If true, show staged (cached) changes instead of unstaged.",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Optional: limit diff to a specific file.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Repository path (default: current directory).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "Stage files and create a git commit. Can stage all changes, specific files, or just commit what's already staged.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Commit message.",
                    },
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional: specific files to stage before committing. Omit to commit what's already staged.",
                    },
                    "all": {
                        "type": "boolean",
                        "description": "If true, stage ALL changes (git add -A) before committing.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Repository path (default: current directory).",
                    },
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "View recent git commit history with hash, author, date, and message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of commits to show (default: 15).",
                    },
                    "oneline": {
                        "type": "boolean",
                        "description": "If true, show compact one-line format (default: true).",
                    },
                    "file_path": {
                        "type": "string",
                        "description": "Optional: show commits that touched a specific file.",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Repository path (default: current directory).",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_branch",
            "description": "List, create, switch, or delete git branches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "create", "switch", "delete"],
                        "description": "Action to perform (default: list).",
                    },
                    "name": {
                        "type": "string",
                        "description": "Branch name (required for create/switch/delete).",
                    },
                    "cwd": {
                        "type": "string",
                        "description": "Repository path (default: current directory).",
                    },
                },
                "required": [],
            },
        },
    },
    # ─── WEB TOOLS ───────────────────────────────────────────────────────
    {
        "type": "function",
        "function": {
            "name": "web_fetch",
            "description": "Fetch a URL and extract readable text content. Use for reading documentation, APIs, or web pages. Strips HTML and returns clean text.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The URL to fetch.",
                    },
                    "max_length": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 100000,
                        "description": "Maximum characters to return (default: 10000).",
                    },
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Search the web for information. Returns relevant results with titles, URLs, and snippets. Uses DuckDuckGo (no API key needed).",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query.",
                    },
                    "max_results": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 20,
                        "description": "Maximum number of results (default: 5).",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

RAW_TOOL_DEFINITIONS.extend(
    [
        {
            "type": "function",
            "function": {
                "name": "repo_context",
                "description": (
                    "Rank repository files relevant to a task using paths, symbols, routes, "
                    "database models, imports, tests, and current Git changes."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "default": 40},
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "repo_routes",
                "description": "List API and UI routes discovered by the repository index.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "repo_models",
                "description": "List ORM, Prisma, and SQL models discovered by the repository index.",
                "parameters": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "repo_navigate",
                "description": (
                    "Use a persistent LSP server for document symbols, definitions, or "
                    "references, with Tree-sitter/RepoGraph fallback for symbols."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "language": {"type": "string"},
                        "operation": {
                            "type": "string",
                            "enum": ["symbols", "definition", "references"],
                        },
                        "line": {"type": "integer", "default": 0},
                        "character": {"type": "integer", "default": 0},
                    },
                    "required": ["path", "language", "operation"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "run_process",
                "description": (
                    "Run a typed argv command without a shell inside Nexus' strongest available "
                    "sandbox. Network is off unless explicitly approved."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "argv": {
                            "type": "array",
                            "items": {"type": "string"},
                            "minItems": 1,
                        },
                        "cwd": {"type": "string"},
                        "timeout": {"type": "number", "default": 120},
                        "network": {"type": "boolean", "default": False},
                        "require_os_isolation": {"type": "boolean", "default": True},
                        "allow_unisolated_host_process": {
                            "type": "boolean",
                            "default": False,
                            "description": "Explicit trusted-host escape hatch.",
                        },
                    },
                    "required": ["argv"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "api_check",
                "description": "Verify a local HTTP API status, JSON fields, and response text.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "method": {"type": "string", "default": "GET"},
                        "expected_status": {"type": "integer", "default": 200},
                        "expected_json": {"type": "object"},
                        "expected_text": {"type": "string"},
                        "json_body": {},
                        "allow_external": {"type": "boolean", "default": False},
                    },
                    "required": ["url"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "database_check",
                "description": "Run read-only SQLite integrity, foreign-key, and schema checks.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "sql": {
                            "type": "string",
                            "description": "Optional migration SQL to inspect without executing.",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "security_scan",
                "description": (
                    "Run Nexus' deterministic secret and unsafe-code pattern scan. "
                    "This does not claim a complete security audit."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "paths": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "browser_check",
                "description": (
                    "Run a deterministic Playwright browser workflow against localhost "
                    "(optional nexusai-cli[browser] extra)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "steps": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "action": {"type": "string"},
                                    "selector": {"type": "string"},
                                    "value": {"type": "string"},
                                },
                                "required": ["action"],
                            },
                        },
                        "screenshot_path": {"type": "string"},
                        "allow_external": {"type": "boolean", "default": False},
                    },
                    "required": ["url"],
                },
            },
        },
    ],
)

RAW_TOOL_DEFINITIONS.extend(
    [
        {
            "type": "function",
            "function": {
                "name": "github_list_issues",
                "description": "List open issues in the GitHub repository.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of issues to list.",
                        }
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "github_view_issue",
                "description": "View a specific GitHub issue and its comments.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "number": {"type": "string", "description": "The issue number to view."}
                    },
                    "required": ["number"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "github_create_pr",
                "description": "Create a Pull Request on GitHub for the current branch. DANGEROUS: Always verify tests pass before calling this.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "The title of the PR."},
                        "body": {
                            "type": "string",
                            "description": "The description body of the PR.",
                        },
                        "base": {
                            "type": "string",
                            "description": "The base branch to merge into (optional, defaults to repo default).",
                        },
                    },
                    "required": ["title", "body"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "generate_dashboard",
                "description": "Generate an HTML regression dashboard from a benchmark JSON result file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "input_path": {
                            "type": "string",
                            "description": "Path to the JSON benchmark result file.",
                        },
                        "output_path": {
                            "type": "string",
                            "description": "Path where the HTML dashboard should be written.",
                        },
                    },
                    "required": ["input_path", "output_path"],
                },
            },
        },
    ]
)

TOOL_DEFINITIONS = [
    ToolDefinition(
        name=raw["function"]["name"],
        description=raw["function"]["description"],
        input_schema=raw["function"].get("parameters", {}),
    )
    for raw in RAW_TOOL_DEFINITIONS
]

# ── Ignore patterns ─────────────────────────────────────────────────────────

IGNORE_DIRS = {
    ".git",
    "__pycache__",
    "node_modules",
    ".next",
    ".venv",
    "venv",
    "dist",
    "build",
    ".cache",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "env",
    ".env",
    ".idea",
    ".vscode",
    "target",
    "coverage",
    ".nexusai",
    ".ruff_cache",
    ".nuxt",
    ".output",
    ".turbo",
    "Library",
    "Applications",
    "Pictures",
    "Music",
    "Movies",
    "Downloads",
    "System",
    "Volumes",
    ".Trash",
    ".DocumentRevisions-V100",
}

IGNORE_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".so",
    ".dylib",
    ".dll",
    ".exe",
    ".o",
    ".a",
    ".class",
    ".jar",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".ico",
    ".svg",
    ".webp",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".zip",
    ".tar",
    ".gz",
    ".bz2",
    ".7z",
    ".pdf",
    ".doc",
    ".docx",
}


def _should_ignore(path: Path) -> bool:
    """Check if a path should be ignored."""
    if path.name.startswith(".") and path.name not in (
        ".env",
        ".gitignore",
        ".eslintrc",
        ".prettierrc",
        ".nexusai",
    ):
        return True
    if path.is_dir() and path.name in IGNORE_DIRS:
        return True
    if path.is_file() and path.suffix.lower() in IGNORE_EXTENSIONS:
        return True
    return False


def _format_size(size: int) -> str:
    """Human-readable file size."""
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f}{unit}" if unit != "B" else f"{size}{unit}"
        size /= 1024
    return f"{size:.1f}TB"


def _run_git(args: list[str], cwd: str | None = None) -> tuple[bool, str]:
    """Run a git command and return (success, output)."""
    try:
        result = subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True,
            cwd=cwd or _tool_working_dir.get() or os.getcwd(),
            timeout=30,
        )
        output = result.stdout
        if result.stderr:
            output += ("\n" if output else "") + result.stderr
        return result.returncode == 0, output.strip()
    except FileNotFoundError:
        return False, "❌ git is not installed or not in PATH"
    except subprocess.TimeoutExpired:
        return False, "⏰ Git command timed out"
    except (LookupError, OSError, RuntimeError, TypeError, ValueError) as e:
        return False, f"❌ Git error: {e}"


def _strip_html(html_text: str) -> str:
    """Very simple HTML-to-text converter."""
    # Remove script and style elements
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html_text, flags=re.DOTALL | re.IGNORECASE)
    # Remove HTML tags
    text = re.sub(r"<[^>]+>", " ", text)
    # Decode HTML entities
    text = html.unescape(text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    # Trim lines
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(line for line in lines if line)
    return text.strip()


def _resolve_path(path_str: str) -> Path:
    """Resolve a path through the active immutable RunContext.

    Tool operations fail closed when a path escapes the workspace or an
    explicitly granted additional root.  A context-free fallback is retained
    only for direct library use and tests outside an Agent run.
    """
    from nexus.run_context import get_run_context

    run_ctx = get_run_context()
    if run_ctx is not None:
        return run_ctx.resolve_path(path_str or ".")

    cwd = _tool_working_dir.get()
    base_dir = Path(cwd).resolve() if cwd else Path.cwd().resolve()
    if not path_str:
        return base_dir
    p = Path(str(path_str).strip()).expanduser()
    resolved = p.resolve() if p.is_absolute() else (base_dir / p).resolve()
    if cwd is None:
        # Direct library calls have no run authority; preserve the historical
        # utility API. Agent executions always install a RunContext above.
        return resolved
    try:
        resolved.relative_to(base_dir)
    except ValueError:
        raise ValueError(f"Path {resolved} is outside the active tool workspace {base_dir}") from None
    return resolved


# ── Tool implementations ────────────────────────────────────────────────────


def tool_read_file(path: str, start_line: int | None = None, end_line: int | None = None) -> ToolResult:
    """Read file contents with line numbers."""
    try:
        p = _resolve_path(path)
        if not p.exists():
            return ToolResult(status=ToolStatus.FAILURE, output=f"❌ File not found: {path}", error="File not found")
        if not p.is_file():
            return ToolResult(status=ToolStatus.FAILURE, output=f"❌ Not a file: {path}", error="Not a file")
        if p.stat().st_size > 2 * 1024 * 1024:
            return ToolResult(status=ToolStatus.FAILURE, output=f"❌ File too large ({_format_size(p.stat().st_size)}). Use search_code instead.", error="File too large")

        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        if start_line is not None:
            try:
                start_line = int(start_line)
            except (ValueError, TypeError):
                start_line = None
        if end_line is not None:
            try:
                end_line = int(end_line)
            except (ValueError, TypeError):
                end_line = None

        start = max(1, start_line or 1)
        end = min(len(lines), end_line or len(lines))

        numbered = []
        for i in range(start - 1, end):
            numbered.append(f"{i + 1:>5} │ {lines[i].rstrip()}")

        header = f"📄 {p.name}  ({len(lines)} lines, {_format_size(p.stat().st_size)})"
        if start_line or end_line:
            header += f"  [showing lines {start}-{end}]"

        return ToolResult(status=ToolStatus.SUCCESS, output=header + "\n" + "\n".join(numbered))
    except (LookupError, OSError, TypeError, ValueError) as e:
        return ToolResult(status=ToolStatus.FAILURE, output=f"❌ Error reading file: {e}", error=str(e))


def tool_write_file(path: str, content: str) -> str:
    """Write content to a file, creating directories as needed. Tracked for undo."""
    try:
        from nexus.mutation import MutationController
        p = _resolve_path(path)

        # Snapshot before writing
        history = get_history()
        snapshot = history.snapshot_before_write(str(p))

        mutator = MutationController(p.parent if p.parent.exists() else p.parent.parent)
        res = mutator.write_file(p, content)
        if not res.success:
            return f"❌ Error writing file: {res.error}"

        # Record the change
        history.record_change(str(p), "write_file", snapshot)

        line_count = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        return f"✅ Wrote {line_count} lines to {p}\nDiff:\n{res.diff}" if res.diff else f"✅ Wrote {line_count} lines to {p}"
    except (OSError, TypeError, ValueError) as e:
        return f"❌ Error writing file: {e}"


def tool_edit_file(path: str, old_text: str, new_text: str) -> str:
    """Replace old_text with new_text in a file. Tracked for undo."""
    try:
        p = _resolve_path(path)
        if not p.exists():
            return f"❌ File not found: {path}"

        with open(p, "r", encoding="utf-8") as f:
            content = f.read()

        # 1. Exact match
        count = content.count(old_text)
        target_old = old_text

        # 2. Whitespace-insensitive fallback match if exact fails
        if count == 0:
            content_lines = content.splitlines(keepends=True)
            old_lines = old_text.splitlines()

            if old_lines:
                clean_old = [line.strip() for line in old_lines if line.strip()]
                matches = []
                for i in range(len(content_lines) - len(old_lines) + 1):
                    chunk = content_lines[i : i + len(old_lines)]
                    clean_chunk = [line.strip() for line in chunk if line.strip()]
                    if clean_chunk == clean_old:
                        matches.append((i, "".join(chunk)))

                if len(matches) == 1:
                    target_old = matches[0][1]
                    count = 1

        if count == 0:
            # Diagnostic feedback for LLM — fail clearly, never silently replace the whole file
            first_line = old_text.splitlines()[0] if old_text.strip() else old_text
            content_lines = content.splitlines()
            similar = [
                f"L{index + 1}: {line.strip()[:80]}"
                for index, line in enumerate(content_lines)
                if first_line.strip() in line.strip() or line.strip() in first_line.strip()
            ]
            hint = f"\nSimilar lines in {p.name}:\n" + "\n".join(similar[:5]) if similar else ""
            return f"❌ Text not found in {p.name}. Make sure old_text matches exactly.{hint}"

        if count > 1:
            return f"⚠️ Found {count} occurrences of old_text in {p.name}. Provide more surrounding lines to make old_text unique."

        # Snapshot before editing
        history = get_history()
        snapshot = history.snapshot_before_write(str(p))

        new_content = content.replace(target_old, new_text, 1)
        from nexus.mutation import MutationController
        mutator = MutationController(p.parent)
        res = mutator.write_file(p, new_content)
        if not res.success:
            return f"❌ Error editing file: {res.error}"

        history.record_change(str(p), "edit_file", snapshot)

        old_lc = old_text.count("\n") + 1
        new_lc = new_text.count("\n") + 1
        return f"✅ Edited {p.name}: replaced {old_lc} lines → {new_lc} lines\nDiff:\n{res.diff}"
    except (LookupError, OSError, TypeError, ValueError) as e:
        return f"❌ Error editing file: {e}"


def tool_patch_file(path: str, start_line: int, end_line: int, new_content: str) -> str:
    """Apply a line-range based edit. Tracked for undo."""
    try:
        p = _resolve_path(path)
        if not p.exists():
            return f"❌ File not found: {path}"

        with open(p, "r", encoding="utf-8") as f:
            lines = f.readlines()

        try:
            start_line = int(start_line)
            end_line = int(end_line)
        except (ValueError, TypeError):
            return "❌ start_line and end_line must be integers"

        if start_line < 1 or start_line > len(lines) + 1:
            return f"❌ start_line {start_line} out of range (file has {len(lines)} lines)"

        if end_line != 0 and end_line < start_line:
            return f"❌ end_line ({end_line}) must be >= start_line ({start_line}) or 0 for insert mode"

        # Snapshot
        history = get_history()
        snapshot = history.snapshot_before_write(str(p))

        new_lines = new_content.split("\n") if new_content else []
        # Ensure each new line ends with \n except possibly the last
        new_lines = [line + "\n" for line in new_lines]

        if end_line == 0:
            # Insert before start_line
            for i, line in enumerate(new_lines):
                lines.insert(start_line - 1 + i, line)
        else:
            # Replace lines[start_line-1 : end_line] with new_lines
            end_line = min(end_line, len(lines))
            lines[start_line - 1 : end_line] = new_lines

        new_content_final = "".join(lines)

        from nexus.mutation import MutationController
        mutator = MutationController(p.parent)
        res = mutator.write_file(p, new_content_final)
        if not res.success:
            return f"❌ Error patching file: {res.error}"

        history.record_change(str(p), "patch_file", snapshot)
        return f"✅ Patched {p.name}: lines {start_line}-{end_line} → {len(new_lines)} new lines\nDiff:\n{res.diff}"
    except (LookupError, OSError, TypeError, ValueError) as e:
        return f"❌ Error patching file: {e}"


def tool_multi_edit(edits: list[dict]) -> str:
    """Apply a transaction of exact, unique text replacements.

    Every final file body is computed in memory before disk is touched. Files
    are then written to sibling temporary files and committed with atomic
    ``os.replace`` operations. If any commit fails, all already-replaced files
    are restored from their captured byte-for-byte originals.
    """
    if not edits:
        return "📝 Multi-edit: no edits provided"

    history = get_history()
    originals: dict[Path, str] = {}
    final_bodies: dict[Path, str] = {}
    edit_counts: dict[Path, int] = {}

    # Build the complete transaction in memory. Multiple edits to one file are
    # applied sequentially against the result of the previous edit.
    for index, edit in enumerate(edits, start=1):
        path = edit.get("path", "")
        old_text = edit.get("old_text", "")
        new_text = edit.get("new_text", "")
        if not isinstance(path, str) or not path:
            return f"❌ Multi-edit aborted: edit #{index} is missing 'path'. No files changed."
        if not isinstance(old_text, str) or not isinstance(new_text, str):
            return (
                f"❌ Multi-edit aborted: edit #{index} old_text/new_text must be strings. "
                "No files changed."
            )
        try:
            target = _resolve_path(path)
        except (OSError, ValueError) as exc:
            return f"❌ Multi-edit aborted: edit #{index} path error — {exc}. No files changed."
        if not target.is_file():
            return f"❌ Multi-edit aborted: edit #{index} file not found: {path}. No files changed."

        if target not in originals:
            try:
                originals[target] = target.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                return f"❌ Multi-edit aborted: cannot read {path}: {exc}. No files changed."
            final_bodies[target] = originals[target]

        current = final_bodies[target]
        occurrences = current.count(old_text)
        if occurrences == 0:
            preview = old_text.splitlines()[0][:80] if old_text else "<empty>"
            return (
                f"❌ Multi-edit aborted: edit #{index} old_text not found in {target.name} "
                f"(starts with {preview!r}). No files changed."
            )
        if occurrences > 1:
            return (
                f"❌ Multi-edit aborted: edit #{index} matched {occurrences} locations in "
                f"{target.name}; provide more surrounding context. No files changed."
            )
        final_bodies[target] = current.replace(old_text, new_text, 1)
        edit_counts[target] = edit_counts.get(target, 0) + 1

    snapshots: dict[Path, str | None] = {}
    temp_paths: dict[Path, Path] = {}
    committed: list[Path] = []
    try:
        for target, body in final_bodies.items():
            snapshots[target] = history.snapshot_before_write(str(target))
            temp = target.with_name(f".{target.name}.nexus-{uuid.uuid4().hex}.tmp")
            with temp.open("w", encoding="utf-8", newline="") as handle:
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            try:
                os.chmod(temp, target.stat().st_mode)
            except OSError:
                pass
            temp_paths[target] = temp

        for target, temp in temp_paths.items():
            os.replace(temp, target)
            committed.append(target)

    except (OSError, UnicodeError) as exc:
        rollback_errors: list[str] = []
        for target in reversed(committed):
            try:
                restore = target.with_name(f".{target.name}.nexus-rollback-{uuid.uuid4().hex}.tmp")
                with restore.open("w", encoding="utf-8", newline="") as handle:
                    handle.write(originals[target])
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(restore, target)
            except OSError as rollback_exc:
                rollback_errors.append(f"{target}: {rollback_exc}")
        detail = f" Rollback errors: {'; '.join(rollback_errors)}" if rollback_errors else ""
        return f"❌ Multi-edit transaction failed: {exc}.{detail}"
    finally:
        for temp in temp_paths.values():
            temp.unlink(missing_ok=True)

    for target in final_bodies:
        history.record_change(
            str(target),
            "multi_edit",
            snapshots[target],
            description=f"transactional multi-edit ({edit_counts[target]} replacement(s))",
        )

    details = [
        f"  {path.name}: {edit_counts[path]} replacement(s)"
        for path in sorted(final_bodies, key=lambda item: str(item))
    ]
    return (
        f"✅ 📝 Multi-edit committed atomically: {len(edits)} edit(s) across "
        f"{len(final_bodies)} file(s)\n" + "\n".join(details)
    )


def tool_file_info(path: str) -> str:
    """Get file/directory metadata."""
    try:
        p = _resolve_path(path)
        if not p.exists():
            return f"❌ Path not found: {path}"

        stat = p.stat()
        info = [f"📋 {p.name}"]
        info.append(f"  Type:      {'directory' if p.is_dir() else 'file'}")
        info.append(f"  Path:      {p}")
        info.append(f"  Size:      {_format_size(stat.st_size)}")
        info.append(
            f"  Modified:  {datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')}"
        )
        info.append(f"  Perms:     {oct(stat.st_mode)[-3:]}")

        if p.is_file():
            mime = mimetypes.guess_type(str(p))[0] or "unknown"
            info.append(f"  MIME:      {mime}")
            # Count lines for text files
            if stat.st_size < 5 * 1024 * 1024:
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        line_count = sum(1 for _ in f)
                    info.append(f"  Lines:     {line_count}")
                except (UnicodeDecodeError, OSError):
                    info.append("  Lines:     (binary file)")
            # MD5 for small files
            if stat.st_size < 1024 * 1024:
                h = hashlib.md5(p.read_bytes()).hexdigest()
                info.append(f"  MD5:       {h}")

        return "\n".join(info)
    except (OSError, TypeError, ValueError) as e:
        return f"❌ Error getting file info: {e}"


def tool_diff_files(file_a: str, file_b: str) -> str:
    """Show unified diff between two files."""
    try:
        import difflib

        pa = _resolve_path(file_a)
        pb = _resolve_path(file_b)

        if not pa.exists():
            return f"❌ File not found: {file_a}"
        if not pb.exists():
            return f"❌ File not found: {file_b}"

        with open(pa, "r", encoding="utf-8", errors="replace") as f:
            lines_a = f.readlines()
        with open(pb, "r", encoding="utf-8", errors="replace") as f:
            lines_b = f.readlines()

        diff = difflib.unified_diff(
            lines_a,
            lines_b,
            fromfile=str(pa.name),
            tofile=str(pb.name),
        )
        result = "".join(diff)
        if not result:
            return f"✅ Files are identical: {pa.name} == {pb.name}"
        return f"📝 Diff: {pa.name} vs {pb.name}\n{result}"
    except (ImportError, OSError, TypeError, ValueError) as e:
        return f"❌ Error diffing files: {e}"


def tool_run_command(
    command: str,
    cwd: str | None = None,
    timeout: float | int | str = 120,
    network: bool = False,
    require_os_isolation: bool = True,
    allow_unisolated_host_process: bool = False,
) -> str:
    """Execute a reviewed compatibility shell command through SandboxRunner."""
    try:
        from nexus.sandbox import SandboxRunner

        cwd_path = _resolve_path(cwd) if cwd else _resolve_path("")
        work_dir = str(cwd_path)
        if timeout is not None:
            try:
                timeout = float(timeout)
            except (ValueError, TypeError):
                timeout = 120.0
        result = SandboxRunner(work_dir).run_shell(
            command,
            cwd=work_dir,
            timeout_seconds=timeout,
            network=network,
            require_os_isolation=require_os_isolation,
            allow_unisolated_host_process=allow_unisolated_host_process,
        )
        return result.format_tool_output()
    except (OSError, RuntimeError, TypeError, ValueError) as e:
        return f"❌ Error running command: {e}"


def tool_run_process(
    argv: list[str],
    cwd: str | None = None,
    timeout: float | int | str = 120,
    network: bool = False,
    require_os_isolation: bool = True,
    allow_unisolated_host_process: bool = False,
) -> str:
    """Execute an argv vector without a shell."""
    try:
        from nexus.sandbox import CommandSpec, SandboxRunner

        cwd_path = _resolve_path(cwd) if cwd else _resolve_path("")
        work_dir = str(cwd_path)
        spec = CommandSpec.create(
            argv,
            work_dir,
            timeout_seconds=float(timeout),
            network=network,
            require_os_isolation=require_os_isolation,
            allow_unisolated_host_process=allow_unisolated_host_process,
        )
        return SandboxRunner(work_dir).run(spec).format_tool_output()
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return f"❌ Error running typed process: {exc}"


# Background process tools delegate to the dedicated supervisor so the canonical
# tool runtime stays below its architecture complexity ceiling.
from nexus.tools import background as _background_processes

_bg_processes = _background_processes._bg_processes
_watch_background_process = _background_processes._watch_background_process


def tool_process_run(
    command: str,
    cwd: str | None = None,
    network: bool = False,
    require_os_isolation: bool = True,
    allow_unisolated_host_process: bool = False,
    timeout: float | int | str = 3600,
    max_output_bytes: int = 1_000_000,
) -> str:
    try:
        work_dir = str(_resolve_path(cwd) if cwd else _resolve_path(""))
    except (LookupError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return f"❌ Error starting background process: {exc}"
    return _background_processes.start_background_process(
        command, work_dir, owner=_tool_owner.get(), network=network,
        require_os_isolation=require_os_isolation,
        allow_unisolated_host_process=allow_unisolated_host_process,
        timeout=timeout, max_output_bytes=max_output_bytes,
    )


def tool_process_status(pid: int) -> str:
    return _background_processes.background_process_status(pid)


def tool_process_stop(pid: int) -> str:
    return _background_processes.stop_background_process(pid)


def stop_owned_processes(owner: str) -> dict[str, object]:
    return _background_processes.stop_owned_processes(owner)


def stop_all_background_processes() -> dict[str, object]:
    return _background_processes.stop_all_background_processes()


atexit.register(stop_all_background_processes)


def tool_search_code(
    pattern: str, directory: str | None = None, file_pattern: str | None = None
) -> str:
    """Search for a pattern across files."""
    try:
        search_dir = _resolve_path(directory or _tool_working_dir.get() or os.getcwd())
        if not search_dir.is_dir():
            return f"❌ Not a directory: {search_dir}"

        matches = []
        max_matches = 50
        regex = re.compile(pattern, re.IGNORECASE)

        for root, dirs, files in os.walk(search_dir):
            # Filter ignored dirs
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith(".")]

            for fname in files:
                if file_pattern and not fnmatch.fnmatch(fname, file_pattern):
                    continue
                fpath = Path(root) / fname
                if _should_ignore(fpath):
                    continue

                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        for i, line in enumerate(f, 1):
                            if regex.search(line):
                                rel = fpath.relative_to(search_dir)
                                matches.append(f"  {rel}:{i}  │ {line.rstrip()}")
                                if len(matches) >= max_matches:
                                    break
                except (OSError, UnicodeDecodeError):
                    continue

                if len(matches) >= max_matches:
                    break
            if len(matches) >= max_matches:
                break

        if not matches:
            return f"🔍 No matches for /{pattern}/ in {search_dir}"

        header = f"🔍 Found {len(matches)} matches for /{pattern}/:"
        if len(matches) == max_matches:
            header += f" (capped at {max_matches})"
        return header + "\n" + "\n".join(matches)
    except (OSError, TypeError, ValueError) as e:
        return f"❌ Error searching: {e}"


def tool_list_directory(
    path: str | None = None, recursive: bool = False, max_depth: int = 3
) -> str:
    """List directory contents."""
    try:
        dir_path = _resolve_path(path or _tool_working_dir.get() or os.getcwd())
        if not dir_path.is_dir():
            return f"❌ Not a directory: {dir_path}"

        items = []

        def _list(p: Path, depth: int, prefix: str = ""):
            if depth > max_depth:
                return
            try:
                entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
            except PermissionError:
                return

            for entry in entries:
                if _should_ignore(entry):
                    continue
                if entry.is_dir():
                    items.append(f"{prefix}📁 {entry.name}/")
                    if recursive:
                        _list(entry, depth + 1, prefix + "   ")
                else:
                    size = _format_size(entry.stat().st_size)
                    items.append(f"{prefix}📄 {entry.name}  ({size})")

        _list(dir_path, 0)

        if not items:
            return f"📁 {dir_path} (empty)"
        return f"📁 {dir_path}\n" + "\n".join(items)
    except (OSError, TypeError, ValueError) as e:
        return f"❌ Error listing directory: {e}"


def tool_find_files(pattern: str, directory: str | None = None) -> str:
    """Find files matching a glob pattern."""
    try:
        search_dir = _resolve_path(directory or _tool_working_dir.get() or os.getcwd())
        matches = []
        for p in search_dir.rglob(pattern):
            if any(part in IGNORE_DIRS for part in p.parts):
                continue
            if _should_ignore(p):
                continue
            rel = p.relative_to(search_dir)
            if p.is_file():
                matches.append(f"  📄 {rel}  ({_format_size(p.stat().st_size)})")
            else:
                matches.append(f"  📁 {rel}/")
            if len(matches) >= 100:
                break

        if not matches:
            return f"🔍 No files matching '{pattern}' in {search_dir}"
        return f"🔍 Found {len(matches)} matches for '{pattern}':\n" + "\n".join(matches)
    except (LookupError, OSError, RuntimeError, TypeError, ValueError) as e:
        return f"❌ Error finding files: {e}"


def tool_get_project_structure(path: str | None = None, max_depth: int = 4) -> str:
    """Generate a tree view of the project."""
    try:
        root = _resolve_path(path or _tool_working_dir.get() or os.getcwd())
        lines = [f"🌳 {root.name}/"]

        def _tree(p: Path, prefix: str, depth: int):
            if depth >= max_depth:
                return
            try:
                entries = sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower()))
            except PermissionError:
                return

            # Filter
            entries = [e for e in entries if not _should_ignore(e)]
            for i, entry in enumerate(entries):
                is_last = i == len(entries) - 1
                connector = "└── " if is_last else "├── "
                if entry.is_dir():
                    lines.append(f"{prefix}{connector}{entry.name}/")
                    ext = "    " if is_last else "│   "
                    _tree(entry, prefix + ext, depth + 1)
                else:
                    lines.append(f"{prefix}{connector}{entry.name}")

        _tree(root, "", 0)
        return "\n".join(lines)
    except (LookupError, OSError, TypeError, ValueError) as e:
        return f"❌ Error getting structure: {e}"


def tool_repo_index(force: bool = False) -> str:
    """Build the persistent repository graph for the active working directory."""
    try:
        from nexus.repo_graph import RepoGraph

        graph = RepoGraph(_tool_working_dir.get() or os.getcwd())
        stats = graph.build(force=bool(force))
        return "🧭 Repository graph refreshed\n" + json.dumps(
            {
                "stats": {
                    "scanned": stats.scanned,
                    "indexed": stats.indexed,
                    "reused": stats.reused,
                    "removed": stats.removed,
                    "parse_errors": stats.parse_errors,
                },
                "graph": graph.summary(),
            },
            indent=2,
        )
    except (ImportError, LookupError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return f"❌ Repository graph indexing failed: {exc}"


def tool_repo_symbols(
    query: str,
    include_callers: bool = True,
    limit: int = 50,
) -> str:
    """Find declarations and callers in the active repository graph."""
    try:
        from nexus.repo_graph import RepoGraph

        graph = RepoGraph(_tool_working_dir.get() or os.getcwd())
        graph.build()
        declarations = [asdict(item) for item in graph.find_symbols(query, limit=limit)]
        callers = graph.find_callers(query, limit=limit) if include_callers else []
        return "🧭 Repository symbol lookup\n" + json.dumps(
            {
                "query": query,
                "declarations": declarations,
                "callers": callers,
            },
            indent=2,
        )
    except (ImportError, LookupError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return f"❌ Repository symbol lookup failed: {exc}"


def tool_repo_impact(paths: list[str]) -> str:
    """Find imports, reverse importers, and tests affected by changed files."""
    try:
        from nexus.repo_graph import RepoGraph

        graph = RepoGraph(_tool_working_dir.get() or os.getcwd())
        graph.build()
        dependencies = {str(path): graph.dependencies(path) for path in paths}
        return "🧭 Repository impact analysis\n" + json.dumps(
            {
                "paths": paths,
                "dependencies": dependencies,
                "impacted_tests": graph.impacted_tests(paths),
            },
            indent=2,
        )
    except (ImportError, LookupError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return f"❌ Repository impact analysis failed: {exc}"


def _built_graph():
    from nexus.repo_graph import RepoGraph

    graph = RepoGraph(_tool_working_dir.get() or os.getcwd())
    graph.build()
    return graph


def tool_repo_context(query: str, limit: int = 40) -> str:
    """Return relevance-ranked repository context."""
    try:
        graph = _built_graph()
        return json.dumps(
            {
                "query": query,
                "results": graph.relevant_files(query, limit=max(1, int(limit))),
                "frameworks": graph.frameworks(),
                "summary": graph.summary(),
            },
            indent=2,
        )
    except (TypeError, ValueError) as exc:
        return f"❌ Repository context selection failed: {exc}"


def tool_repo_routes(query: str = "") -> str:
    """Return indexed routes."""
    try:
        return json.dumps(_built_graph().routes(query), indent=2)
    except (TypeError, ValueError) as exc:
        return f"❌ Repository route discovery failed: {exc}"


def tool_repo_models(query: str = "") -> str:
    """Return indexed database models."""
    try:
        return json.dumps(_built_graph().models(query), indent=2)
    except (TypeError, ValueError) as exc:
        return f"❌ Repository model discovery failed: {exc}"


_language_service_pools: dict[str, object] = {}


def tool_repo_navigate(
    path: str,
    language: str,
    operation: str,
    line: int = 0,
    character: int = 0,
) -> str:
    """Navigate symbols through LSP with deterministic fallbacks."""
    from nexus.language_intelligence import (
        LanguageServicePool,
        LSPError,
        TreeSitterAdapter,
    )

    root = str(Path(_tool_working_dir.get() or os.getcwd()).resolve())
    pool = _language_service_pools.get(root)
    if pool is None:
        pool = LanguageServicePool(root)
        _language_service_pools[root] = pool
    try:
        client = pool.client(language)
        if operation == "symbols":
            result = client.document_symbols(path)
        elif operation == "definition":
            result = client.definition(path, int(line), int(character))
        elif operation == "references":
            result = client.references(path, int(line), int(character))
        else:
            return f"❌ Unsupported navigation operation: {operation}"
        return json.dumps(
            {"engine": "lsp", "operation": operation, "result": result},
            indent=2,
        )
    except LSPError as lsp_error:
        if operation != "symbols":
            return json.dumps(
                {
                    "engine": "unavailable",
                    "operation": operation,
                    "error": str(lsp_error),
                    "guidance": (f"Install a {language} language server for precise {operation}."),
                },
                indent=2,
            )
        target = _resolve_path(path)
        try:
            source = target.read_text(encoding="utf-8")
            adapter = TreeSitterAdapter()
            if adapter.available:
                return json.dumps(
                    {
                        "engine": "tree-sitter",
                        "operation": "symbols",
                        "result": adapter.symbols(source, language),
                    },
                    indent=2,
                )
        except (OSError, UnicodeDecodeError, LSPError):
            pass
        graph = _built_graph()
        relative = target.resolve().relative_to(Path(root)).as_posix()
        record = graph.files.get(relative)
        return json.dumps(
            {
                "engine": "repograph",
                "operation": "symbols",
                "lsp_error": str(lsp_error),
                "result": [asdict(item) for item in record.symbols] if record else [],
            },
            indent=2,
        )


def tool_api_check(
    url: str,
    method: str = "GET",
    expected_status: int = 200,
    expected_json: dict | None = None,
    expected_text: str = "",
    json_body=None,
    allow_external: bool = False,
) -> str:
    """Verify an HTTP API contract."""
    from nexus.behavioral import ApiProbeSpec, ApiVerifier

    result = ApiVerifier().verify(
        ApiProbeSpec(
            method=method,
            url=url,
            expected_status=int(expected_status),
            expected_json=expected_json,
            expected_text=expected_text,
            json_body=json_body,
            allow_external=allow_external,
        )
    )
    return json.dumps(result.to_dict(), indent=2)


def tool_database_check(path: str = "", sql: str = "") -> str:
    """Run read-only SQLite verification or inspect migration SQL."""
    from nexus.behavioral import DatabaseVerifier

    verifier = DatabaseVerifier()
    if sql:
        findings = verifier.migration_risks(sql)
        return json.dumps(
            {
                "kind": "database_migration",
                "status": "failed" if findings else "passed",
                "summary": (
                    f"{len(findings)} destructive migration operation(s) require approval"
                    if findings
                    else "No deterministic destructive migration pattern detected"
                ),
                "evidence": {"findings": findings, "executed": False},
            },
            indent=2,
        )
    if not path:
        return "❌ database_check requires path or sql"
    result = verifier.verify_sqlite(_resolve_path(path))
    return json.dumps(result.to_dict(), indent=2)


def tool_security_scan(paths: list[str] | None = None) -> str:
    """Run deterministic security checks."""
    from nexus.behavioral import SecurityScanner

    result = SecurityScanner().scan(_tool_working_dir.get() or os.getcwd(), paths)
    return json.dumps(result.to_dict(), indent=2)


def tool_browser_check(
    url: str,
    steps: list[dict] | None = None,
    screenshot_path: str = "",
    allow_external: bool = False,
) -> str:
    """Run an optional Playwright workflow."""
    from nexus.behavioral import BrowserProbeSpec, BrowserStep, BrowserVerifier

    parsed_steps = tuple(
        BrowserStep(
            action=str(item.get("action", "")),
            selector=str(item.get("selector", "")),
            value=str(item.get("value", "")),
        )
        for item in (steps or [])
    )
    result = BrowserVerifier().verify(
        BrowserProbeSpec(
            url=url,
            steps=parsed_steps,
            screenshot_path=screenshot_path,
            allow_external=allow_external,
        )
    )
    return json.dumps(result.to_dict(), indent=2)


# ─── GIT TOOL IMPLEMENTATIONS ───────────────────────────────────────────


def tool_git_status(cwd: str | None = None) -> str:
    """Show comprehensive git status."""
    work_dir = str(_resolve_path(cwd or _tool_working_dir.get() or os.getcwd()))

    # Get branch
    ok, branch = _run_git(["branch", "--show-current"], work_dir)
    if not ok:
        return f"❌ Not a git repository or git error: {branch}"

    # Get status
    _, status = _run_git(["status", "--porcelain", "-b"], work_dir)

    # Get commit count
    _, log_count = _run_git(["rev-list", "--count", "HEAD"], work_dir)

    # Get remote
    _, remote = _run_git(["remote", "-v"], work_dir)

    # Parse status
    staged, modified, untracked, deleted = [], [], [], []
    for line in status.split("\n"):
        if not line or line.startswith("##"):
            continue
        x, y = line[0], line[1]
        fname = line[3:].strip()
        if x in "MADRC":
            staged.append(f"    {x} {fname}")
        if y == "M":
            modified.append(f"    M {fname}")
        elif y == "D":
            deleted.append(f"    D {fname}")
        elif y == "?":
            untracked.append(f"    ? {fname}")

    parts = [f"🌿 Branch: {branch or '(detached HEAD)'}"]
    if log_count.strip().isdigit():
        parts.append(f"📊 Commits: {log_count.strip()}")
    if remote:
        remote_line = remote.split("\n")[0] if remote else ""
        parts.append(f"🔗 Remote: {remote_line}")

    if staged:
        parts.append(f"\n✅ Staged ({len(staged)}):")
        parts.extend(staged)
    if modified:
        parts.append(f"\n📝 Modified ({len(modified)}):")
        parts.extend(modified)
    if deleted:
        parts.append(f"\n🗑️  Deleted ({len(deleted)}):")
        parts.extend(deleted)
    if untracked:
        parts.append(f"\n❓ Untracked ({len(untracked)}):")
        parts.extend(untracked)
    if not staged and not modified and not untracked and not deleted:
        parts.append("\n✨ Working tree clean")

    return "\n".join(parts)


def tool_git_diff(
    target: str | None = None,
    staged: bool = False,
    file_path: str | None = None,
    cwd: str | None = None,
) -> str:
    """Show git diffs."""
    work_dir = str(_resolve_path(cwd or _tool_working_dir.get() or os.getcwd()))
    args = ["diff"]
    if staged:
        args.append("--cached")
    if target:
        args.append(target)
    if file_path:
        args.extend(["--", file_path])

    args.extend(["--stat"])
    ok, stat_output = _run_git(args, work_dir)

    # Also get the full diff
    full_args = ["diff"]
    if staged:
        full_args.append("--cached")
    if target:
        full_args.append(target)
    if file_path:
        full_args.extend(["--", file_path])

    _, full_diff = _run_git(full_args, work_dir)

    if not full_diff and not stat_output:
        ctx = "staged" if staged else "unstaged"
        return f"✨ No {ctx} changes{f' in {file_path}' if file_path else ''}"

    # Truncate if massive
    if len(full_diff) > 15000:
        full_diff = (
            full_diff[:7000]
            + f"\n\n... ({len(full_diff) - 14000} chars truncated) ...\n\n"
            + full_diff[-7000:]
        )

    parts = []
    if stat_output:
        parts.append(f"📊 Summary:\n{stat_output}")
    parts.append(f"\n{full_diff}")
    return "\n".join(parts)


def tool_git_commit(
    message: str,
    files: list[str] | None = None,
    all: bool = False,
    cwd: str | None = None,
) -> str:
    """Stage and commit."""
    work_dir = str(_resolve_path(cwd or _tool_working_dir.get() or os.getcwd()))
    # Stage files
    if all:
        ok, out = _run_git(["add", "-A"], work_dir)
        if not ok:
            return f"❌ Failed to stage files: {out}"
    elif files:
        ok, out = _run_git(["add"] + files, work_dir)
        if not ok:
            return f"❌ Failed to stage files: {out}"

    # Commit
    ok, out = _run_git(["commit", "-m", message], work_dir)
    if not ok:
        if "nothing to commit" in out:
            return "ℹ️  Nothing to commit. Stage changes first with git_commit(all=true) or specify files."
        return f"❌ Commit failed: {out}"

    # Get the short hash
    _, hash_out = _run_git(["rev-parse", "--short", "HEAD"], work_dir)

    return f"✅ Committed: [{hash_out.strip()}] {message}\n{out}"


def tool_git_log(
    count: int = 15,
    oneline: bool = True,
    file_path: str | None = None,
    cwd: str | None = None,
) -> str:
    """View commit history."""
    work_dir = str(_resolve_path(cwd or _tool_working_dir.get() or os.getcwd()))
    args = ["log", f"-{count}"]
    if oneline:
        args.append("--oneline")
    else:
        args.extend(["--format=%h %an %ad %s", "--date=short"])
    if file_path:
        args.extend(["--", file_path])

    ok, output = _run_git(args, work_dir)
    if not ok:
        return f"❌ Git log failed: {output}"

    return f"📜 Recent commits:\n{output}" if output else "📜 No commits yet"


def tool_git_branch(
    action: str = "list",
    name: str | None = None,
    cwd: str | None = None,
) -> str:
    """Manage branches."""
    work_dir = str(_resolve_path(cwd or _tool_working_dir.get() or os.getcwd()))
    if action == "list":
        ok, output = _run_git(["branch", "-a"], work_dir)
        return f"🌿 Branches:\n{output}" if ok else f"❌ {output}"

    if not name:
        return f"❌ Branch name required for '{action}'"

    if action == "create":
        ok, output = _run_git(["checkout", "-b", name], work_dir)
        return f"✅ Created and switched to branch: {name}" if ok else f"❌ {output}"

    elif action == "switch":
        ok, output = _run_git(["checkout", name], work_dir)
        return f"✅ Switched to branch: {name}" if ok else f"❌ {output}"

    elif action == "delete":
        ok, output = _run_git(["branch", "-d", name], work_dir)
        return f"✅ Deleted branch: {name}" if ok else f"❌ {output}"

    return f"❌ Unknown action: {action}. Use list/create/switch/delete."


# ─── WEB TOOL IMPLEMENTATIONS ───────────────────────────────────────────


class _PolicyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Revalidate every redirect before urllib follows it."""

    def __init__(self, policy, original_url: str):
        super().__init__()
        self.policy = policy
        self.original_url = original_url

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        destination = urllib.parse.urljoin(req.full_url, newurl)
        violation = self.policy.check_redirect(self.original_url, destination)
        if violation:
            raise urllib.error.URLError(violation.reason)
        return super().redirect_request(req, fp, code, msg, headers, destination)


def _safe_urlopen(request: urllib.request.Request, *, timeout: float, policy):
    """Open HTTP(S) on an address validated by ``NetworkPolicy``.

    Connecting to the validated IP, while retaining the original Host header
    and TLS server name, removes the DNS-rebinding gap between validation and
    urllib's connection-time DNS lookup.
    """
    original_url = request.full_url
    current_url = original_url
    for _redirect in range(6):
        violation, target = policy.resolve_url(current_url)
        if violation or target is None:
            reason = violation.reason if violation else "URL could not be resolved safely"
            raise urllib.error.URLError(reason)
        parsed = urllib.parse.urlparse(current_url)
        connection_type = (
            _PinnedHTTPSConnection if parsed.scheme.lower() == "https" else _PinnedHTTPConnection
        )
        connection = connection_type(
            target.hostname,
            target.addresses[0],
            target.port,
            timeout=timeout,
        )
        headers = dict(request.header_items())
        default_port = 443 if parsed.scheme.lower() == "https" else 80
        headers.setdefault(
            "Host",
            target.hostname if target.port == default_port else f"{target.hostname}:{target.port}",
        )
        path = urllib.parse.urlunparse(("", "", parsed.path or "/", "", parsed.query, ""))
        try:
            connection.request(request.get_method(), path, body=request.data, headers=headers)
            response = connection.getresponse()
        except BaseException:
            connection.close()
            raise
        if response.status in {301, 302, 303, 307, 308}:
            location = response.headers.get("Location", "")
            response.close()
            connection.close()
            if not location:
                raise urllib.error.URLError("Redirect response did not include a Location header")
            destination = urllib.parse.urljoin(current_url, location)
            redirect_violation = policy.check_url_syntax(destination)
            if redirect_violation:
                raise urllib.error.URLError(
                    f"Redirect from {original_url} leads to blocked destination: "
                    f"{redirect_violation.reason}"
                )
            current_url = destination
            continue
        wrapped = _PinnedResponse(response, connection, current_url)
        if response.status >= 400:
            raise urllib.error.HTTPError(
                current_url,
                response.status,
                response.reason,
                response.headers,
                wrapped,
            )
        return wrapped
    raise urllib.error.URLError("Too many redirects (maximum 5)")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    """HTTP connection whose socket destination is a pre-validated IP."""

    def __init__(self, hostname: str, address: str, port: int, *, timeout: float):
        super().__init__(hostname, port=port, timeout=timeout)
        self._pinned_address = address

    def connect(self):
        self.sock = self._create_connection(
            (self._pinned_address, self.port),
            self.timeout,
            self.source_address,
        )
        if self._tunnel_host:
            self._tunnel()


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection pinned to an IP while validating the original hostname."""

    def __init__(self, hostname: str, address: str, port: int, *, timeout: float):
        super().__init__(
            hostname,
            port=port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._pinned_address = address

    def connect(self):
        self.sock = socket.create_connection(
            (self._pinned_address, self.port),
            self.timeout,
            self.source_address,
        )
        server_hostname = self.host
        if self._tunnel_host:
            self._tunnel()
            server_hostname = self._tunnel_host
        self.sock = self._context.wrap_socket(self.sock, server_hostname=server_hostname)


class _PinnedResponse:
    """Context-managed adapter retaining the final URL and owning connection."""

    def __init__(self, response, connection, url: str):
        self._response = response
        self._connection = connection
        self._url = url
        self.headers = response.headers

    def read(self, amount: int | None = None):
        return self._response.read(amount)

    def geturl(self) -> str:
        return self._url

    def close(self) -> None:
        try:
            self._response.close()
        finally:
            self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        self.close()


def tool_web_fetch(url: str, max_length: int = 10000) -> str:
    """Fetch public HTTP(S) text while blocking SSRF and unsafe redirects."""
    from nexus.network_policy import NetworkPolicy, network_globally_disabled

    if not isinstance(url, str) or not url.strip() or len(url) > 4096:
        return "❌ Network policy blocked URL: URL must contain 1-4096 characters"
    policy = NetworkPolicy(max_response_bytes=500_000)
    # Full resolution happens inside _safe_urlopen immediately before the
    # socket is pinned. Syntax validation here keeps mocked/offline tests free
    # of accidental DNS calls.
    violation = policy.check_url_syntax(url)
    if violation:
        return f"❌ Network policy blocked URL ({violation.category}): {violation.reason}"
    if network_globally_disabled():
        return "❌ Network policy blocked URL (network_disabled): outbound network is disabled"
    try:
        max_length = max(1, min(int(max_length or 10000), 100_000))
    except (TypeError, ValueError):
        return "❌ max_length must be an integer between 1 and 100000"
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "NexusAI/3.1 (coding-agent)"},
        )
        with _safe_urlopen(req, timeout=15, policy=policy) as resp:
            final_url = getattr(resp, "geturl", lambda: url)()
            redirect_violation = policy.check_url_syntax(final_url)
            if final_url != url and redirect_violation:
                return (
                    "❌ Network policy blocked redirect "
                    f"({redirect_violation.category}): {redirect_violation.reason}"
                )
            content_type = resp.headers.get("Content-Type", "")
            type_violation = policy.check_content_type(content_type)
            if type_violation:
                return f"❌ Network policy blocked response: {type_violation.reason}"
            raw = resp.read(policy.max_response_bytes + 1)
            if len(raw) > policy.max_response_bytes:
                return (
                    "❌ Network policy blocked response: content exceeded "
                    f"{policy.max_response_bytes} bytes"
                )

        encoding = "utf-8"
        if "charset=" in content_type:
            encoding = content_type.split("charset=")[-1].split(";")[0].strip()
        text = raw.decode(encoding, errors="replace")
        if "html" in content_type.lower():
            text = _strip_html(text)
        if len(text) > max_length:
            text = text[:max_length] + f"\n\n... (truncated, {len(text)} total chars)"
        return f"🌐 Fetched {url}\n\n{text}"
    except urllib.error.HTTPError as exc:
        return f"❌ HTTP {exc.code}: {exc.reason} — {url}"
    except urllib.error.URLError as exc:
        return f"❌ URL error: {exc.reason} — {url}"
    except (OSError, ValueError) as exc:
        return f"❌ Error fetching URL: {exc}"


def tool_web_search(query: str, max_results: int = 5) -> str:
    """Search the web using DuckDuckGo HTML (no API key needed)."""
    from nexus.network_policy import NetworkPolicy, network_globally_disabled

    if not isinstance(query, str) or not query.strip() or len(query) > 500:
        return "❌ Search query must contain 1-500 characters"
    try:
        max_results = max(1, min(int(max_results or 5), 20))
    except (TypeError, ValueError):
        return "❌ max_results must be an integer between 1 and 20"
    if network_globally_disabled():
        return "❌ Network policy blocked search (network_disabled): outbound network is disabled"
    try:
        # Use DuckDuckGo HTML search
        encoded_query = urllib.parse.quote_plus(query)
        url = f"https://html.duckduckgo.com/html/?q={encoded_query}"
        policy = NetworkPolicy(
            allowed_hosts=frozenset({"html.duckduckgo.com"}),
            max_response_bytes=200_000,
        )
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "NexusAI/1.0"},
        )
        with _safe_urlopen(req, timeout=10, policy=policy) as resp:
            html_text = resp.read(policy.max_response_bytes).decode("utf-8", errors="replace")

        # Parse results (simple regex extraction)
        results = []
        # DuckDuckGo HTML has results in <a class="result__a"> tags
        result_pattern = re.compile(
            r'<a[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>.*?'
            r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
            re.DOTALL,
        )

        for match in result_pattern.finditer(html_text):
            if len(results) >= max_results:
                break
            link = match.group(1)
            title = _strip_html(match.group(2)).strip()
            snippet = _strip_html(match.group(3)).strip()

            # DuckDuckGo wraps links in a redirect, extract the actual URL
            if "uddg=" in link:
                actual_url = urllib.parse.unquote(link.split("uddg=")[-1].split("&")[0])
            else:
                actual_url = link

            if title and actual_url:
                results.append(
                    f"  {len(results) + 1}. {title}\n     {actual_url}\n     {snippet}\n"
                )

        if not results:
            return f"🔍 No results found for: {query}"

        return f"🔍 Search results for '{query}':\n\n" + "\n".join(results)
    except (LookupError, OSError, RuntimeError, TypeError, ValueError) as e:
        return f"❌ Search error: {e}"


# ── Dispatch ─────────────────────────────────────────────────────────────────

# ── GitHub Tools ─────────────────────────────────────────────────────────────


def tool_github_list_issues(limit: int = 10) -> str:
    try:
        from nexus.github import GitHubIntegration

        issues = GitHubIntegration.list_issues(limit=limit)
        if not issues:
            return "No open issues found."
        lines = []
        for i in issues:
            lines.append(f"#{i.get('number')} [{i.get('state')}] {i.get('title')}")
        return "\n".join(lines)
    except (ImportError, LookupError, OSError, RuntimeError, TypeError, ValueError) as e:
        return f"❌ GitHub Error: {e}"


def tool_github_view_issue(number: str) -> str:
    try:
        from nexus.github import GitHubIntegration

        issue = GitHubIntegration.view_issue(number)
        if not issue:
            return f"❌ Issue #{number} not found."
        comments = "\n".join(
            f"- {item.get('author', {}).get('login', 'unknown')}: {item.get('body', '')}"
            for item in issue.get("comments", [])
        )
        return (
            f"Issue #{issue.get('number')}: {issue.get('title')}\n"
            f"State: {issue.get('state')}\n"
            f"URL: {issue.get('url')}\n\n"
            f"{issue.get('body', '(no body)')}\n\n"
            f"Comments:\n{comments or '(none)'}"
        )
    except (ImportError, LookupError, OSError, RuntimeError, TypeError, ValueError) as e:
        return f"❌ GitHub Error: {e}"


def tool_generate_dashboard(input_path: str, output_path: str) -> str:
    try:
        from nexus.dashboard import RegressionDashboard

        RegressionDashboard.generate(input_path, output_path)
        return f"✅ Dashboard successfully generated at {output_path}"
    except ImportError as e:
        return f"❌ Failed to generate dashboard: {e}"


def tool_github_create_pr(title: str, body: str, base: str = "") -> str:
    try:
        from nexus.github import GitHubIntegration

        url = GitHubIntegration.create_pull_request(title, body, base)
        return f"✅ Pull request created successfully: {url}"
    except ImportError as e:
        return f"❌ GitHub Error: {e}"


def tool_read_notebook(path: str) -> str:
    """Read a Jupyter Notebook (.ipynb) and display its cells."""
    import json
    try:
        p = _resolve_path(path)
        if not p.exists():
            return f"❌ File not found: {path}"
        with open(p, "r", encoding="utf-8") as f:
            nb = json.load(f)
            
        cells = nb.get("cells", [])
        output = [f"Notebook: {p.name} ({len(cells)} cells)"]
        for i, cell in enumerate(cells):
            cell_type = cell.get("cell_type", "unknown")
            source = "".join(cell.get("source", []))
            output.append(f"--- Cell {i} [{cell_type}] ---")
            output.append(source.strip())
        return "\n".join(output)
    except Exception as e:
        return f"❌ Error reading notebook: {e}"


def tool_edit_notebook_cell(path: str, cell_index: int, new_source: str) -> str:
    """Edit a specific cell in a Jupyter Notebook."""
    import json
    try:
        p = _resolve_path(path)
        if not p.exists():
            return f"❌ File not found: {path}"
        with open(p, "r", encoding="utf-8") as f:
            nb = json.load(f)
            
        cells = nb.get("cells", [])
        if cell_index < 0 or cell_index >= len(cells):
            return f"❌ Invalid cell index {cell_index}. Notebook has {len(cells)} cells."
            
        cells[cell_index]["source"] = [line + "\n" for line in new_source.split("\n")]
        # Remove trailing newline from the last line if necessary
        if cells[cell_index]["source"]:
            cells[cell_index]["source"][-1] = cells[cell_index]["source"][-1].rstrip("\n")
            
        with open(p, "w", encoding="utf-8") as f:
            json.dump(nb, f, indent=1)
            
        return f"✅ Cell {cell_index} updated successfully."
    except Exception as e:
        return f"❌ Error editing notebook: {e}"


def tool_schedule_routine(interval: int, task: str) -> str:
    """Schedule a routine task to run in the background."""
    try:
        from nexus.routine import schedule_routine
        # Note: We need a way to pass the agent context. We will handle agent context injection in agent.py
        # For now, schedule_routine will just use the global RoutineOrchestrator.
        return schedule_routine(interval, task, agent=getattr(tool_schedule_routine, 'agent_instance', None))
    except ImportError as e:
        return f"❌ Routine Error: {e}"


def tool_message_peer(peer_name: str, message: str) -> str:
    """Message an active peer subagent."""
    try:
        from nexus.routine import message_peer
        return message_peer(peer_name, message)
    except ImportError as e:
        return f"❌ Peer Messaging Error: {e}"


TOOL_DISPATCH = {
    # File tools
    "read_file": tool_read_file,
    "write_file": tool_write_file,
    "edit_file": tool_edit_file,
    "patch_file": tool_patch_file,
    "multi_edit": tool_multi_edit,
    "read_notebook": tool_read_notebook,
    "edit_notebook_cell": tool_edit_notebook_cell,
    "file_info": tool_file_info,
    "diff_files": tool_diff_files,
    # Routine and Peer tools
    "schedule_routine": tool_schedule_routine,
    "message_peer": tool_message_peer,
    # Search tools
    "search_code": tool_search_code,
    "list_directory": tool_list_directory,
    "find_files": tool_find_files,
    "get_project_structure": tool_get_project_structure,
    "repo_index": tool_repo_index,
    "repo_symbols": tool_repo_symbols,
    "repo_impact": tool_repo_impact,
    "repo_context": tool_repo_context,
    "repo_routes": tool_repo_routes,
    "repo_models": tool_repo_models,
    "repo_navigate": tool_repo_navigate,
    # Shell tools
    "run_command": tool_run_command,
    "run_process": tool_run_process,
    "process_run": tool_process_run,
    "process_status": tool_process_status,
    "process_stop": tool_process_stop,
    # Git tools
    "git_status": tool_git_status,
    "git_diff": tool_git_diff,
    "git_commit": tool_git_commit,
    "git_log": tool_git_log,
    "git_branch": tool_git_branch,
    # Web tools
    "web_fetch": tool_web_fetch,
    "web_search": tool_web_search,
    # Behavioral verification tools
    "api_check": tool_api_check,
    "database_check": tool_database_check,
    "security_scan": tool_security_scan,
    "browser_check": tool_browser_check,
    "github_list_issues": tool_github_list_issues,
    "github_view_issue": tool_github_view_issue,
    "github_create_pr": tool_github_create_pr,
    "generate_dashboard": tool_generate_dashboard,
}


def normalize_tool_arguments(name: str, args: dict) -> dict:
    """Normalize common tool parameter name variations from LLMs."""
    args = dict(args)
    if name in ("read_file", "write_file", "edit_file", "patch_file", "file_info"):
        if "path" not in args:
            for alt in ("file_path", "file", "filename", "filepath", "target_file"):
                if alt in args:
                    args["path"] = args.pop(alt)
                    break
        if "path" in args and isinstance(args["path"], str):
            p = args["path"]
            import getpass

            curr_user = getpass.getuser()
            p = re.sub(
                r"^/Users/\[?(?:username|user|yourname|name)\]?/",
                f"/Users/{curr_user}/",
                p,
                flags=re.I,
            )
            p = re.sub(
                r"^/home/\[?(?:username|user|yourname|name)\]?/",
                f"/home/{curr_user}/",
                p,
                flags=re.I,
            )
            args["path"] = p
    elif name in ("run_command", "process_run"):
        if "command" not in args:
            for alt in ("cmd", "shell_command", "script", "exec"):
                if alt in args:
                    args["command"] = args.pop(alt)
                    break
    elif name == "run_process":
        if "argv" not in args:
            for alt in ("args", "command", "cmd"):
                if alt in args:
                    value = args.pop(alt)
                    args["argv"] = value if isinstance(value, list) else [str(value)]
                    break
    elif name == "search_code":
        if "pattern" not in args:
            for alt in ("query", "search_pattern", "regex", "term"):
                if alt in args:
                    args["pattern"] = args.pop(alt)
                    break
    elif name == "web_fetch":
        if "url" not in args:
            for alt in ("link", "uri", "target_url"):
                if alt in args:
                    args["url"] = args.pop(alt)
                    break
    elif name == "web_search":
        if "query" not in args:
            for alt in ("q", "search", "term", "keywords"):
                if alt in args:
                    args["query"] = args.pop(alt)
                    break
    # Runtime-only control-plane metadata is consumed by ToolExecutionController
    # and must never be forwarded to the underlying file handler.
    if name in {"write_file", "edit_file", "patch_file", "multi_edit"}:
        args.pop("scope_reason", None)
    return args


def _adapt_legacy_tool_output(output: str) -> ToolResult:
    """Convert legacy string-returning handlers at the registry boundary.

    The Agent and all external tool paths consume only ``ToolResult.status``.
    This adapter is intentionally centralized while legacy handlers are migrated
    incrementally, preventing scattered output-prefix checks in orchestration.
    """
    text = str(output)
    if text.startswith("❌ BLOCKED"):
        return ToolResult(status=ToolStatus.BLOCKED, output=text, error=text)
    if text.startswith("⏰"):
        return ToolResult(status=ToolStatus.TIMEOUT, output=text, error=text)
    if text.startswith("⏸️"):
        return ToolResult(status=ToolStatus.CANCELLED, output=text, error=text)
    if text.startswith("❌"):
        return ToolResult(status=ToolStatus.FAILURE, output=text, error=text)
    return ToolResult(status=ToolStatus.SUCCESS, output=text)


def execute_tool(name: str, arguments: dict, policy_engine=None) -> ToolResult:
    """Execute a tool by name with the given arguments.

    Args:
        name: Tool name.
        arguments: Tool arguments dictionary.
        policy_engine: Optional PolicyEngine instance. When provided, the action
            is evaluated before execution; a denied decision returns a BLOCKED result.
    """
    arguments = normalize_tool_arguments(name, arguments)

    # PolicyEngine guard (P1-11)
    if policy_engine is not None:
        try:
            decision = policy_engine.evaluate(action=name, target=str(arguments))
            if not decision.is_allowed():
                return ToolResult(
                    status=ToolStatus.BLOCKED,
                    output=f"❌ Blocked by security policy: {decision.reason}",
                    error=decision.reason,
                )
        except Exception:
            pass  # Policy engine errors must never block legitimate execution

    tool_def = registry.get(name)
    if not tool_def:
        return ToolResult(
            status=ToolStatus.INVALID_INPUT,
            output=f"❌ Unknown tool: {name}",
            error=f"Unknown tool: {name}"
        )
    try:
        if not tool_def.handler:
            return ToolResult(
                status=ToolStatus.FAILURE,
                output=f"❌ No handler for tool: {name}"
            )
        result = tool_def.handler(**arguments)
        if isinstance(result, ToolResult):
            return result
        return _adapt_legacy_tool_output(str(result))
    except Exception as e:
        return ToolResult(
            status=ToolStatus.FAILURE,
            output=f"❌ Tool execution failed for {name}: {e}",
            error=str(e)
        )


# Initialize registry with RAW_TOOL_DEFINITIONS
for raw_tool in RAW_TOOL_DEFINITIONS:
    fn_def = raw_tool.get("function", {})
    name = fn_def.get("name")
    if not name:
        continue
        
    handler = TOOL_DISPATCH.get(name)
    
    # Determine flags based on name (legacy mapping)
    mutates = name in (
        "write_file", "edit_file", "patch_file", "multi_edit", 
        "git_commit", "git_branch", "run_command", "run_process", "process_run"
    )
    network = name in (
        "web_fetch", "web_search", "github_list_issues", 
        "github_view_issue", "github_create_pr", "api_check"
    )
    
    perm = PermissionLevel.WRITE if mutates else PermissionLevel.READ
    if network:
        perm = PermissionLevel.NETWORK
        
    risk = RiskLevel.HIGH if mutates or network else RiskLevel.LOW
    if name in ("run_command", "run_process", "process_run"):
        risk = RiskLevel.DANGEROUS
        perm = PermissionLevel.EXECUTE

    tool = ToolDefinition(
        name=name,
        description=fn_def.get("description", ""),
        input_schema=fn_def.get("parameters", {}),
        output_schema={},
        risk_level=risk,
        permission=perm,
        mutates_workspace=mutates,
        requires_network=network,
        handler=handler
    )
    registry.register(tool)
