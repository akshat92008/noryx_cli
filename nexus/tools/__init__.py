"""
nexus.tools package
-------------------
Canonical implementation lives in nexus.tools.tools_impl (the original tools.py).
All symbols are re-exported here for full backward compatibility.
"""

import os  # Re-exposed so tests can monkeypatch nexus.tools.os.replace

from nexus.tools.tools_impl import (
    # Data / metadata
    RAW_TOOL_DEFINITIONS,
    TOOL_DEFINITIONS,
    TOOL_DISPATCH,
    PermissionLevel,
    # Core types
    RiskLevel,
    ToolDefinition,
    ToolRegistry,
    ToolResult,
    ToolStatus,
    _language_service_pools,
    _PinnedHTTPConnection,
    # Private classes needed by tests (monkeypatching)
    _PinnedHTTPSConnection,
    # Private helpers needed by other modules
    _resolve_path,
    _safe_urlopen,
    _tool_history,
    _tool_owner,
    # Standard library re-export (tests patch nexus.tools.os.replace)
    # Context variables (module-level)
    _tool_working_dir,
    execute_tool,
    get_history,
    # Core execution
    normalize_tool_arguments,
    stop_all_background_processes,
    stop_owned_processes,
    # Analysis tools
    tool_api_check,
    tool_browser_check,
    # Context / history helpers
    tool_context,
    tool_database_check,
    tool_diff_files,
    tool_edit_file,
    tool_edit_notebook_cell,
    tool_file_info,
    tool_find_files,
    tool_generate_dashboard,
    tool_get_project_structure,
    tool_git_branch,
    tool_git_commit,
    tool_git_diff,
    tool_git_log,
    # Git tools
    tool_git_status,
    tool_github_create_pr,
    # GitHub tools
    tool_github_list_issues,
    tool_github_view_issue,
    tool_list_directory,
    tool_message_peer,
    tool_multi_edit,
    tool_patch_file,
    tool_process_run,
    tool_process_status,
    tool_process_stop,
    # File tools
    tool_read_file,
    # Notebook tools
    tool_read_notebook,
    tool_repo_context,
    tool_repo_impact,
    tool_repo_index,
    tool_repo_models,
    tool_repo_navigate,
    tool_repo_routes,
    tool_repo_symbols,
    # Process tools
    tool_run_command,
    tool_run_process,
    # Other tools
    tool_schedule_routine,
    # Search / repo tools
    tool_search_code,
    tool_security_scan,
    # Web tools
    tool_web_fetch,
    tool_web_search,
    tool_write_file,
)

__all__ = [
    "RAW_TOOL_DEFINITIONS",
    "TOOL_DEFINITIONS",
    "TOOL_DISPATCH",
    "_tool_working_dir",
    "tool_context",
    "get_history",
    "RiskLevel",
    "PermissionLevel",
    "ToolStatus",
    "ToolDefinition",
    "ToolResult",
    "ToolRegistry",
    "normalize_tool_arguments",
    "execute_tool",
    "_resolve_path",
    "_safe_urlopen",
]
