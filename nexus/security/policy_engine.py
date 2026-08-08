"""Authoritative PolicyEngine for Nexus CLI.

Enforces deterministic policy precedence beneath the model layer.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Sequence


class SecurityAction(str, Enum):
    """Explicit security-relevant actions governed by PolicyEngine."""

    READ_FILE = "read_file"
    WRITE_FILE = "write_file"
    DELETE_FILE = "delete_file"
    MOVE_FILE = "move_file"
    EXECUTE_COMMAND = "execute_command"
    EXECUTE_SHELL = "execute_shell"
    ACCESS_SECRET = "access_secret"
    ACCESS_ENVIRONMENT = "access_environment"
    OPEN_NETWORK_CONNECTION = "open_network_connection"
    INSTALL_DEPENDENCY = "install_dependency"
    MODIFY_GIT_HISTORY = "modify_git_history"
    PUSH_REMOTE = "push_remote"
    LOAD_PLUGIN = "load_plugin"
    START_MCP_SERVER = "start_mcp_server"
    CREATE_WORKER = "create_worker"
    INCREASE_BUDGET = "increase_budget"
    CHANGE_PROVIDER = "change_provider"
    EXPORT_ARTIFACT = "export_artifact"
    ENABLE_TELEMETRY = "enable_telemetry"
    ACCESS_OTHER_WORKSPACE = "access_other_workspace"


class PolicyOutcome(str, Enum):
    """Possible outcomes of a policy evaluation."""

    ALLOW = "allow"
    ALLOW_ONCE = "allow_once"
    REQUIRE_APPROVAL = "require_approval"
    DENY = "deny"
    BLOCKED = "blocked"
    POLICY_ERROR = "policy_error"


class RiskLevel(str, Enum):
    """Risk severity classification."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class PolicyDecision:
    """A deterministic decision record emitted by PolicyEngine."""

    decision_id: str
    action: SecurityAction
    outcome: PolicyOutcome
    reasons: list[str] = field(default_factory=list)
    matched_rules: list[str] = field(default_factory=list)
    required_approvals: list[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    evidence: list[dict[str, Any]] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)
    expires_at: float | None = None

    def is_allowed(self) -> bool:
        return self.outcome in (PolicyOutcome.ALLOW, PolicyOutcome.ALLOW_ONCE)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["action"] = self.action.value
        data["outcome"] = self.outcome.value
        data["risk_level"] = self.risk_level.value
        return data


