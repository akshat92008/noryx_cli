"""Compatibility policy SDK backed by the canonical extension protocol."""

from __future__ import annotations

from typing import Any

from nexus.extensions import ToolContext


class ExecutionPolicy:
    """Base policy compatible with the active ``nexus.policies`` entry point."""

    name = "legacy-policy"

    def check_permissions(self, tool_name: str, args: dict) -> tuple[bool, str | None]:
        """Legacy permission hook retained for compatibility."""
        del tool_name, args
        return True, None

    def decide(self, capability: str, target: str, context: ToolContext) -> str:
        """Canonical policy decision: ``allow``, ``ask``, or ``deny``."""
        del context
        allowed, reason = self.check_permissions(capability, {"target": target})
        if allowed:
            return "allow"
        return "ask" if reason and reason.lower().startswith("ask") else "deny"

    def before_action(self, action: str, kwargs: dict) -> None:
        del action, kwargs

    def after_action(self, action: str, kwargs: dict, success: bool, result: Any) -> None:
        del action, kwargs, success, result
