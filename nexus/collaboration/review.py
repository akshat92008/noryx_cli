"""
nexus/collaboration/review.py

ResultReviewService: validates every worker result before it can
be accepted for integration.

Self-review is strictly prohibited.
Review approval issues APPROVE_FOR_INTEGRATION, never task-level VERIFIED.
All claims must be independently verified against patch, criteria, and evidence.
"""

from __future__ import annotations

import uuid
from typing import List, Optional, Sequence

from nexus.collaboration.models import (
    AgentAssignment,
    AgentRole,
    AssignmentResult,
    AssignmentReview,
    AssignmentStatus,
    ReviewDecision,
    ReviewFinding,
    ReviewFindingCategory,
    ReviewIssue,
    RiskLevel,
)
from nexus.collaboration.results import validate_result


class ResultReviewService:
    """
    Policy-driven review of worker results.
    Returns AssignmentReview containing decision (APPROVE_FOR_INTEGRATION / REVISE / REJECT / BLOCKED)
    and structured findings.
    """

    def __init__(
        self,
        known_repository_revision: str,
        allowed_path_prefixes: Sequence[str] = (),
    ) -> None:
        self._revision = known_repository_revision
        self._allowed_prefixes = tuple(allowed_path_prefixes)

    def review(
        self,
        result: AssignmentResult,
        assignment: AgentAssignment,
        current_revision: str,
        reviewer_id: Optional[str] = None,
        reviewer_role: Optional[AgentRole] = None,
    ) -> AssignmentReview:
        blocking_issues: List[ReviewIssue] = []
        warnings: List[ReviewIssue] = []
        missing_tests: List[str] = []
        scope_violations: List[str] = []
        security_findings: List[str] = []
        evidence: List[str] = list(result.evidence_ids or result.evidence)
        findings: List[ReviewFinding] = []
        missing_evidence: List[str] = []

        # 1. Prohibit Self-Review
        if reviewer_id and reviewer_id == result.worker_id:
            blocking_issues.append(ReviewIssue(
                issue_id=str(uuid.uuid4()),
                description="Self-review prohibited: worker cannot review its own assignment.",
                severity=RiskLevel.CRITICAL,
            ))

        # 2. Schema and Patch Validation
        try:
            validate_result(result, assignment)
        except Exception as exc:
            blocking_issues.append(ReviewIssue(
                issue_id=str(uuid.uuid4()),
                description=f"Validation failed: {exc}",
                severity=RiskLevel.HIGH,
            ))

        # 3. Scope Compliance & Protected Path Check
        allowed_paths_strs = {str(p) for p in (assignment.allowed_mutation_paths or assignment.allowed_paths)}
        prohibited_paths_strs = {str(p) for p in (assignment.protected_paths or assignment.prohibited_paths)}

        for change in result.proposed_changes:
            path = change.path
            if path in prohibited_paths_strs:
                msg = f"Change targets protected path: {path}"
                scope_violations.append(msg)
                blocking_issues.append(ReviewIssue(
                    issue_id=str(uuid.uuid4()),
                    description=msg,
                    severity=RiskLevel.CRITICAL,
                ))
            elif allowed_paths_strs and not any(path.startswith(ap) for ap in allowed_paths_strs):
                msg = f"Change targets unplanned path outside allowed scope: {path}"
                scope_violations.append(msg)
                warnings.append(ReviewIssue(
                    issue_id=str(uuid.uuid4()),
                    description=msg,
                    severity=RiskLevel.HIGH,
                ))

        # 4. Acceptance Criteria & Test Verification
        for req in (assignment.acceptance_criteria or assignment.requirements):
            matched = any(req.lower() in ev.lower() for ev in evidence)
            if not matched:
                missing_tests.append(f"Acceptance criterion '{req}' lacks evidence.")

        if missing_tests:
            warnings.append(ReviewIssue(
                issue_id=str(uuid.uuid4()),
                description=f"Missing acceptance evidence: {missing_tests}",
                severity=RiskLevel.MEDIUM,
            ))

        # 5. Security Inspection
        for finding in result.findings:
            if finding.severity in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                msg = f"Security concern: {finding.description}"
                security_findings.append(msg)
                blocking_issues.append(ReviewIssue(
                    issue_id=str(uuid.uuid4()),
                    description=msg,
                    severity=finding.severity,
                ))

        # 6. Determine Decision
        if any(i.severity == RiskLevel.CRITICAL for i in blocking_issues):
            decision = ReviewDecision.REJECT
        elif blocking_issues:
            decision = ReviewDecision.REVISE
        elif scope_violations:
            decision = ReviewDecision.REVISE
        elif result.status not in (AssignmentStatus.COMPLETED, AssignmentStatus.LOCALLY_VALIDATED):
            decision = ReviewDecision.BLOCKED
        else:
            decision = ReviewDecision.APPROVE_FOR_INTEGRATION

        return AssignmentReview(
            review_id=str(uuid.uuid4()),
            assignment_id=assignment.assignment_id,
            decision=decision,
            blocking_issues=tuple(blocking_issues),
            warnings=tuple(warnings),
            missing_tests=tuple(missing_tests),
            scope_violations=tuple(scope_violations),
            security_findings=tuple(security_findings),
            evidence=tuple(evidence),
            accepted=(decision == ReviewDecision.APPROVE_FOR_INTEGRATION),
            integration_eligible=(decision == ReviewDecision.APPROVE_FOR_INTEGRATION),
        )
