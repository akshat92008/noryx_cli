"""Deterministic enterprise governance primitives.

The enterprise layer is intentionally offline-first: all state is JSON on disk,
policy rules are declarative dictionaries, and evaluation is deterministic.
"""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

ENTERPRISE_STATE_VERSION = "nexus.enterprise.v1"
SECRET_REDACTION = "[REDACTED]"


class Role(str, Enum):
    VIEWER = "viewer"
    DEVELOPER = "developer"
    MAINTAINER = "maintainer"
    APPROVER = "approver"
    SECURITY_REVIEWER = "security_reviewer"
    AUDITOR = "auditor"
    ORG_ADMIN = "org_admin"
    SERVICE = "service"


ROLE_PERMISSIONS: dict[Role, frozenset[str]] = {
    Role.VIEWER: frozenset({"read_repository", "run_analysis"}),
    Role.DEVELOPER: frozenset({
        "read_repository",
        "run_analysis",
        "request_edits",
        "execute_commands",
    }),
    Role.MAINTAINER: frozenset({
        "read_repository",
        "run_analysis",
        "request_edits",
        "execute_commands",
        "install_packages",
        "apply_transactions",
        "roll_back",
        "manage_extensions",
    }),
    Role.APPROVER: frozenset({"approve_edits", "apply_transactions", "view_audit_records"}),
    Role.SECURITY_REVIEWER: frozenset({
        "read_repository",
        "run_analysis",
        "access_network",
        "view_audit_records",
        "export_compliance_data",
    }),
    Role.AUDITOR: frozenset({"view_audit_records", "export_compliance_data"}),
    Role.ORG_ADMIN: frozenset({
        "read_repository",
        "run_analysis",
        "request_edits",
        "approve_edits",
        "execute_commands",
        "access_network",
        "install_packages",
        "access_credentials",
        "use_cloud_models",
        "run_autonomous_mode",
        "apply_transactions",
        "roll_back",
        "manage_policies",
        "manage_extensions",
        "view_audit_records",
        "export_compliance_data",
    }),
    Role.SERVICE: frozenset({"read_repository", "run_analysis"}),
}


@dataclass(frozen=True)
class Identity:
    identity_id: str
    display_name: str
    kind: str = "local_user"
    organization_id: str = ""
    roles: tuple[Role, ...] = (Role.VIEWER,)
    project_ids: tuple[str, ...] = ()
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["roles"] = [role.value for role in self.roles]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Identity":
        return cls(
            identity_id=data["identity_id"],
            display_name=data.get("display_name", data["identity_id"]),
            kind=data.get("kind", "local_user"),
            organization_id=data.get("organization_id", ""),
            roles=tuple(Role(role) for role in data.get("roles", ["viewer"])),
            project_ids=tuple(data.get("project_ids", ())),
            active=data.get("active", True),
        )


@dataclass(frozen=True)
class Organization:
    organization_id: str
    name: str
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class Project:
    project_id: str
    organization_id: str
    name: str
    repository: str = ""
    sensitivity: str = "internal"
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class PolicyRule:
    rule_id: str
    effect: str
    conditions: dict[str, Any] = field(default_factory=dict)
    constraints: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    priority: int = 100
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PolicyRule":
        effect = data.get("effect", "")
        if effect not in {
            "allow",
            "deny",
            "require_approval",
            "allow_with_constraints",
            "require_local_model",
            "require_stronger_verification",
            "require_independent_review",
            "require_sandbox",
            "require_audit_annotation",
        }:
            raise ValueError(f"Invalid policy effect: {effect}")
        if not isinstance(data.get("conditions", {}), dict):
            raise ValueError("Policy conditions must be an object")
        if not isinstance(data.get("constraints", {}), dict):
            raise ValueError("Policy constraints must be an object")
        return cls(
            rule_id=data.get("rule_id") or f"rule_{uuid.uuid4().hex[:10]}",
            effect=effect,
            conditions=dict(data.get("conditions", {})),
            constraints=dict(data.get("constraints", {})),
            reason=data.get("reason", ""),
            priority=int(data.get("priority", 100)),
            active=bool(data.get("active", True)),
        )


