"""Canonical Task Contract for Nexus CLI (Sprint 6)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional


class TaskType(str, Enum):
    BUG_REPAIR = "bug_repair"
    FEATURE_IMPLEMENTATION = "feature_implementation"
    REFACTOR = "refactor"
    MIGRATION = "migration"
    DEPENDENCY_UPGRADE = "dependency_upgrade"
    TEST_CREATION = "test_creation"
    TEST_REPAIR = "test_repair"
    SECURITY_REMEDIATION = "security_remediation"
    PERFORMANCE_OPTIMIZATION = "performance_optimization"
    CONFIGURATION_CHANGE = "configuration_change"
    INVESTIGATION = "investigation"
    DOCUMENTATION = "documentation"
    CODE_EXPLANATION = "code_explanation"


class RiskLevel(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class RequirementSource(str, Enum):
    EXPLICIT_USER = "explicit_user"
    REPOSITORY_EVIDENCE = "repository_evidence"
    INFERRED = "inferred"
    DEFAULT_POLICY = "default_policy"


@dataclass
class Requirement:
    id: str
    statement: str
    source: RequirementSource
    mandatory: bool = True
    evidence_reference: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "statement": self.statement,
            "source": self.source.value if isinstance(self.source, RequirementSource) else self.source,
            "mandatory": self.mandatory,
            "evidence_reference": self.evidence_reference,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Requirement:
        return cls(
            id=data["id"],
            statement=data["statement"],
            source=RequirementSource(data["source"]) if isinstance(data["source"], str) else data["source"],
            mandatory=data.get("mandatory", True),
            evidence_reference=data.get("evidence_reference"),
        )


@dataclass
class Constraint:
    id: str
    description: str
    category: str = "general"
    is_prohibition: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Constraint:
        return cls(**data)


@dataclass
class Assumption:
    id: str
    statement: str
    evidence: str
    confidence: float = 0.8
    consequence_if_wrong: str = ""
    validation_step: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Assumption:
        return cls(**data)


@dataclass
class Question:
    id: str
    text: str
    is_blocking: bool
    category: str
    suggested_default: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Question:
        return cls(**data)


@dataclass
class TaskContract:
    task_id: str = field(default_factory=lambda: f"task-{uuid.uuid4().hex[:8]}")
    raw_user_request: str = ""
    normalized_objective: str = ""
    task_type: TaskType = TaskType.FEATURE_IMPLEMENTATION
    repository_snapshot_id: str = "snap-initial"
    mandatory_requirements: List[Requirement] = field(default_factory=list)
    optional_requirements: List[Requirement] = field(default_factory=list)
    constraints: List[Constraint] = field(default_factory=list)
    prohibited_changes: List[Constraint] = field(default_factory=list)
    assumptions: List[Assumption] = field(default_factory=list)
    unresolved_questions: List[Question] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    completion_definition: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "raw_user_request": self.raw_user_request,
            "normalized_objective": self.normalized_objective,
            "task_type": self.task_type.value if isinstance(self.task_type, TaskType) else self.task_type,
            "repository_snapshot_id": self.repository_snapshot_id,
            "mandatory_requirements": [r.to_dict() for r in self.mandatory_requirements],
            "optional_requirements": [r.to_dict() for r in self.optional_requirements],
            "constraints": [c.to_dict() for c in self.constraints],
            "prohibited_changes": [p.to_dict() for p in self.prohibited_changes],
            "assumptions": [a.to_dict() for a in self.assumptions],
            "unresolved_questions": [q.to_dict() for q in self.unresolved_questions],
            "risk_level": self.risk_level.value if isinstance(self.risk_level, RiskLevel) else self.risk_level,
            "completion_definition": self.completion_definition,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TaskContract:
        return cls(
            task_id=data.get("task_id", f"task-{uuid.uuid4().hex[:8]}"),
            raw_user_request=data.get("raw_user_request", ""),
            normalized_objective=data.get("normalized_objective", ""),
            task_type=TaskType(data["task_type"]) if "task_type" in data else TaskType.FEATURE_IMPLEMENTATION,
            repository_snapshot_id=data.get("repository_snapshot_id", "snap-initial"),
            mandatory_requirements=[Requirement.from_dict(r) for r in data.get("mandatory_requirements", [])],
            optional_requirements=[Requirement.from_dict(r) for r in data.get("optional_requirements", [])],
            constraints=[Constraint.from_dict(c) for c in data.get("constraints", [])],
            prohibited_changes=[Constraint.from_dict(p) for p in data.get("prohibited_changes", [])],
            assumptions=[Assumption.from_dict(a) for a in data.get("assumptions", [])],
            unresolved_questions=[Question.from_dict(q) for q in data.get("unresolved_questions", [])],
            risk_level=RiskLevel(data["risk_level"]) if "risk_level" in data else RiskLevel.LOW,
            completion_definition=data.get("completion_definition", ""),
        )
