"""Compatibility facade for the single canonical tool runtime.

Implementations live in :mod:`nexus.tools.tools_impl`; this module contains no
independent registry or execution path.
"""

from nexus.tools.tools_impl import (
    _run_git,
    tool_git_branch,
    tool_git_commit,
    tool_git_diff,
    tool_git_log,
    tool_git_status,
    tool_github_create_pr,
    tool_github_list_issues,
    tool_github_view_issue,
)

__all__ = [
    "_run_git",
    "tool_git_status",
    "tool_git_diff",
    "tool_git_commit",
    "tool_git_log",
    "tool_git_branch",
    "tool_github_list_issues",
    "tool_github_view_issue",
    "tool_github_create_pr",
]
