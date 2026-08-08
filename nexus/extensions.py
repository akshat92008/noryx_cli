"""Stable extension contracts and entry-point discovery for Nexus."""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib.metadata import entry_points
from typing import Any, Protocol, runtime_checkable

EXTENSION_API_VERSION = "nexus.extensions.v1"


@dataclass(frozen=True)
class ModelRequest:
    messages: tuple[dict[str, Any], ...]
    tools: tuple[dict[str, Any], ...] = ()
    max_tokens: int | None = None
    temperature: float = 0.2
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ModelResponse:
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, int | float] = field(default_factory=dict)
    raw: Any = None


@runtime_checkable
class ModelProvider(Protocol):
    """Provider SDK contract."""

    name: str

    def complete(self, model: str, request: ModelRequest) -> ModelResponse: ...


@runtime_checkable
class NexusTool(Protocol):
    """Tool SDK contract."""

    name: str
    description: str
    input_schema: dict[str, Any]
    # Implementations must expose ``capabilities``. Filesystem tools must also
    # expose ``filesystem = {"read_arguments": [...], "write_arguments": [...]}``.
    capabilities: tuple[str, ...] | list[str] | set[str]

    def invoke(self, arguments: dict[str, Any], context: "ToolContext") -> Any: ...


@dataclass(frozen=True)
class ToolContext:
    working_dir: str
    session_id: str
    task_id: str = ""
    permission_mode: str = "review"


@runtime_checkable
class PolicyProvider(Protocol):
    """Policy SDK contract."""

    name: str

    def decide(self, capability: str, target: str, context: ToolContext) -> str: ...


@dataclass
class ExtensionRecord:
    name: str
    group: str
    value: str
    loaded: bool
    error: str = ""
    instance: Any = None


class ExtensionRegistry:
    """Discover extensions through standard Python entry points."""

    GROUPS = {
        "providers": "nexus.providers",
        "tools": "nexus.tools",
        "policies": "nexus.policies",
        "skills": "nexus.skills",
    }

    def __init__(self):
        self.records: list[ExtensionRecord] = []

    def discover(self) -> list[ExtensionRecord]:
        self.records = []
        available = entry_points()
        for label, group in self.GROUPS.items():
            selected = available.select(group=group)
            for item in selected:
                try:
                    instance = item.load()
                    if isinstance(instance, type):
                        instance = instance()
                    self._validate(label, instance)
                except Exception as exc:
                    self.records.append(
                        ExtensionRecord(item.name, group, item.value, False, str(exc))
                    )
                else:
                    self.records.append(
                        ExtensionRecord(
                            item.name,
                            group,
                            item.value,
                            True,
                            instance=instance,
                        )
                    )
        return list(self.records)

    @staticmethod
    def _validate(kind: str, instance: Any) -> None:
        if kind == "providers" and not isinstance(instance, ModelProvider):
            raise TypeError("Provider does not implement ModelProvider")
        if kind == "tools" and not isinstance(instance, NexusTool):
            raise TypeError("Tool does not implement NexusTool")
        if kind == "policies" and not isinstance(instance, PolicyProvider):
            raise TypeError("Policy does not implement PolicyProvider")
        if kind == "skills" and not hasattr(instance, "get_system_prompt"):
            raise TypeError("Skill does not expose get_system_prompt()")

    def loaded(self, group: str) -> list[Any]:
        normalized = self.GROUPS.get(group, group)
        return [
            record.instance
            for record in self.records
            if record.group == normalized and record.loaded
        ]
