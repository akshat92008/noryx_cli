"""
Hooks Engine — lifecycle automation that triggers actions on events.

Events: file edit, command run, commit, push, error, plan complete, etc.
Hook types: shell commands (argv vectors), prompt injections, tool calls.
"""

from nexus.hooks.base import BaseHook, HookEvent, HookFailurePolicy, HookType
from nexus.hooks.runner import HookRunner

__all__ = ["HookEvent", "HookType", "HookFailurePolicy", "BaseHook", "HookRunner"]
