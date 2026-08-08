"""Compatibility facade for the single canonical tool runtime.

Implementations live in :mod:`nexus.tools.tools_impl`; this module contains no
independent registry or execution path.
"""
from nexus.tools.tools_impl import (
    tool_run_command,
    tool_run_process,
    tool_process_run,
    tool_process_status,
    tool_process_stop,
    _watch_background_process,
    stop_owned_processes,
    stop_all_background_processes,
)

__all__ = ['tool_run_command', 'tool_run_process', 'tool_process_run', 'tool_process_status', 'tool_process_stop', '_watch_background_process', 'stop_owned_processes', 'stop_all_background_processes']