@dataclass(frozen=True)
class PolicyDecision:
    effect: str
    allowed: bool
    reason_codes: tuple[str, ...]
    constraints: dict[str, Any] = field(default_factory=dict)
    matched_rules: tuple[str, ...] = ()
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ApprovalRequest:
    request_id: str
    requester_id: str
    scope: str
    risk: str
    repository_revision: str = ""
    transaction_id: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    expires_at: float = 0.0
    status: str = "pending"
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ApprovalDecision:
    request_id: str
    approver_id: str
    decision: str
    reason: str = ""
    decided_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class EnterpriseAuditRecord:
    record_id: str
    action: str
    actor_id: str
    organization_id: str = ""
    project_id: str = ""
    timestamp: float = field(default_factory=time.time)
    details: dict[str, Any] = field(default_factory=dict)
    previous_hash: str = ""
    record_hash: str = ""

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("record_hash", None)
        return data


@dataclass(frozen=True)
class BudgetLimit:
    subject_type: str
    subject_id: str
    limit_usd: float
    period: str = "monthly"
    spent_usd: float = 0.0
    approval_threshold_usd: float = 0.0


class EnterpriseStore:
    """JSON-backed enterprise state store."""

    def __init__(self, state_dir: Path | None = None):
        self.state_dir = state_dir or Path.home() / ".nexusai" / "enterprise"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def read(self, name: str, default: Any) -> Any:
        path = self.state_dir / f"{name}.json"
        if not path.is_file():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default

    def write(self, name: str, data: Any) -> None:
        path = self.state_dir / f"{name}.json"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)


