"""Capability-based permission model for extensions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

# Well-known extension capabilities
EXTENSION_CAPABILITIES = frozenset({
    "fs_read",
    "fs_write",
    "network",
    "shell",
    "env_read",
    "tool_invoke",
    "provider_call",
    "context_read",
    "context_write",
    "verification_run",
    "planning_read",
    "planning_write",
    "routing_read",
    "routing_write",
    "event_subscribe",
    "event_publish",
    "mcp_connect",
    "mcp_serve",
    "pure",
})

# Capabilities that require explicit user approval
SENSITIVE_CAPABILITIES = frozenset({
    "fs_write",
    "network",
    "shell",
    "env_read",
    "tool_invoke",
    "provider_call",
    "context_write",
    "routing_write",
    "event_publish",
    "mcp_connect",
    "mcp_serve",
})

# Capabilities that are never grantable to untrusted extensions
FORBIDDEN_CAPABILITIES = frozenset({
    "bypass_tool_gateway",
    "bypass_transaction_engine",
    "modify_nexus_internals",
    "unrestricted_fs",
    "unrestricted_env",
    "auto_install",
    "auto_enable",
})


@dataclass(frozen=True)
class Capability:
    """A single extension capability."""

    name: str
    description: str = ""
    sensitive: bool = False

    def __post_init__(self):
        if self.name in FORBIDDEN_CAPABILITIES:
            raise ValueError(f"Capability '{self.name}' is forbidden")


@dataclass
class CapabilitySet:
    """Set of granted capabilities for an extension."""

    granted: frozenset[str] = field(default_factory=frozenset)
    requested: frozenset[str] = field(default_factory=frozenset)

    def has(self, capability: str) -> bool:
        return capability in self.granted

    def requires_approval(self) -> frozenset[str]:
        """Return requested capabilities that need user approval."""
        return self.requested - self.granted

    def sensitive_requested(self) -> frozenset[str]:
        return self.requested & SENSITIVE_CAPABILITIES

    def to_dict(self) -> dict:
        return {
            "granted": sorted(self.granted),
            "requested": sorted(self.requested),
            "pending": sorted(self.requires_approval()),
        }


def validate_capabilities(capabilities: Iterable[str]) -> list[str]:
    """Validate capability names and return errors."""
    errors: list[str] = []
    for cap in capabilities:
        if cap in FORBIDDEN_CAPABILITIES:
            errors.append(f"Forbidden capability: {cap}")
        elif cap not in EXTENSION_CAPABILITIES:
            errors.append(f"Unknown capability: {cap}")
    return errors


def check_capability_access(
    granted: frozenset[str],
    required: str,
) -> bool:
    """Check if a required capability is granted."""
    if required in FORBIDDEN_CAPABILITIES:
        return False
    return required in granted or "pure" in granted and required == "pure"
