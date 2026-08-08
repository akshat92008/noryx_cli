"""Compatibility facade for the single canonical tool runtime.

Implementations live in :mod:`nexus.tools.tools_impl`; this module contains no
independent registry or execution path.
"""
from nexus.tools.tools_impl import (
    _PinnedHTTPConnection,
    _PinnedHTTPSConnection,
    tool_web_fetch,
    tool_web_search,
)

__all__ = ['_PinnedHTTPConnection', '_PinnedHTTPSConnection', 'tool_web_fetch', 'tool_web_search']
