"""Compatibility SDK backed by the canonical ``nexus.extensions`` contract.

New extensions should publish entry points under ``nexus.tools``.  The legacy
``BaseTool`` and ``FunctionTool`` names remain available, but instances now
implement the exact runtime protocol consumed by :class:`ExtensionRegistry`.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any, Callable, Iterable

from nexus.extensions import ToolContext


class BaseTool(ABC):
    """Canonical Nexus tool base class with capability declarations."""

    capabilities: tuple[str, ...] = ("pure",)
    filesystem: dict[str, list[str]] = {
        "read_arguments": [],
        "write_arguments": [],
    }

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique tool name exposed to the model."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable tool description."""

    @property
    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON schema defining tool arguments (legacy alias)."""

    @abstractmethod
    def execute(self, **kwargs: Any) -> Any:
        """Execute the tool implementation (legacy entry point)."""

    @property
    def input_schema(self) -> dict[str, Any]:
        """Canonical extension schema consumed by ``ExtensionRegistry``."""
        return self.parameters

    def invoke(self, arguments: dict[str, Any], context: ToolContext) -> Any:
        """Canonical runtime entry point.

        ``context`` is intentionally explicit so tools never need ``os.getcwd``.
        Legacy implementations may ignore it, but filesystem access remains
        constrained by the declared argument contract before invocation.
        """
        del context
        return self.execute(**arguments)

    def to_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.input_schema,
            },
        }


class FunctionTool(BaseTool):
    """Wrap a Python callable as a canonical Nexus extension tool."""

    def __init__(
        self,
        schema: dict[str, Any],
        func: Callable[..., Any],
        *,
        capabilities: Iterable[str] = ("pure",),
        filesystem: dict[str, list[str]] | None = None,
    ):
        self._schema = schema
        self._func = func
        self.capabilities = tuple(str(item) for item in capabilities)
        self.filesystem = filesystem or {
            "read_arguments": [],
            "write_arguments": [],
        }

    @property
    def name(self) -> str:
        return self._schema["function"]["name"]

    @property
    def description(self) -> str:
        return self._schema["function"].get("description", "")

    @property
    def parameters(self) -> dict[str, Any]:
        return self._schema["function"].get(
            "parameters", {"type": "object", "properties": {}}
        )

    def execute(self, **kwargs: Any) -> Any:
        return self._func(**kwargs)


class ToolRegistry:
    """Small local registry for composing canonical extension tools."""

    def __init__(self):
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def get_all_schemas(self) -> list[dict[str, Any]]:
        return [tool.to_schema() for tool in self._tools.values()]

    def execute(self, name: str, arguments: dict[str, Any] | str) -> tuple[bool, str]:
        tool = self.get(name)
        if not tool:
            return False, f"❌ Unknown tool: '{name}' not found in registry."

        args_dict: Any = arguments
        if isinstance(arguments, str):
            try:
                args_dict = json.loads(arguments)
            except json.JSONDecodeError:
                return False, f"❌ Tool '{name}' arguments are not valid JSON."
        if not isinstance(args_dict, dict):
            return False, f"❌ Tool '{name}' arguments must be a JSON object."

        try:
            result = tool.execute(**args_dict)
            rendered = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
            return True, rendered
        except (TypeError, ValueError) as exc:# extension boundary must return a diagnostic
            return False, f"❌ Tool '{name}' failed: {exc}"
