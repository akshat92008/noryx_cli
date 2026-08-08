"""
SDK for extending Nexus with custom hooks.
"""

from typing import Protocol, runtime_checkable

# We import HookEvent from nexus.hooks.base to maintain backwards compatibility,
# but expose it through the SDK.
from nexus.hooks.base import HookContext, HookEvent


@runtime_checkable
class HookPlugin(Protocol):
    """Protocol for a Nexus hook plugin."""

    def on_event(self, event: HookEvent, context: HookContext) -> None:
        """Triggered when a hook event fires."""
        ...

    @property
    def name(self) -> str:
        """Name of the hook plugin."""
        ...
