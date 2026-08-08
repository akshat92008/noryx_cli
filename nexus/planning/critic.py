"""Independent Plan Critic Stage for Nexus CLI (Sprint 6)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional

from nexus.planning.engineering_plan import ActionType, EngineeringPlan, PlanStep
from nexus.planning.task_contract import TaskContract, RiskLevel
from nexus.planning.validator import DeterministicValidator, ValidationIssue, IssueSeverity


class CritiqueDecision(str, Enum):
    APPROVE = "APPROVE"
    APPROVE_WITH_WARNINGS = "APPROVE_WITH_WARNINGS"
    REVISE = "REVISE"
    BLOCK = "BLOCK"


@dataclass
class PlanIssue:
    issue_id: str
    category: str
    description: str
    severity: str
    recommended_action: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PlanIssue:
        return cls(**data)


@dataclass
class PlanCritique:
    critique_id: str = field(default_factory=lambda: f"critique-{uuid.uuid4().hex[:8]}")
    plan_id: str = ""
    plan_version: int = 1
    decision: CritiqueDecision = CritiqueDecision.APPROVE
    blocking_issues: List[PlanIssue] = field(default_factory=list)
    warnings: List[PlanIssue] = field(default_factory=list)
    missing_evidence: List[str] = field(default_factory=list)
    suggested_changes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "critique_id": self.critique_id,
            "plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "decision": self.decision.value if isinstance(self.decision, CritiqueDecision) else self.decision,
            "blocking_issues": [b.to_dict() for b in self.blocking_issues],
            "warnings": [w.to_dict() for w in self.warnings],
            "missing_evidence": self.missing_evidence,
            "suggested_changes": self.suggested_changes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PlanCritique:
        return cls(
            critique_id=data.get("critique_id", f"critique-{uuid.uuid4().hex[:8]}"),
            plan_id=data.get("plan_id", ""),
            plan_version=data.get("plan_version", 1),
            decision=CritiqueDecision(data["decision"]) if "decision" in data else CritiqueDecision.APPROVE,
            blocking_issues=[PlanIssue.from_dict(b) for b in data.get("blocking_issues", [])],
            warnings=[PlanIssue.from_dict(w) for w in data.get("warnings", [])],
            missing_evidence=data.get("missing_evidence", []),
            suggested_changes=data.get("suggested_changes", []),
        )


class PlanCritic:
    """Independent critic that evaluates initial plans against safety, scope, caller, and test requirements."""

    def __init__(
        self,
        validator: Optional[DeterministicValidator] = None,
        allowed_tools: Optional[set[str]] = None,
    ):
        self.validator = validator or DeterministicValidator()
        self.allowed_tools = set(allowed_tools or ())

    def critique(
        self,
        plan: EngineeringPlan,
        task_contract: Optional[TaskContract] = None,
        repo_context: Optional[Dict[str, Any]] = None,
    ) -> PlanCritique:
        blocking: List[PlanIssue] = []
        warnings: List[PlanIssue] = []
        missing_ev: List[str] = []
        suggestions: List[str] = []

        # 1. Run deterministic validation
        v_issues = self.validator.validate(
            plan,
            task_contract,
            allowed_tools=self.allowed_tools or None,
        )
        for vi in v_issues:
            issue = PlanIssue(
                issue_id=f"VI-{vi.code}",
                category="deterministic_validation",
                description=vi.message,
                severity=vi.severity.value,
                recommended_action=f"Resolve validation error '{vi.code}'",
            )
            if vi.severity == IssueSeverity.ERROR:
                blocking.append(issue)
            else:
                warnings.append(issue)

        # 2. Check for missing test strategy
        has_test_step = any(s.action_type in ("verify", ActionType.VERIFY) or "test" in s.title.lower() for s in plan.steps)
        if not has_test_step and not plan.verification_strategy.get("command"):
            warnings.append(
                PlanIssue(
                    issue_id="CRIT-MISSING-TEST-STEP",
                    category="test_strategy",
                    description="Plan does not contain an explicit test verification step.",
                    severity="WARNING",
                    recommended_action="Add an explicit test execution step before finalizing",
                )
            )
            suggestions.append("Add automated test run step to verification strategy")

        # 3. Check for broad rewrite / over-scoping
        if len(plan.affected_scope) > 10 and task_contract and task_contract.task_type == "bug_repair":
            blocking.append(
                PlanIssue(
                    issue_id="CRIT-OVERBROAD-REWRITE",
                    category="scope",
                    description=f"Bug repair task proposes modifying {len(plan.affected_scope)} files. Excessive scope.",
                    severity="ERROR",
                    recommended_action="Scope repair down to minimal affected components",
                )
            )

        # 4. Check hypothesis support for bug repairs
        if task_contract and task_contract.task_type == "bug_repair":
            if not plan.root_cause_hypotheses:
                warnings.append(
                    PlanIssue(
                        issue_id="CRIT-NO-HYPOTHESIS",
                        category="diagnosis",
                        description="Bug repair plan missing root-cause hypothesis.",
                        severity="WARNING",
                        recommended_action="Formulate candidate root cause before mutation",
                    )
                )

        # Determine decision
        if blocking:
            decision = CritiqueDecision.REVISE
        elif warnings:
            decision = CritiqueDecision.APPROVE_WITH_WARNINGS
        else:
            decision = CritiqueDecision.APPROVE

        return PlanCritique(
            plan_id=plan.plan_id,
            plan_version=plan.version,
            decision=decision,
            blocking_issues=blocking,
            warnings=warnings,
            missing_evidence=missing_ev,
            suggested_changes=suggestions,
        )
