"""Deterministic Plan Validator (Sprint 6)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from nexus.planning.engineering_plan import EngineeringPlan, PlanStep
from nexus.planning.task_contract import TaskContract, RiskLevel


class IssueSeverity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class ValidationIssue:
    code: str
    message: str
    severity: IssueSeverity
    target_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "severity": self.severity.value,
            "target_id": self.target_id,
        }


class DeterministicValidator:
    """Performs strict deterministic graph, file, policy, and schema validation on EngineeringPlan."""

    def __init__(self, root_dir: Optional[str] = None, protected_patterns: Optional[List[str]] = None):
        self.root_dir = Path(root_dir).resolve() if root_dir else Path.cwd().resolve()
        self.protected_patterns = protected_patterns or [
            "pyproject.toml",
            "setup.py",
            "SECURITY.md",
            ".env",
            "nexus/config/",
        ]

    def validate(
        self,
        plan: EngineeringPlan,
        task_contract: Optional[TaskContract] = None,
        allowed_tools: Optional[Set[str]] = None,
    ) -> List[ValidationIssue]:
        issues: List[ValidationIssue] = []

        # 1. Plan completeness checks
        if not plan.steps:
            issues.append(
                ValidationIssue(
                    code="EMPTY_PLAN",
                    message="EngineeringPlan contains no steps.",
                    severity=IssueSeverity.ERROR,
                )
            )
            return issues

        if not plan.acceptance_criteria:
            issues.append(
                ValidationIssue(
                    code="MISSING_CRITERIA",
                    message="EngineeringPlan must contain at least one acceptance criterion.",
                    severity=IssueSeverity.ERROR,
                )
            )

        # 2. Step dependency & graph checks
        step_ids = {s.step_id for s in plan.steps}
        seen_steps: Set[str] = set()

        for step in plan.steps:
            # Completion condition
            if not step.completion_condition or len(step.completion_condition.strip()) < 5:
                issues.append(
                    ValidationIssue(
                        code="MISSING_COMPLETION_CONDITION",
                        message=f"Step '{step.step_id}' lacks a clear completion condition.",
                        severity=IssueSeverity.ERROR,
                        target_id=step.step_id,
                    )
                )

            # Verification method
            if not step.verification_method:
                issues.append(
                    ValidationIssue(
                        code="MISSING_VERIFICATION",
                        message=f"Step '{step.step_id}' has no verification method defined.",
                        severity=IssueSeverity.ERROR,
                        target_id=step.step_id,
                    )
                )

            # Dependencies
            for dep in step.dependencies:
                if dep not in step_ids:
                    issues.append(
                        ValidationIssue(
                            code="UNKNOWN_DEPENDENCY",
                            message=f"Step '{step.step_id}' references non-existent dependency '{dep}'.",
                            severity=IssueSeverity.ERROR,
                            target_id=step.step_id,
                        )
                    )
                elif dep in seen_steps:
                    # Dep came before, valid
                    pass
                else:
                    # Forward dependency / potential cycle
                    issues.append(
                        ValidationIssue(
                            code="FORWARD_DEPENDENCY",
                            message=f"Step '{step.step_id}' references step '{dep}' which is defined later or circular.",
                            severity=IssueSeverity.WARNING,
                            target_id=step.step_id,
                        )
                    )

            # High risk rollback check
            if step.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL) and not step.rollback_strategy:
                issues.append(
                    ValidationIssue(
                        code="MISSING_ROLLBACK",
                        message=f"High-risk step '{step.step_id}' must specify a rollback strategy.",
                        severity=IssueSeverity.ERROR,
                        target_id=step.step_id,
                    )
                )

            # Tool availability check
            if allowed_tools:
                for tool in step.allowed_tools:
                    if tool not in allowed_tools:
                        issues.append(
                            ValidationIssue(
                                code="UNSUPPORTED_TOOL",
                                message=f"Step '{step.step_id}' requests tool '{tool}' which is not allowed in session policy.",
                                severity=IssueSeverity.ERROR,
                                target_id=step.step_id,
                            )
                        )

            # Target path & boundary checks
            for path_str in step.intended_targets + step.mutation_scope:
                try:
                    p = (self.root_dir / path_str).resolve()
                    if not str(p).startswith(str(self.root_dir)):
                        issues.append(
                            ValidationIssue(
                                code="PATH_OUT_OF_BOUNDS",
                                message=f"Step '{step.step_id}' targets path outside root directory: '{path_str}'.",
                                severity=IssueSeverity.ERROR,
                                target_id=step.step_id,
                            )
                        )
                    # Protected paths check
                    for prot in self.protected_patterns:
                        if prot in path_str and step.action_type == "mutate":
                            issues.append(
                                ValidationIssue(
                                    code="PROTECTED_FILE_MUTATION",
                                    message=f"Step '{step.step_id}' modifies protected file pattern '{prot}'. Explicit approval required.",
                                    severity=IssueSeverity.WARNING,
                                    target_id=step.step_id,
                                )
                            )
                except Exception as e:
                    issues.append(
                        ValidationIssue(
                            code="INVALID_PATH_FORMAT",
                            message=f"Step '{step.step_id}' has invalid path string '{path_str}': {e}",
                            severity=IssueSeverity.ERROR,
                            target_id=step.step_id,
                        )
                    )

            seen_steps.add(step.step_id)

        # 3. Task contract requirement coverage
        if task_contract:
            if task_contract.repository_snapshot_id != plan.repository_snapshot_id:
                issues.append(
                    ValidationIssue(
                        code="STALE_SNAPSHOT_MISMATCH",
                        message=f"Plan snapshot '{plan.repository_snapshot_id}' does not match task contract snapshot '{task_contract.repository_snapshot_id}'.",
                        severity=IssueSeverity.ERROR,
                    )
                )

        return issues
