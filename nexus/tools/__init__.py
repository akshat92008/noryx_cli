"""
nexus.tools package
-------------------
Canonical implementation lives in nexus.tools.tools_impl (the original tools.py).
All symbols are re-exported here for full backward compatibility.
"""
import os  # Re-exposed so tests can monkeypatch nexus.tools.os.replace
from nexus.tools.tools_impl import (
    # Standard library re-export (tests patch nexus.tools.os.replace)
    # Context variables (module-level)
    _tool_working_dir,
    _tool_history,
    _tool_owner,
    _language_service_pools,
    # Private classes needed by tests (monkeypatching)
    _PinnedHTTPSConnection,
    _PinnedHTTPConnection,
    # Data / metadata
    RAW_TOOL_DEFINITIONS,
    TOOL_DEFINITIONS,
    TOOL_DISPATCH,
    # Context / history helpers
    tool_context,
    get_history,
    # Core types
    RiskLevel,
    PermissionLevel,
    ToolStatus,
    ToolDefinition,
    ToolResult,
    ToolRegistry,
    # Core execution
    normalize_tool_arguments,
    execute_tool,
    # Private helpers needed by other modules
    _resolve_path,
    _safe_urlopen,
    # File tools
    tool_read_file,
    tool_write_file,
    tool_edit_file,
    tool_patch_file,
    tool_multi_edit,
    tool_file_info,
    tool_diff_files,
    # Process tools
    tool_run_command,
    tool_run_process,
    tool_process_run,
    tool_process_status,
    tool_process_stop,
    stop_owned_processes,
    stop_all_background_processes,
    # Search / repo tools
    tool_search_code,
    tool_list_directory,
    tool_find_files,
    tool_get_project_structure,
    tool_repo_index,
    tool_repo_symbols,
    tool_repo_impact,
    tool_repo_context,
    tool_repo_routes,
    tool_repo_models,
    tool_repo_navigate,
    # Analysis tools
    tool_api_check,
    tool_database_check,
    tool_security_scan,
    tool_browser_check,
    # Git tools
    tool_git_status,
    tool_git_diff,
    tool_git_commit,
    tool_git_log,
    tool_git_branch,
    # Web tools
    tool_web_fetch,
    tool_web_search,
    # GitHub tools
    tool_github_list_issues,
    tool_github_view_issue,
    tool_generate_dashboard,
    tool_github_create_pr,
    # Notebook tools
    tool_read_notebook,
    tool_edit_notebook_cell,
    # Other tools
    tool_schedule_routine,
    tool_message_peer,
)

__all__ = [
    "RAW_TOOL_DEFINITIONS", "TOOL_DEFINITIONS", "TOOL_DISPATCH",
    "_tool_working_dir",
    "tool_context", "get_history",
    "RiskLevel", "PermissionLevel", "ToolStatus", "ToolDefinition", "ToolResult", "ToolRegistry",
    "normalize_tool_arguments", "execute_tool",
    "_resolve_path", "_safe_urlopen",
]
