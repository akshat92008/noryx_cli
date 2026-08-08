"""Structured repository permissions loaded from ``.nexus/policies.yml``."""

from __future__ import annotations

import fnmatch
import json
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


@dataclass
class ModePolicy:
    """Defines the operational rules and bounds for a specific execution mode."""

    may_edit: bool = False
    may_apply: bool = False
    require_review: bool = True
    context_depth: str = "shallow"
    model_strategy: str = "balanced"
    verification_level: str = "standard"
    retry_budget: int = 0
    require_os_isolation: bool = False
    allow_shell_command: bool = True
    require_distinct_reviewer: bool = False


def get_mode_policy(mode: str) -> ModePolicy:
    """Return the ModePolicy preset for a given mode string."""
    if mode == "review":
        return ModePolicy(
            may_edit=True,
            may_apply=False,
            require_review=True,
            context_depth="deep",
            verification_level="full",
            require_os_isolation=True,
        )
    elif mode in ("workspace", "default"):
        return ModePolicy(
            may_edit=True,
            may_apply=False,
            require_review=True,
            require_os_isolation=True,
        )
    elif mode in ("autonomous", "acceptEdits"):
        return ModePolicy(
            may_edit=True,
            may_apply=True,
            require_review=False,
            retry_budget=2,
            require_os_isolation=True,
            allow_shell_command=False,
        )
    elif mode == "quality":
        return ModePolicy(
            may_edit=True,
            may_apply=True,
            require_review=True,
            context_depth="deep",
            model_strategy="quality",
            verification_level="full",
            retry_budget=3,
            require_os_isolation=True,
            allow_shell_command=False,
            require_distinct_reviewer=True,
        )
    elif mode == "budget":
        return ModePolicy(
            may_edit=True,
            may_apply=True,
            require_review=False,
            model_strategy="budget",
            retry_budget=1,
            require_os_isolation=True,
            allow_shell_command=False,
        )
    elif mode == "plan":
        return ModePolicy(
            may_edit=False, may_apply=False, require_review=True, context_depth="deep"
        )
    elif mode == "local-only":
        return ModePolicy(
            may_edit=True,
            may_apply=True,
            require_review=False,
            model_strategy="local",
            retry_budget=2,
            require_os_isolation=True,
            allow_shell_command=False,
        )
    elif mode == "ci":
        return ModePolicy(
            may_edit=True,
            may_apply=True,
            require_review=False,
            verification_level="full",
            retry_budget=1,
            require_os_isolation=True,
            allow_shell_command=False,
        )
    return ModePolicy()


class PermissionDecision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class CapabilityRule:
    decision: PermissionDecision
    capability: str
    pattern: str

    @classmethod
    def parse(cls, decision: PermissionDecision, value: str) -> "CapabilityRule":
        if ":" in value:
            capability, pattern = value.split(":", 1)
        else:
            capability, pattern = value, "*"
        capability = capability.strip().lower().replace("-", "_")
        pattern = pattern.strip() or "*"
        if not re.fullmatch(r"[a-z_][a-z0-9_]*", capability):
            raise ValueError(f"Invalid capability name: {capability!r}")
        return cls(decision, capability, pattern)


@dataclass
class Policy:
    rules: list[CapabilityRule] = field(default_factory=list)
    source: str = ""
    defaults: dict[str, PermissionDecision] = field(default_factory=dict)

    def decide(self, capability: str, target: str = "") -> PermissionDecision:
        normalized = capability.lower().replace("-", "_")
        matching = [
            rule
            for rule in self.rules
            if rule.capability == normalized and fnmatch.fnmatch(target or "", rule.pattern)
        ]
        if matching:
            # A deny can never be shadowed by a broader allow.
            if any(rule.decision == PermissionDecision.DENY for rule in matching):
                return PermissionDecision.DENY
            if any(rule.decision == PermissionDecision.ASK for rule in matching):
                return PermissionDecision.ASK
            return PermissionDecision.ALLOW
        return self.defaults.get(normalized, PermissionDecision.ASK)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "defaults": {key: value.value for key, value in self.defaults.items()},
            "rules": [
                {
                    "decision": rule.decision.value,
                    "capability": rule.capability,
                    "pattern": rule.pattern,
                }
                for rule in self.rules
            ],
        }