def _matches_condition(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        if "in" in expected:
            return actual in expected["in"]
        if "not_in" in expected:
            return actual not in expected["not_in"]
        if "lte" in expected:
            return actual is not None and actual <= expected["lte"]
        if "gte" in expected:
            return actual is not None and actual >= expected["gte"]
        if "prefix" in expected:
            return isinstance(actual, str) and actual.startswith(str(expected["prefix"]))
        raise ValueError(f"Unsupported condition operator: {sorted(expected)}")
    return actual == expected


class EnterpriseAuditService:
    """Tamper-evident append-only enterprise audit log."""

    def __init__(self, store: EnterpriseStore | None = None):
        self.store = store or EnterpriseStore()

    def _records(self) -> list[EnterpriseAuditRecord]:
        return [
            EnterpriseAuditRecord(**item)
            for item in self.store.read("audit", {"version": ENTERPRISE_STATE_VERSION, "records": []})[
                "records"
            ]
        ]

    def append(
        self,
        action: str,
        actor_id: str,
        *,
        organization_id: str = "",
        project_id: str = "",
        details: dict[str, Any] | None = None,
    ) -> EnterpriseAuditRecord:
        records = self._records()
        previous_hash = records[-1].record_hash if records else ""
        record = EnterpriseAuditRecord(
            record_id=f"audit_{uuid.uuid4().hex}",
            action=action,
            actor_id=actor_id,
            organization_id=organization_id,
            project_id=project_id,
            details=_redact(details or {}),
            previous_hash=previous_hash,
        )
        digest = hashlib.sha256(json.dumps(record.payload(), sort_keys=True).encode()).hexdigest()
        final = EnterpriseAuditRecord(**(record.payload() | {"record_hash": digest}))
        records.append(final)
        self.store.write(
            "audit",
            {"version": ENTERPRISE_STATE_VERSION, "records": [asdict(item) for item in records]},
        )
        return final

    def verify_chain(self) -> bool:
        previous_hash = ""
        for record in self._records():
            if record.previous_hash != previous_hash:
                return False
            digest = hashlib.sha256(json.dumps(record.payload(), sort_keys=True).encode()).hexdigest()
            if digest != record.record_hash:
                return False
            previous_hash = record.record_hash
        return True

    def list_records(self) -> list[EnterpriseAuditRecord]:
        return self._records()


class IdentityService:
    def __init__(self, store: EnterpriseStore | None = None):
        self.store = store or EnterpriseStore()

    def create(
        self,
        identity_id: str,
        display_name: str,
        *,
        kind: str = "local_user",
        organization_id: str = "",
        roles: tuple[Role, ...] = (Role.VIEWER,),
        project_ids: tuple[str, ...] = (),
    ) -> Identity:
        identities = {item["identity_id"]: item for item in self.store.read("identities", {"items": []})["items"]}
        identity = Identity(identity_id, display_name, kind, organization_id, roles, project_ids)
        identities[identity_id] = identity.to_dict()
        self.store.write("identities", {"version": ENTERPRISE_STATE_VERSION, "items": list(identities.values())})
        return identity

    def get(self, identity_id: str) -> Identity | None:
        for item in self.store.read("identities", {"items": []})["items"]:
            if item["identity_id"] == identity_id:
                return Identity.from_dict(item)
        return None


class OrganizationService:
    def __init__(self, store: EnterpriseStore | None = None):
        self.store = store or EnterpriseStore()

    def create(self, name: str, organization_id: str | None = None) -> Organization:
        organization = Organization(organization_id or f"org_{uuid.uuid4().hex[:10]}", name)
        items = self.store.read("organizations", {"items": []})["items"]
        items.append(asdict(organization))
        self.store.write("organizations", {"version": ENTERPRISE_STATE_VERSION, "items": items})
        return organization

    def list(self) -> list[Organization]:
        return [Organization(**item) for item in self.store.read("organizations", {"items": []})["items"]]


class ProjectService:
    def __init__(self, store: EnterpriseStore | None = None):
        self.store = store or EnterpriseStore()

    def create(
        self,
        organization_id: str,
        name: str,
        *,
        repository: str = "",
        sensitivity: str = "internal",
        project_id: str | None = None,
    ) -> Project:
        project = Project(project_id or f"entproj_{uuid.uuid4().hex[:10]}", organization_id, name, repository, sensitivity)
        items = self.store.read("enterprise_projects", {"items": []})["items"]
        items.append(asdict(project))
        self.store.write("enterprise_projects", {"version": ENTERPRISE_STATE_VERSION, "items": items})
        return project

    def get(self, project_id: str) -> Project | None:
        for item in self.store.read("enterprise_projects", {"items": []})["items"]:
            if item["project_id"] == project_id:
                return Project(**item)
        return None


class AuthorizationService:
    """RBAC authorization. Deny by default."""

    def __init__(self, identities: IdentityService | None = None):
        self.identities = identities or IdentityService()

    def is_allowed(self, identity_id: str, permission: str, *, project_id: str = "") -> bool:
        identity = self.identities.get(identity_id)
        if not identity or not identity.active:
            return False
        if project_id and identity.project_ids and project_id not in identity.project_ids:
            return False
        return any(permission in ROLE_PERMISSIONS[role] for role in identity.roles)


class PolicyEngine:
    """Declarative policy-as-code evaluator with deny precedence."""

    def __init__(self, store: EnterpriseStore | None = None, audit: EnterpriseAuditService | None = None):
        self.store = store or EnterpriseStore()
        self.audit = audit or EnterpriseAuditService(self.store)

    def load_rules(self) -> list[PolicyRule]:
        return [
            PolicyRule.from_dict(item)
            for item in self.store.read("policies", {"version": ENTERPRISE_STATE_VERSION, "rules": []})["rules"]
        ]

    def activate_rules(self, rules: list[PolicyRule], *, actor_id: str = "system") -> None:
        self.store.write(
            "policies",
            {
                "version": ENTERPRISE_STATE_VERSION,
                "rules": [rule.to_dict() for rule in sorted(rules, key=lambda item: (item.priority, item.rule_id))],
            },
        )
        self.audit.append("policy.activate", actor_id, details={"rule_count": len(rules)})

    def evaluate(self, context: dict[str, Any]) -> PolicyDecision:
        matched = []
        constraints: dict[str, Any] = {}
        effects: list[str] = []
        reasons: list[str] = []
        for rule in sorted(self.load_rules(), key=lambda item: (item.priority, item.rule_id)):
            if not rule.active:
                continue
            if all(_matches_condition(context.get(key), value) for key, value in rule.conditions.items()):
                matched.append(rule.rule_id)
                effects.append(rule.effect)
                reasons.append(rule.reason or rule.rule_id)
                constraints.update(rule.constraints)

        if "deny" in effects:
            effect = "deny"
            allowed = False
        elif not effects:
            effect = "deny"
            allowed = False
            reasons.append("deny_by_default")
        elif "require_approval" in effects:
            effect = "require_approval"
            allowed = False
        else:
            effect = effects[0]
            allowed = effect in {"allow", "allow_with_constraints"}

        decision = PolicyDecision(
            effect=effect,
            allowed=allowed,
            reason_codes=tuple(reasons),
            constraints=constraints,
            matched_rules=tuple(matched),
            evidence={"context_hash": hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()},
        )
        self.audit.append(
            "policy.decision",
            context.get("identity_id", "unknown"),
            organization_id=context.get("organization_id", ""),
            project_id=context.get("project_id", ""),
            details=asdict(decision),
        )
        return decision


class ApprovalWorkflowService:
    def __init__(self, store: EnterpriseStore | None = None, auth: AuthorizationService | None = None):
        self.store = store or EnterpriseStore()
        self.auth = auth or AuthorizationService(IdentityService(self.store))

    def request(self, requester_id: str, scope: str, risk: str, **kwargs: Any) -> ApprovalRequest:
        request = ApprovalRequest(
            request_id=f"approval_{uuid.uuid4().hex}",
            requester_id=requester_id,
            scope=scope,
            risk=risk,
            repository_revision=kwargs.get("repository_revision", ""),
            transaction_id=kwargs.get("transaction_id", ""),
            evidence=kwargs.get("evidence", {}),
            expires_at=kwargs.get("expires_at", 0.0),
        )
        data = self.store.read("approvals", {"requests": [], "decisions": []})
        data["requests"].append(asdict(request))
        self.store.write("approvals", data)
        return request

    def decide(self, request_id: str, approver_id: str, decision: str, *, reason: str = "") -> ApprovalDecision:
        data = self.store.read("approvals", {"requests": [], "decisions": []})
        request = next((item for item in data["requests"] if item["request_id"] == request_id), None)
        if not request:
            raise ValueError(f"Approval request not found: {request_id}")
        if request["requester_id"] == approver_id:
            raise PermissionError("Self-approval is not allowed")
        if not self.auth.is_allowed(approver_id, "approve_edits"):
            raise PermissionError("Approver lacks approve_edits permission")
        if decision not in {"approved", "rejected"}:
            raise ValueError("Approval decision must be approved or rejected")
        request["status"] = decision
        result = ApprovalDecision(request_id, approver_id, decision, reason)
        data["decisions"].append(asdict(result))
        self.store.write("approvals", data)
        return result

    def list_requests(self) -> list[ApprovalRequest]:
        return [ApprovalRequest(**item) for item in self.store.read("approvals", {"requests": []})["requests"]]


class SecretBroker:
    def __init__(self, store: EnterpriseStore | None = None, auth: AuthorizationService | None = None):
        self.store = store or EnterpriseStore()
        self.auth = auth or AuthorizationService(IdentityService(self.store))

    def put(self, name: str, value: str, *, project_id: str, provider: str = "", purpose: str = "") -> None:
        digest = hashlib.sha256(value.encode()).hexdigest()
        data = self.store.read("secrets", {"items": []})
        data["items"] = [item for item in data["items"] if not (item["name"] == name and item["project_id"] == project_id)]
        data["items"].append({
            "name": name,
            "project_id": project_id,
            "provider": provider,
            "purpose": purpose,
            "value": value,
            "digest": digest,
            "created_at": time.time(),
        })
        self.store.write("secrets", data)

    def request(self, name: str, *, identity_id: str, project_id: str, provider: str = "", purpose: str = "") -> str:
        if not self.auth.is_allowed(identity_id, "access_credentials", project_id=project_id):
            raise PermissionError("Identity lacks access_credentials permission")
        for item in self.store.read("secrets", {"items": []})["items"]:
            if item["name"] == name and item["project_id"] == project_id:
                if provider and item.get("provider") and item["provider"] != provider:
                    raise PermissionError("Provider scope mismatch")
                if purpose and item.get("purpose") and item["purpose"] != purpose:
                    raise PermissionError("Purpose scope mismatch")
                return item["value"]
        raise KeyError(name)

    def list_redacted(self, project_id: str = "") -> list[dict[str, Any]]:
        items = self.store.read("secrets", {"items": []})["items"]
        return [
            {key: value for key, value in item.items() if key != "value"} | {"value": SECRET_REDACTION}
            for item in items
            if not project_id or item["project_id"] == project_id
        ]


class BudgetGovernanceService:
    def __init__(self, store: EnterpriseStore | None = None):
        self.store = store or EnterpriseStore()

    def set_limit(self, limit: BudgetLimit) -> None:
        data = self.store.read("budgets", {"items": []})
        data["items"] = [
            item
            for item in data["items"]
            if not (item["subject_type"] == limit.subject_type and item["subject_id"] == limit.subject_id and item["period"] == limit.period)
        ]
        data["items"].append(asdict(limit))
        self.store.write("budgets", data)

    def charge(self, subject_type: str, subject_id: str, amount_usd: float, *, period: str = "monthly") -> PolicyDecision:
        data = self.store.read("budgets", {"items": []})
        for item in data["items"]:
            if item["subject_type"] == subject_type and item["subject_id"] == subject_id and item["period"] == period:
                projected = float(item["spent_usd"]) + amount_usd
                if projected > float(item["limit_usd"]):
                    return PolicyDecision("deny", False, ("budget_hard_limit",), evidence={"projected": projected})
                if item.get("approval_threshold_usd") and projected > float(item["approval_threshold_usd"]):
                    return PolicyDecision("require_approval", False, ("budget_approval_threshold",), evidence={"projected": projected})
                item["spent_usd"] = projected
                self.store.write("budgets", data)
                return PolicyDecision("allow", True, ("budget_available",), evidence={"spent": projected})
        return PolicyDecision("deny", False, ("budget_missing",))


class ComplianceExportService:
    def __init__(self, store: EnterpriseStore | None = None):
        self.store = store or EnterpriseStore()

    def export(self) -> dict[str, Any]:
        return {
            "version": ENTERPRISE_STATE_VERSION,
            "generated_at": time.time(),
            "access_records": self.store.read("identities", {"items": []})["items"],
            "policy_decisions": self.store.read("audit", {"records": []})["records"],
            "approvals": self.store.read("approvals", {"requests": [], "decisions": []}),
            "budgets": self.store.read("budgets", {"items": []})["items"],
            "credentials": SecretBroker(self.store).list_redacted(),
            "audit_chain_valid": EnterpriseAuditService(self.store).verify_chain(),
        }


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if any(token in key.lower() for token in ("secret", "token", "password", "key")):
                redacted[key] = SECRET_REDACTION
            else:
                redacted[key] = _redact(item)
        return redacted
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value
