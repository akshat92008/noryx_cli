"""Compatibility facade for canonical Nexus CLI command handlers."""
from nexus.cli.cli_impl import (
    _handle_autonomy_project,
    _handle_benchmark,
    _handle_change_commands,
    _handle_collaboration_commands,
    _handle_enterprise,
    _handle_extensions,
    _handle_generate_dashboard,
    _handle_mcp,
    _handle_performance_and_release,
    _handle_plan_commands,
    _handle_recovery_commands,
    _handle_run_management,
    _handle_workspace_commands,
    _solve_issue_prompt,
    handle_slash_command,
)


__all__ = [name for name in globals() if name.startswith("_handle_")] + ["handle_slash_command"]
