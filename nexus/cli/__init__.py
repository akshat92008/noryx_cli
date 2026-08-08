"""
nexus.cli package
-----------------
Canonical implementation in nexus.cli.cli_impl (original cli.py).
All public and private functions are re-exported for full backward compatibility,
including attributes that tests monkeypatch.
"""
# Re-export Agent so tests can patch nexus.cli.Agent
from nexus.agent import Agent  # noqa: F401
# Re-export RunCatalog so tests can patch nexus.cli.RunCatalog
from nexus.run_catalog import RunCatalog  # noqa: F401
# Re-export memory elements for test patching
from nexus.memory import ConversationMemory
from nexus.tools.tools_impl import get_history

from nexus.cli.cli_impl import (
    main,
    parse_args,
    run_interactive,
    run_web,
    start_background_web_server,
    non_interactive_exit_code,
    exit_code_for_outcome,
    handle_slash_command,
    _close_and_exit,
    _configure_output_streams,
    _handle_benchmark,
    _handle_workspace_commands,
    _handle_run_management,
    _handle_generate_dashboard,
    _handle_extensions,
    _handle_mcp,
    _handle_enterprise,
    _handle_autonomy_project,
    _handle_performance_and_release,
    _solve_issue_prompt,
    _handle_plan_commands,
    _handle_recovery_commands,
    _handle_change_commands,
    _handle_collaboration_commands,
    _normalize_subcommand_argv,
    _extension_state_dir,
    _extension_registry,
    _state_dir_from_working_dir,
)

__all__ = [
    "Agent", "RunCatalog", "run_doctor",
    "main", "parse_args", "run_interactive", "run_web",
    "start_background_web_server", "non_interactive_exit_code",
    "exit_code_for_outcome", "handle_slash_command",
    "_close_and_exit", "_configure_output_streams", "_handle_benchmark",
]
