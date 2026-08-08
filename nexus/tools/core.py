"""Compatibility facade for the single canonical tool runtime.

Implementations live in :mod:`nexus.tools.tools_impl`; this module contains no
independent registry or execution path.
"""
from nexus.tools.tools_impl import (
    tool_context,
    get_history,
    normalize_tool_arguments,
    execute_tool,
)

__all__ = ['tool_context', 'get_history', 'normalize_tool_arguments', 'execute_tool']
