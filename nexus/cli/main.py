"""Compatibility facade for the single canonical CLI runtime.

All command execution lives in :mod:`nexus.cli.cli_impl`.  This module exists only
for backwards-compatible imports and contains no independent orchestration path.
"""
from nexus.cli.cli_impl import (
    _close_and_exit,
    _configure_output_streams,
    exit_code_for_outcome,
    main,
    non_interactive_exit_code,
    run_interactive,
    run_web,
    start_background_web_server,
)

__all__ = [
    "main", "run_interactive", "run_web", "start_background_web_server",
    "non_interactive_exit_code", "exit_code_for_outcome", "_close_and_exit",
    "_configure_output_streams",
]
