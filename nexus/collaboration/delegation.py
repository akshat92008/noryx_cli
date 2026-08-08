"""
nexus/collaboration/delegation.py

Collaboration eligibility decision engine and DelegationPlanner.
Evaluates task characteristics, repository boundaries, dependency coupling,
budget constraints, and model capabilities to produce a deterministic
and model-assisted CollaborationDecision.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import List, Optional, Sequence

from nexus.collaboration.models import (
    AgentRole,
    CollaborationBudget,
    CollaborationDecision,
    CollaborationMode,
    CollaborationPolicyProfile,
    DelegationAssessment,
    RiskLevel,
)


@dataclass
class TaskCharacteristics:
    """Caller-provided signals describing the engineering task."""
    task_id: str
    description: str
    estimated_files_affected: int
    packages_involved: Sequence[str]
    languages_involved: Sequence[str]
    independent_workstreams: Sequence[str]
    sequential_dependencies: Sequence[str]
    estimated_context_tokens: int
    requires_security_review: bool
    requires_architecture_review: bool
    dependency_coupling_score: float   # 0.0 = independent, 1.0 = fully coupled
    time_budget_seconds: Optional[int]
    financial_budget_usd: Optional[float]
    local_only: bool
    worker_isolation_available: bool
    central_verifier_available: bool = True
    has_overlapping_symbol_edits: bool = False


# ---------------------------------------------------------------------------
# Constants & Thresholds
# ---------------------------------------------------------------------------

_MINIMUM_FILES_FOR_PARALLELISM = 3
_MINIMUM_WORKSTREAMS = 2
_COORDINATION_COST_PER_WORKER = 0.10
_MAX_COUPLING_FOR_PARALLEL = 0.4
_MINIMUM_CONTEXT_TOKENS_FOR_PARTITION = 8_000


class DelegationPlanner:
    """
    Determines whether collaboration adds measurable value over single-agent execution.
    Produces both legacy DelegationAssessment and canonical CollaborationDecision.
    """

    def __init__(
        self,
        policy: CollaborationPolicyProfile = CollaborationPolicyProfile.DISABLED,
        budget: Optional[CollaborationBudget] = None,
    ) -> None:
        self._policy = policy
        self._budget = budget

    def decide(
        self,
        task: TaskCharacteristics,
    ) -> CollaborationDecision:
        """
        Produce a canonical CollaborationDecision.
        Explicitly rejects collaboration when task is trivial, coupled, or un-isolatable.
        """
        reasons: List[str] = []

        # 1. Policy check
        if self._policy == CollaborationPolicyProfile.DISABLED:
            return CollaborationDecision(
                use_collaboration=False,
                recommended_mode=CollaborationMode.SINGLE_AGENT,
                reasons=("Collaboration policy is DISABLED.",),
                expected_benefit="0%",
                expected_overhead="0%",
                expected_cost=Decimal("0.00"),
                risk=RiskLevel.LOW,
                confidence=1.0,
            )

        # 2. Hard Rejection Criteria
        if not task.central_verifier_available:
            reasons.append("Central verification service is unavailable.")
            return CollaborationDecision(
                use_collaboration=False,
                recommended_mode=CollaborationMode.SINGLE_AGENT,
                reasons=tuple(reasons),
                expected_benefit="0%",
                expected_overhead="0%",
                expected_cost=Decimal("0.00"),
                risk=RiskLevel.HIGH,
                confidence=1.0,
            )

        if not task.worker_isolation_available:
            reasons.append("Worker workspace isolation is unavailable.")
            return CollaborationDecision(
                use_collaboration=False,
                recommended_mode=CollaborationMode.SINGLE_AGENT,
                reasons=tuple(reasons),
                expected_benefit="0%",
                expected_overhead="0%",
                expected_cost=Decimal("0.00"),
                risk=RiskLevel.HIGH,
                confidence=1.0,
            )

        if task.has_overlapping_symbol_edits:
            reasons.append("Mutating tasks edit overlapping symbols in the same module.")
            return CollaborationDecision(
                use_collaboration=False,
                recommended_mode=CollaborationMode.SINGLE_AGENT,
                reasons=tuple(reasons),
                expected_benefit="0%",
                expected_overhead="15%",
                expected_cost=Decimal("0.00"),
                risk=RiskLevel.HIGH,
                confidence=0.95,
            )

        if task.dependency_coupling_score > _MAX_COUPLING_FOR_PARALLEL:
            reasons.append(f"Task coupling score ({task.dependency_coupling_score:.2f}) exceeds threshold.")
            return CollaborationDecision(
                use_collaboration=False,
                recommended_mode=CollaborationMode.SINGLE_AGENT,
                reasons=tuple(reasons),
                expected_benefit="5%",
                expected_overhead="25%",
                expected_cost=Decimal("0.10"),
                risk=RiskLevel.MEDIUM,
                confidence=0.9,
            )

        if self._budget and self._budget.maximum_workers <= 1:
            reasons.append("Budget limits maximum workers to 1.")
            return CollaborationDecision(
                use_collaboration=False,
                recommended_mode=CollaborationMode.SINGLE_AGENT,
                reasons=tuple(reasons),
                expected_benefit="0%",
                expected_overhead="0%",
                expected_cost=Decimal("0.00"),
                risk=RiskLevel.LOW,
                confidence=1.0,
            )

        # 3. Small / Trivial Task Check
        if (
            task.estimated_files_affected < _MINIMUM_FILES_FOR_PARALLELISM
            and not task.requires_security_review
            and not task.requires_architecture_review
            and len(task.independent_workstreams) < 2
        ):
            reasons.append("Task is small or single-file fix; single-agent execution is more efficient.")
            return CollaborationDecision(
                use_collaboration=False,
                recommended_mode=CollaborationMode.SINGLE_AGENT,
                reasons=tuple(reasons),
                expected_benefit="0%",
                expected_overhead="30%",
                expected_cost=Decimal("0.05"),
                risk=RiskLevel.LOW,
                confidence=0.95,
            )

        # 4. Mode Selection & Benefit Assessment
        benefit_score = 0.0
        mode = CollaborationMode.SINGLE_AGENT

        if task.requires_security_review or task.requires_architecture_review:
            if len(task.independent_workstreams) <= 1:
                mode = CollaborationMode.REVIEW_PAIR
                benefit_score += 0.35
                reasons.append("Task requires independent review pair.")
            else:
                mode = CollaborationMode.SPECIALIST_TEAM
                benefit_score += 0.50
                reasons.append("Task requires specialist implementation and review team.")
        elif len(task.packages_involved) >= 2 and len(task.independent_workstreams) >= 2:
            mode = CollaborationMode.PARALLEL_IMPLEMENTATION
            benefit_score += 0.45
            reasons.append("Task spans multiple independent packages suitable for parallel implementation.")
        elif len(task.independent_workstreams) >= 2:
            mode = CollaborationMode.PARALLEL_ANALYSIS
            benefit_score += 0.30
            reasons.append("Task has multiple independent investigation streams.")
        else:
            mode = CollaborationMode.REVIEW_PAIR
            benefit_score += 0.25
            reasons.append("Task benefits from implementer + reviewer pair.")

        # Final decision calculation
        use_collab = benefit_score >= 0.25
        est_cost = Decimal(str(round(0.20 * (2 if mode == CollaborationMode.REVIEW_PAIR else 3), 2)))

        return CollaborationDecision(
            use_collaboration=use_collab,
            recommended_mode=mode if use_collab else CollaborationMode.SINGLE_AGENT,
            reasons=tuple(reasons),
            expected_benefit=f"{int(benefit_score * 100)}%",
            expected_overhead="15%",
            expected_cost=est_cost if use_collab else Decimal("0.00"),
            risk=RiskLevel.LOW if use_collab else RiskLevel.NONE,
            confidence=0.90,
        )

    def assess(
        self,
        task: TaskCharacteristics,
        evidence_ids: Sequence[str] = (),
    ) -> DelegationAssessment:
        """Legacy assessment method wrapping decide()."""
        decision = self.decide(task)

        parallelizable = list(task.independent_workstreams)
        if task.requires_security_review:
            parallelizable.append("security_review")
        if task.requires_architecture_review:
            parallelizable.append("architecture_review")

        max_workers = 0
        if decision.use_collaboration:
            if decision.recommended_mode == CollaborationMode.REVIEW_PAIR:
                max_workers = 2
            elif decision.recommended_mode == CollaborationMode.SPECIALIST_TEAM:
                max_workers = min(4, self._budget.maximum_workers if self._budget else 4)
            else:
                max_workers = min(len(parallelizable), self._budget.maximum_workers if self._budget else 4)

        return DelegationAssessment(
            collaboration_recommended=decision.use_collaboration,
            expected_benefit=float(decision.expected_benefit.rstrip("%")) / 100.0,
            coordination_cost=0.10 * max_workers,
            parallelizable_work=tuple(parallelizable if decision.use_collaboration else ()),
            sequential_dependencies=tuple(task.sequential_dependencies),
            risks=tuple(decision.reasons),
            maximum_workers=max_workers,
            evidence_ids=tuple(evidence_ids),
        )
