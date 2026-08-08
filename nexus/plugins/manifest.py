"""Compatibility import for the canonical plugin manifest contract.

Plugin manifests are implemented by :mod:`nexus.plugins.worker`.  Keeping this
module as a thin alias removes the former duplicate/missing implementation while
preserving the documented import path.
"""

from nexus.plugins.worker import PluginLoadError, PluginManifest, compute_plugin_hash

__all__ = ["PluginLoadError", "PluginManifest", "compute_plugin_hash"]
