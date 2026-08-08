"""Executable Acceptance Criteria Engine (Sprint 6)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Dict, List, Optional

from nexus.planning.task_contract import Requirement, TaskContract, RiskLevel


class VerificationType(str, Enum):
    COMMAND = "command"
    FILE_EXISTS = "file_exists"
    SYMBOL_PRESENT = "symbol_present"
    POLICY = "policy"


@dataclass
class VerificationStrategy:
    type: VerificationType = VerificationType.COMMAND
    command_intent: Optional[str] = None
    target_path: Optional[str] = None
    expected_output: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type.value if isinstance(self.type, VerificationType) else self.type,
            "command_intent": self.command_intent,
            "target_path": self.target_path,
            "expected_output": self.expected_output,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> VerificationStrategy:
        return cls(
            type=VerificationType(data["type"]) if "type" in data else VerificationType.COMMAND,
            command_intent=data.get("command_intent"),
            target_path=data.get("target_path"),
            expected_output=data.get("expected_output"),
        )


@dataclass
class AcceptanceCriterion:
    id: str
    statement: str
    mandatory: bool = True
    requirement_source: str = "explicit_user"
    verification: VerificationStrategy = field(default_factory=VerificationStrategy)
    expected_evidence: str = ""
    affected_scope: List[str] = field(default_factory=list)
    risk_level: RiskLevel = RiskLevel.LOW
    status: str = "PENDING"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "statement": self.statement,
            "mandatory": self.mandatory,
            "requirement_source": self.requirement_source,
            "verification": self.verification.to_dict(),
            "expected_evidence": self.expected_evidence,
            "affected_scope": self.affected_scope,
            "risk_level": self.risk_level.value if isinstance(self.risk_level, RiskLevel) else self.risk_level,
            "status": self.status,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AcceptanceCriterion:
        return cls(
            id=data["id"],
            statement=data["statement"],
            mandatory=data.get("mandatory", True),
            requirement_source=data.get("requirement_source", "explicit_user"),
            verification=VerificationStrategy.from_dict(data.get("verification", {})),
            expected_evidence=data.get("expected_evidence", ""),
            affected_scope=data.get("affected_scope", []),
            risk_level=RiskLevel(data["risk_level"]) if "risk_level" in data else RiskLevel.LOW,
            status=data.get("status", "PENDING"),
        )


class AcceptanceEngine:
    """Generates and validates executable acceptance criteria from TaskContract and repo context."""

    VAGUE_PATTERNS = [
        r"^works properly$",
        r"^works correctly$",
        r"^make robust$",
        r"^improve quality$",
        r"^no bugs$",
        r"^good design$",
    ]

    def is_vague(self, statement: str) -> bool:
        norm = statement.strip().lower()
        for pat in self.VAGUE_PATTERNS:
            if re.search(pat, norm):
                return True
        return False

    def generate_criteria(
        self, task_contract: TaskContract, repo_context: Optional[Dict[str, Any]] = None
    ) -> List[AcceptanceCriterion]:
        criteria: List[AcceptanceCriterion] = []
        idx = 1

        for req in task_contract.mandatory_requirements:
            if self.is_vague(req.statement):
                continue

            # Determine verification strategy based on task type and repo context
            tests = (repo_context or {}).get("tests", [])
            test_file = tests[0] if tests else "tests/test_verification.py"

            strat = VerificationStrategy(
                type=VerificationType.COMMAND,
                command_intent=f"Run test runner for requirement {req.id}",
                target_path=test_file,
                expected_output="all tests pass",
            )

            criteria.append(
                AcceptanceCriterion(
                    id=f"AC-{idx}",
                    statement=req.statement,
                    mandatory=True,
                    requirement_source=req.source.value if hasattr(req.source, "value") else str(req.source),
                    verification=strat,
                    expected_evidence="Command execution exit code 0 and test log summary",
                    affected_scope=(repo_context or {}).get("relevant_files", []),
                    risk_level=task_contract.risk_level,
                )
            )
            idx += 1

        # Fallback if no specific requirements mapped
        if not criteria:
            criteria.append(
                AcceptanceCriterion(
                    id="AC-1",
                    statement=task_contract.normalized_objective or task_contract.raw_user_request,
                    mandatory=True,
                    requirement_source="explicit_user",
                    verification=VerificationStrategy(
                        type=VerificationType.COMMAND,
                        command_intent="Run full regression test suite",
                        expected_output="0 exit code",
                    ),
                    expected_evidence="Test run output log",
                    affected_scope=[],
                    risk_level=task_contract.risk_level,
                )
            )

        return criteria
