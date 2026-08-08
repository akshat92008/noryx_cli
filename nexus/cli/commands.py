"""Compatibility facade for canonical Noryx CLI command handlers."""

from nexus.cli.cli_impl import (
    handle_slash_command,
)

__all__ = [name for name in globals() if name.startswith("_handle_")] + ["handle_slash_command"]
