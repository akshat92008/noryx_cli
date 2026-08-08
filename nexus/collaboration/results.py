"""
nexus/collaboration/results.py

Worker result schema, validation, patch inspection, and evidence verification.

Workers must not submit unrestricted prose as their only output.
All results must pass schema, syntax, placeholder, and evidence validation before acceptance.
"""

from __future__ import annotations

import ast
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Optional, Sequence, Tuple

from nexus.collaboration.models import (
    AgentAssignment,
    AssignmentResult,
    AssignmentStatus,
    ProposedChange,
    ResourceUsage,
    RiskLevel,
    WorkerFinding,
)

# Alias for backward compatibility
WorkerResult = AssignmentResult
WorkerResultStatus = AssignmentStatus


class ResultValidationError(ValueError):
    """Raised when a WorkerResult fails schema, patch, or evidence validation."""


_PLACEHOLDER_PATTERNS = [
    "pass",
    "todo",
    "fixme",
    "stub_pass",
    "notimplementederror",
    "placeholder",
]


def validate_result(result: AssignmentResult, assignment: Optional[AgentAssignment] = None) -> None:
    """
    Validates that a WorkerResult satisfies the minimum contract, contains no placeholder code,
    has real patch artifacts, and obeys assignment scope.
    Raises ResultValidationError on failure.
    """
    if not result.assignment_id:
        raise ResultValidationError("WorkerResult.assignment_id is required.")

    summary_text = result.summary or ""
    if not summary_text or len(summary_text.strip()) < 10:
        raise ResultValidationError(
            "WorkerResult.summary must be a substantive description (≥10 chars)."
        )

    if result.status in (AssignmentStatus.COMPLETED, AssignmentStatus.LOCALLY_VALIDATED):
        evidence_ids = result.evidence_ids or result.evidence
        if not evidence_ids:
            raise ResultValidationError(
                "COMPLETED result must provide at least one evidence_id."
            )

        # Check for mutation workers
        if assignment and assignment.mutation_policy.allowed and assignment.allowed_mutation_paths:
            if not result.proposed_changes and not result.patch_artifact:
                raise ResultValidationError(
                    "COMPLETED mutating worker result must provide real proposed_changes or patch_artifact."
                )

        # Check proposed changes for placeholders, syntax errors, and path boundaries
        for change in result.proposed_changes:
            if not change.path:
                raise ResultValidationError(
                    f"ProposedChange '{change.change_id}' missing required 'path'."
                )

            path_str = change.path
            diff_ref = change.diff_reference or ""

            # Check protected / allowed scope if assignment provided
            if assignment:
                prohibited = {str(p) for p in assignment.prohibited_paths}
                allowed = {str(p) for p in assignment.allowed_paths}
                if path_str in prohibited:
                    raise ResultValidationError(
                        f"ProposedChange targets prohibited path: {path_str}"
                    )
                if allowed and not any(path_str.startswith(ap) for ap in allowed):
                    raise ResultValidationError(
                        f"ProposedChange targets path outside allowed scope: {path_str}"
                    )

            # Check for placeholder code in diff or description
            content_to_check = (change.description + "\n" + diff_ref).lower()
            for pattern in _PLACEHOLDER_PATTERNS:
                if f"# {pattern}" in content_to_check or f"raise {pattern}" in content_to_check:
                    raise ResultValidationError(
                        f"ProposedChange on '{path_str}' contains placeholder code pattern '{pattern}'."
                    )

            # Python syntax check if diff contains python code snippet
            if path_str.endswith(".py") and diff_ref and diff_ref.startswith("+"):
                try:
                    added_lines = "\n".join(
                        line[1:] for line in diff_ref.splitlines() if line.startswith("+")
                    )
                    if added_lines.strip():
                        ast.parse(added_lines)
                except SyntaxError as exc:
                    raise ResultValidationError(
                        f"ProposedChange on '{path_str}' contains invalid Python syntax: {exc}"
                    ) from exc

    if result.status == AssignmentStatus.INVALID:
        return

    if not isinstance(result.cost, ResourceUsage):
        raise ResultValidationError("WorkerResult.cost must be a ResourceUsage instance.")


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------


def build_finding(
    description: str,
    severity: RiskLevel,
    evidence_ids: Tuple[str, ...] = (),
    affected_paths: Tuple[str, ...] = (),
) -> WorkerFinding:
    return WorkerFinding(
        finding_id=str(uuid.uuid4()),
        description=description,
        severity=severity,
        evidence_ids=evidence_ids,
        affected_paths=affected_paths,
    )


def build_proposed_change(
    path: str,
    description: str,
    diff_reference: Optional[str] = None,
    transaction_ref: Optional[str] = None,
) -> ProposedChange:
    return ProposedChange(
        change_id=str(uuid.uuid4()),
        path=path,
        description=description,
        diff_reference=diff_reference,
        transaction_ref=transaction_ref,
    )


def build_result(
    assignment_id: str,
    worker_id: str,
    status: AssignmentStatus,
    summary: str,
    findings: Tuple[WorkerFinding, ...] = (),
    proposed_changes: Tuple[ProposedChange, ...] = (),
    transaction_reference: Optional[str] = None,
    verification_results: Tuple[str, ...] = (),
    unresolved_questions: Tuple[str, ...] = (),
    risks: Tuple[str, ...] = (),
    evidence_ids: Tuple[str, ...] = (),
    model_calls: int = 0,
    tool_calls: int = 0,
    tokens_used: int = 0,
    cost_usd: Optional[Decimal] = None,
    wall_clock_seconds: float = 0.0,
    patch_artifact: Optional[str] = None,
) -> AssignmentResult:
    return AssignmentResult(
        assignment_id=assignment_id,
        worker_id=worker_id,
        status=status,
        summary=summary,
        findings=findings,
        proposed_changes=proposed_changes,
        transaction_reference=transaction_reference,
        verification_results=verification_results,
        unresolved_questions=unresolved_questions,
        risks=risks,
        evidence_ids=evidence_ids,
        patch_artifact=patch_artifact,
        cost=ResourceUsage(
            model_calls=model_calls,
            tool_calls=tool_calls,
            tokens_used=tokens_used,
            cost_usd=cost_usd,
            wall_clock_seconds=wall_clock_seconds,
        ),
    )
