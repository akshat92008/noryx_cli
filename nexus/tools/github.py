"""Compatibility facade for the single canonical tool runtime.

Implementations live in :mod:`nexus.tools.tools_impl`; this module contains no
independent registry or execution path.
"""

from nexus.tools.tools_impl import (
    tool_github_create_pr,
    tool_github_list_issues,
    tool_github_view_issue,
)

__all__ = ["tool_github_list_issues", "tool_github_view_issue", "tool_github_create_pr"]
