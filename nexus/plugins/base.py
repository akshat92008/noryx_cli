"""
Base Plugin — foundation class for modular plugins.

Plugins bundle custom skills, lifecycle hooks, and extra tool implementations.
"""

from typing import Any

from nexus.hooks.base import BaseHook
from nexus.skills.base import BaseSkill


class BasePlugin:
    """
    Base class for plugins. Subclass this to define an extension package.

    Plugins can provide custom tools, skills, hooks, and commands.
    """

    name: str = "base_plugin"
    description: str = "Base plugin description"
    version: str = "1.0.0"
    author: str = ""

    def __init__(self):
        self.enabled = True

    def get_skills(self) -> list[BaseSkill]:
        """Return list of custom skills registered by this plugin."""
        return []

    def get_hooks(self) -> list[BaseHook]:
        """Return list of lifecycle hooks registered by this plugin."""
        return []

    def get_tools(self) -> list[dict]:
        """Return list of custom tool schemas (definitions) registered by this plugin."""
        return []

    def get_tool_dispatch(self) -> dict[str, Any]:
        """Return map of tool names to implementation functions."""
        return {}

    def setup(self) -> bool:
        """Lifecycle initialization hook. Returns True if setup is successful."""
        return True

    def teardown(self):
        """Teardown logic when disabling or unloading plugin."""
        pass
