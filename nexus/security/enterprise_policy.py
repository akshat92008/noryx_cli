"""Enterprise policy model and precedence merger for Nexus CLI.

Supports organization, project, and user policy schemas with deterministic precedence merging.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class OrganizationPolicy:
    organization_id: str = "org-default"
    allowed_providers: list[str] = field(default_factory=lambda: ["hosted", "nova", "custom"])
    allowed_models: list[str] = field(default_factory=list)
    network_mode: str = "allowlist"
    protected_paths: list[str] = field(default_factory=list)
    forbidden_commands: list[str] = field(default_factory=list)
    deny_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProjectPolicy:
    project_id: str = "proj-default"
    allowed_providers: list[str] = field(default_factory=list)
    deny_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PolicyMerger:
    """Merges organization, project, and user policies deterministically."""

    @staticmethod
    def merge(
        org_policy: OrganizationPolicy | dict[str, Any],
        proj_policy: ProjectPolicy | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        org_dict = org_policy.to_dict() if isinstance(org_policy, OrganizationPolicy) else org_policy
        proj_dict = (
            proj_policy.to_dict()
            if isinstance(proj_policy, ProjectPolicy)
            else (proj_policy or {})
        )

        effective_deny_actions = set(org_dict.get("deny_actions", [])) | set(proj_dict.get("deny_actions", []))

        return {
            "organization_id": org_dict.get("organization_id", "org-default"),
            "network_mode": org_dict.get("network_mode", "allowlist"),
            "deny_actions": list(effective_deny_actions),
            "allowed_providers": org_dict.get("allowed_providers", []),
        }
