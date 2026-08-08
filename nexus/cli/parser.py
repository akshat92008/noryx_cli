"""Compatibility facade for the canonical Nexus CLI parser."""
from nexus.cli.cli_impl import _normalize_subcommand_argv, parse_args

__all__ = ["parse_args", "_normalize_subcommand_argv"]
