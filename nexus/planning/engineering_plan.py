"""Canonical Engineering Plan Model for Nexus CLI (Sprint 6)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional

from nexus.planning.task_contract import RiskLevel, Assumption


class ActionType(str, Enum):
    INSPECT = "inspect"
    ANALYZE = "analyze"
    MUTATE = "mutate"
    VERIFY = "verify"
    ROLLBACK = "rollback"
    APPROVAL_GATE = "approval_gate"


class HypothesisStatus(str, Enum):
    PROPOSED = "proposed"
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNRESOLVED = "unresolved"


@dataclass
class EvidenceReference:
    source_type: str  # e.g., 'test_failure', 'git_diff', 'stack_trace', 'symbol_caller'
    reference_id: str
    summary: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EvidenceReference:
        return cls(**data)


@dataclass
class Hypothesis:
    hypothesis_id: str
    statement: str
    supporting_evidence: List[EvidenceReference] = field(default_factory=list)
    contradicting_evidence: List[EvidenceReference] = field(default_factory=list)
    confidence: float = 0.5
    validation_action: str = ""
    status: HypothesisStatus = HypothesisStatus.PROPOSED

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "statement": self.statement,
            "supporting_evidence": [e.to_dict() for e in self.supporting_evidence],
            "contradicting_evidence": [e.to_dict() for e in self.contradicting_evidence],
            "confidence": self.confidence,
            "validation_action": self.validation_action,
            "status": self.status.value if isinstance(self.status, HypothesisStatus) else self.status,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Hypothesis:
        return cls(
            hypothesis_id=data["hypothesis_id"],
            statement=data["statement"],
            supporting_evidence=[EvidenceReference.from_dict(e) for e in data.get("supporting_evidence", [])],
            contradicting_evidence=[EvidenceReference.from_dict(e) for e in data.get("contradicting_evidence", [])],
            confidence=data.get("confidence", 0.5),
            validation_action=data.get("validation_action", ""),
            status=HypothesisStatus(data["status"]) if "status" in data else HypothesisStatus.PROPOSED,
        )


@dataclass
class PlanStep:
    step_id: str
    title: str
    objective: str
    action_type: ActionType = ActionType.MUTATE
    dependencies: List[str] = field(default_factory=list)
    evidence_inputs: List[EvidenceReference] = field(default_factory=list)
    intended_targets: List[str] = field(default_factory=list)
    allowed_tools: List[str] = field(default_factory=list)
    mutation_scope: List[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    expected_outcome: str = ""
    completion_condition: str = ""
    verification_method: str = ""
    rollback_strategy: Optional[str] = None
    parallelizable: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "objective": self.objective,
            "action_type": self.action_type.value if isinstance(self.action_type, ActionType) else self.action_type,
            "dependencies": self.dependencies,
            "evidence_inputs": [e.to_dict() for e in self.evidence_inputs],
            "intended_targets": self.intended_targets,
            "allowed_tools": self.allowed_tools,
            "mutation_scope": self.mutation_scope,
            "risk_level": self.risk_level.value if isinstance(self.risk_level, RiskLevel) else self.risk_level,
            "expected_outcome": self.expected_outcome,
            "completion_condition": self.completion_condition,
            "verification_method": self.verification_method,
            "rollback_strategy": self.rollback_strategy,
            "parallelizable": self.parallelizable,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PlanStep:
        return cls(
            step_id=data["step_id"],
            title=data["title"],
            objective=data["objective"],
            action_type=ActionType(data["action_type"]) if "action_type" in data else ActionType.MUTATE,
            dependencies=data.get("dependencies", []),
            evidence_inputs=[EvidenceReference.from_dict(e) for e in data.get("evidence_inputs", [])],
            intended_targets=data.get("intended_targets", []),
            allowed_tools=data.get("allowed_tools", []),
            mutation_scope=data.get("mutation_scope", []),
            risk_level=RiskLevel(data["risk_level"]) if "risk_level" in data else RiskLevel.LOW,
            expected_outcome=data.get("expected_outcome", ""),
            completion_condition=data.get("completion_condition", ""),
            verification_method=data.get("verification_method", ""),
            rollback_strategy=data.get("rollback_strategy"),
            parallelizable=data.get("parallelizable", False),
        )


@dataclass
class EngineeringPlan:
    plan_id: str = field(default_factory=lambda: f"plan-{uuid.uuid4().hex[:8]}")
    task_contract_id: str = ""
    repository_snapshot_id: str = "snap-initial"
    context_bundle_id: str = "bundle-1"
    objective: str = ""
    root_cause_hypotheses: List[Hypothesis] = field(default_factory=list)
    affected_scope: List[str] = field(default_factory=list)
    architecture_constraints: List[str] = field(default_factory=list)
    steps: List[PlanStep] = field(default_factory=list)
    acceptance_criteria: List[Dict[str, Any]] = field(default_factory=list)
    verification_strategy: Dict[str, Any] = field(default_factory=dict)
    risk_assessment: Dict[str, Any] = field(default_factory=dict)
    estimated_cost: Optional[Dict[str, Any]] = None
    estimated_duration: Optional[Dict[str, Any]] = None
    approval_requirements: List[str] = field(default_factory=list)
    assumptions: List[Assumption] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)
    confidence: float = 0.9
    version: int = 1

    def to_dict(self) -> Dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "task_contract_id": self.task_contract_id,
            "repository_snapshot_id": self.repository_snapshot_id,
            "context_bundle_id": self.context_bundle_id,
            "objective": self.objective,
            "root_cause_hypotheses": [h.to_dict() for h in self.root_cause_hypotheses],
            "affected_scope": self.affected_scope,
            "architecture_constraints": self.architecture_constraints,
            "steps": [s.to_dict() for s in self.steps],
            "acceptance_criteria": self.acceptance_criteria,
            "verification_strategy": self.verification_strategy,
            "risk_assessment": self.risk_assessment,
            "estimated_cost": self.estimated_cost,
            "estimated_duration": self.estimated_duration,
            "approval_requirements": self.approval_requirements,
            "assumptions": [a.to_dict() for a in self.assumptions],
            "limitations": self.limitations,
            "confidence": self.confidence,
            "version": self.version,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> EngineeringPlan:
        return cls(
            plan_id=data.get("plan_id", f"plan-{uuid.uuid4().hex[:8]}"),
            task_contract_id=data.get("task_contract_id", ""),
            repository_snapshot_id=data.get("repository_snapshot_id", "snap-initial"),
            context_bundle_id=data.get("context_bundle_id", "bundle-1"),
            objective=data.get("objective", ""),
            root_cause_hypotheses=[Hypothesis.from_dict(h) for h in data.get("root_cause_hypotheses", [])],
            affected_scope=data.get("affected_scope", []),
            architecture_constraints=data.get("architecture_constraints", []),
            steps=[PlanStep.from_dict(s) for s in data.get("steps", [])],
            acceptance_criteria=data.get("acceptance_criteria", []),
            verification_strategy=data.get("verification_strategy", {}),
            risk_assessment=data.get("risk_assessment", {}),
            estimated_cost=data.get("estimated_cost"),
            estimated_duration=data.get("estimated_duration"),
            approval_requirements=data.get("approval_requirements", []),
            assumptions=[Assumption.from_dict(a) for a in data.get("assumptions", [])],
            limitations=data.get("limitations", []),
            confidence=data.get("confidence", 0.9),
            version=data.get("version", 1),
        )