DEFAULT_DECISIONS = {
    "read": PermissionDecision.ALLOW,
    "write": PermissionDecision.ASK,
    "command": PermissionDecision.ASK,
    "package_install": PermissionDecision.ASK,
    "database_migration": PermissionDecision.ASK,
    "network_access": PermissionDecision.ASK,
    "git_push": PermissionDecision.ASK,
    "deployment": PermissionDecision.ASK,
}


class PolicyLoader:
    """Parse JSON or the documented conservative YAML subset."""

    FILENAMES = (".nexus/policies.yml", ".nexus/policies.yaml", ".nexus/policies.json")

    def __init__(self, root: str | Path, is_trusted=None):
        self.root = Path(root).expanduser().resolve()
        self.is_trusted = is_trusted

    def load(self) -> Policy:
        for relative in self.FILENAMES:
            path = self.root / relative
            if path.is_file():
                data = self._parse(path)
                trusted = self.is_trusted(path) if self.is_trusted else True
                return self._from_mapping(data, path, trusted)
        return Policy(defaults=dict(DEFAULT_DECISIONS))

    def _parse(self, path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8")
        if path.suffix == ".json":
            value = json.loads(text)
            if not isinstance(value, dict):
                raise ValueError(f"Policy root must be an object: {path}")
            return value
        return self._parse_yaml_subset(text)

    @staticmethod
    def _parse_yaml_subset(text: str) -> dict[str, Any]:
        """Parse top-level ``allow/ask/deny`` lists without executing YAML tags."""
        result: dict[str, Any] = {}
        section = ""
        for line_number, raw in enumerate(text.splitlines(), 1):
            stripped = raw.split("#", 1)[0].rstrip()
            if not stripped.strip():
                continue
            if not raw.startswith((" ", "\t")) and stripped.endswith(":"):
                section = stripped[:-1].strip()
                result.setdefault(section, [])
                continue
            item = re.match(r"^\s*-\s+(.+?)\s*$", stripped)
            if item and section:
                value = item.group(1).strip().strip("'\"")
                result.setdefault(section, []).append(value)
                continue
            scalar = re.match(r"^\s*([A-Za-z_][\w-]*)\s*:\s*(\S+)\s*$", stripped)
            if scalar:
                result[scalar.group(1)] = scalar.group(2).strip("'\"")
                section = ""
                continue
            raise ValueError(f"Unsupported policy syntax at line {line_number}")
        return result

    @staticmethod
    def _from_mapping(data: dict[str, Any], path: Path, trusted: bool = True) -> Policy:
        rules = []
        for label, decision in (
            ("allow", PermissionDecision.ALLOW),
            ("ask", PermissionDecision.ASK),
            ("deny", PermissionDecision.DENY),
        ):
            values = data.get(label, [])
            if not isinstance(values, list):
                raise ValueError(f"{label} must be a list in {path}")
            for value in values:
                rule = CapabilityRule.parse(decision, str(value))
                if not trusted:
                    default_decision = DEFAULT_DECISIONS.get(rule.capability, PermissionDecision.ASK)
                    levels = {PermissionDecision.ALLOW: 0, PermissionDecision.ASK: 1, PermissionDecision.DENY: 2}
                    if levels[rule.decision] < levels[default_decision]:
                        continue
                rules.append(rule)
        defaults = dict(DEFAULT_DECISIONS)
        raw_defaults = data.get("defaults", {})
        if isinstance(raw_defaults, dict):
            for capability, value in raw_defaults.items():
                decision = PermissionDecision(str(value))
                if not trusted:
                    default_decision = DEFAULT_DECISIONS.get(str(capability), PermissionDecision.ASK)
                    levels = {PermissionDecision.ALLOW: 0, PermissionDecision.ASK: 1, PermissionDecision.DENY: 2}
                    if levels[decision] < levels[default_decision]:
                        continue
                defaults[str(capability)] = decision
        return Policy(rules=rules, source=str(path), defaults=defaults)
