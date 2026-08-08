"""
Plugin System — bundles skills, hooks, and tools into modular plugins.

Discovers and loads extension bundles from both project-local directories
and user-global configuration directories.
"""

from nexus.plugins.base import BasePlugin
from nexus.plugins.loader import PluginLoader

__all__ = ["BasePlugin", "PluginLoader"]
