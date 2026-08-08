"""Compatibility facade for canonical CLI utility functions."""
from nexus.cli.cli_impl import _extension_registry, _extension_state_dir, _state_dir_from_working_dir

__all__ = ["_extension_registry", "_extension_state_dir", "_state_dir_from_working_dir"]