class PolicyEngine:
    """Canonical, single-source-of-truth policy evaluation engine for Nexus.
    
    Evaluates actions against deterministic precedence:
    1. Immutable runtime safety rules (hard deny for dangerous root paths, secret exfiltration)
    2. Organization policy
    3. Project policy
    4. User policy
    5. Run-specific execution contract
    6. One-time explicit approvals
    7. Safe defaults
    
    Lower precedence policy layers can NEVER weaken a higher layer DENY.
    """

    def __init__(
        self,
        org_policy: dict[str, Any] | None = None,
        project_policy: dict[str, Any] | None = None,
        user_policy: dict[str, Any] | None = None,
        execution_contract: dict[str, Any] | None = None,
        approvals: list[str] | None = None,
    ):
        self.org_policy = org_policy or {}
        self.project_policy = project_policy or {}
        self.user_policy = user_policy or {}
        self.execution_contract = execution_contract or {}
        self.approvals = set(approvals or [])

    def evaluate(
        self,
        action: SecurityAction | str,
        target: str = "",
        *,
        actor: str = "agent",
        run_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        """Evaluate a security action deterministically."""
        decision_id = f"dec-{uuid.uuid4().hex[:12]}"
        context = context or {}

        # 1. Action Normalization & Validation
        try:
            sec_action = SecurityAction(action) if isinstance(action, str) else action
        except ValueError:
            return PolicyDecision(
                decision_id=decision_id,
                action=SecurityAction.EXECUTE_COMMAND,
                outcome=PolicyOutcome.DENY,
                reasons=[f"Unknown or invalid action type: {action!r}"],
                risk_level=RiskLevel.HIGH,
            )

        # 2. Immutable Runtime Safety Rules (Top Priority)
        immutable_denial = self._check_immutable_safety(sec_action, target, context)
        if immutable_denial:
            return PolicyDecision(
                decision_id=decision_id,
                action=sec_action,
                outcome=PolicyOutcome.DENY,
                reasons=immutable_denial["reasons"],
                matched_rules=["IMMUTABLE_RUNTIME_SAFETY"],
                risk_level=immutable_denial["risk_level"],
            )

        # 3. Organization Policy
        org_denial = self._check_policy_layer(self.org_policy, sec_action, target)
        if org_denial == PolicyOutcome.DENY:
            return PolicyDecision(
                decision_id=decision_id,
                action=sec_action,
                outcome=PolicyOutcome.DENY,
                reasons=["Denied by Organization Policy"],
                matched_rules=["ORG_POLICY_DENY"],
                risk_level=RiskLevel.HIGH,
            )

        # 4. Project Policy (cannot override Org DENY)
        proj_outcome = self._check_policy_layer(self.project_policy, sec_action, target)
        if proj_outcome == PolicyOutcome.DENY:
            return PolicyDecision(
                decision_id=decision_id,
                action=sec_action,
                outcome=PolicyOutcome.DENY,
                reasons=["Denied by Project Policy"],
                matched_rules=["PROJECT_POLICY_DENY"],
                risk_level=RiskLevel.MEDIUM,
            )

        # 5. User Policy
        user_outcome = self._check_policy_layer(self.user_policy, sec_action, target)
        if user_outcome == PolicyOutcome.DENY:
            return PolicyDecision(
                decision_id=decision_id,
                action=sec_action,
                outcome=PolicyOutcome.DENY,
                reasons=["Denied by User Policy"],
                matched_rules=["USER_POLICY_DENY"],
                risk_level=RiskLevel.MEDIUM,
            )

        # 6. Check Execution Contract & Approvals
        if target in self.approvals or f"{sec_action.value}:{target}" in self.approvals:
            return PolicyDecision(
                decision_id=decision_id,
                action=sec_action,
                outcome=PolicyOutcome.ALLOW_ONCE,
                reasons=["Allowed by explicit one-time user approval"],
                matched_rules=["EXPLICIT_APPROVAL"],
                risk_level=RiskLevel.LOW,
            )

        # 7. Check if approval required or allowed by contract / default
        if self._requires_approval(sec_action, target, context):
            return PolicyDecision(
                decision_id=decision_id,
                action=sec_action,
                outcome=PolicyOutcome.REQUIRE_APPROVAL,
                reasons=["Action requires explicit user authorization"],
                required_approvals=[f"{sec_action.value}:{target}"],
                risk_level=self._calculate_risk(sec_action),
            )

        # 8. Allow by default if safe
        return PolicyDecision(
            decision_id=decision_id,
            action=sec_action,
            outcome=PolicyOutcome.ALLOW,
            reasons=["Action permitted by security policy"],
            matched_rules=["SAFE_DEFAULT_ALLOW"],
            risk_level=self._calculate_risk(sec_action),
        )

    def _check_immutable_safety(
        self, action: SecurityAction, target: str, context: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Enforce immutable runtime safety rules that CANNOT be disabled by configuration."""
        target_lower = target.lower()

        # Block access to system paths or private credentials
        forbidden_substrings = [
            "/.ssh",
            "id_rsa",
            "id_ed25519",
            "/.aws/credentials",
            "/.gcp/",
            "/etc/shadow",
            "/etc/passwd",
        ]
        if any(sub in target_lower for sub in forbidden_substrings):
            return {
                "reasons": [f"Access to protected credential or system path is forbidden: {target}"],
                "risk_level": RiskLevel.CRITICAL,
            }

        # Block cloud metadata network connections
        if action == SecurityAction.OPEN_NETWORK_CONNECTION:
            if "169.254.169.254" in target or "metadata.google" in target_lower:
                return {
                    "reasons": ["Access to cloud metadata endpoints is strictly forbidden"],
                    "risk_level": RiskLevel.CRITICAL,
                }

        # Block force push or destructive git mutations unless explicitly approved
        if action == SecurityAction.PUSH_REMOTE and "--force" in context.get("argv", []):
            return {
                "reasons": ["Autonomous force-push to remote repositories is forbidden"],
                "risk_level": RiskLevel.HIGH,
            }

        return None

    def _check_policy_layer(
        self, policy_dict: dict[str, Any], action: SecurityAction, target: str
    ) -> PolicyOutcome | None:
        if not policy_dict:
            return None
        denied_actions = policy_dict.get("deny_actions", [])
        if action.value in denied_actions or "*" in denied_actions:
            return PolicyOutcome.DENY
        return None

    def _requires_approval(
        self, action: SecurityAction, target: str, context: dict[str, Any]
    ) -> bool:
        high_risk_actions = {
            SecurityAction.DELETE_FILE,
            SecurityAction.EXECUTE_SHELL,
            SecurityAction.INSTALL_DEPENDENCY,
            SecurityAction.MODIFY_GIT_HISTORY,
            SecurityAction.PUSH_REMOTE,
            SecurityAction.LOAD_PLUGIN,
            SecurityAction.START_MCP_SERVER,
            SecurityAction.INCREASE_BUDGET,
            SecurityAction.CHANGE_PROVIDER,
            SecurityAction.ACCESS_OTHER_WORKSPACE,
        }
        return action in high_risk_actions

    def _calculate_risk(self, action: SecurityAction) -> RiskLevel:
        critical_actions = {SecurityAction.PUSH_REMOTE, SecurityAction.ACCESS_SECRET}
        high_actions = {
            SecurityAction.DELETE_FILE,
            SecurityAction.EXECUTE_SHELL,
            SecurityAction.INSTALL_DEPENDENCY,
            SecurityAction.MODIFY_GIT_HISTORY,
            SecurityAction.START_MCP_SERVER,
            SecurityAction.LOAD_PLUGIN,
        }
        if action in critical_actions:
            return RiskLevel.CRITICAL
        if action in high_actions:
            return RiskLevel.HIGH
        return RiskLevel.LOW
