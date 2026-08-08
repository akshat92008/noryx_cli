"""
nexus/collaboration/policies.py

Collaboration budget and policy management.

Defaults:
  - Multi-agent collaboration DISABLED by default (conservative).
  - Investigation-only may be enabled before parallel mutation.
  - Parallel mutation requires explicit configuration.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Optional

from nexus.collaboration.models import CollaborationBudget, CollaborationPolicyProfile

# ---------------------------------------------------------------------------
# Default budgets per policy profile
# ---------------------------------------------------------------------------

def default_budget(profile: CollaborationPolicyProfile) -> CollaborationBudget:
    """Return the conservative default budget for a given policy profile."""
    if profile == CollaborationPolicyProfile.DISABLED:
        return CollaborationBudget(
            maximum_workers=0,
            maximum_parallel_workers=0,
            maximum_total_model_calls=0,
            maximum_total_tool_calls=0,
            maximum_total_tokens=0,
            maximum_total_cost_usd=Decimal("0.00"),
            maximum_wall_clock_seconds=0,
            maximum_worker_retries=0,
            maximum_reassignments=0,
        )

    if profile == CollaborationPolicyProfile.INVESTIGATION_ONLY:
        return CollaborationBudget(
            maximum_workers=3,
            maximum_parallel_workers=3,
            maximum_total_model_calls=30,
            maximum_total_tool_calls=120,
            maximum_total_tokens=200_000,
            maximum_total_cost_usd=Decimal("0.50"),
            maximum_wall_clock_seconds=300,
            maximum_worker_retries=1,
            maximum_reassignments=1,
        )

    if profile == CollaborationPolicyProfile.REVIEW_ONLY:
        return CollaborationBudget(
            maximum_workers=2,
            maximum_parallel_workers=2,
            maximum_total_model_calls=20,
            maximum_total_tool_calls=60,
            maximum_total_tokens=100_000,
            maximum_total_cost_usd=Decimal("0.30"),
            maximum_wall_clock_seconds=180,
            maximum_worker_retries=1,
            maximum_reassignments=1,
        )

    if profile == CollaborationPolicyProfile.CONTROLLED_PARALLEL:
        return CollaborationBudget(
            maximum_workers=4,
            maximum_parallel_workers=2,
            maximum_total_model_calls=60,
            maximum_total_tool_calls=240,
            maximum_total_tokens=400_000,
            maximum_total_cost_usd=Decimal("2.00"),
            maximum_wall_clock_seconds=600,
            maximum_worker_retries=2,
            maximum_reassignments=2,
        )

    # CUSTOM — caller must provide their own budget
    return CollaborationBudget(
        maximum_workers=2,
        maximum_parallel_workers=1,
        maximum_total_model_calls=20,
        maximum_total_tool_calls=80,
        maximum_total_tokens=100_000,
        maximum_total_cost_usd=None,
        maximum_wall_clock_seconds=300,
        maximum_worker_retries=1,
        maximum_reassignments=1,
    )


# ---------------------------------------------------------------------------
# Policy validator
# ---------------------------------------------------------------------------


class PolicyViolation(RuntimeError):
    pass


class CollaborationPolicyEngine:
    """
    Validates proposed collaboration actions against the active policy.
    Returns clear error messages rather than silently allowing violations.
    """

    def __init__(
        self,
        profile: CollaborationPolicyProfile,
        budget: Optional[CollaborationBudget] = None,
        local_only: bool = False,
    ) -> None:
        self.profile = profile
        self.budget = budget or default_budget(profile)
        self.local_only = local_only

    def check_collaboration_enabled(self) -> None:
        if self.profile == CollaborationPolicyProfile.DISABLED:
            raise PolicyViolation(
                "Multi-agent collaboration is disabled by policy. "
                "Set collaboration.policy to INVESTIGATION_ONLY, REVIEW_ONLY, "
                "CONTROLLED_PARALLEL, or CUSTOM to enable."
            )

    def check_mutation_allowed(self) -> None:
        if self.profile in (
            CollaborationPolicyProfile.INVESTIGATION_ONLY,
            CollaborationPolicyProfile.REVIEW_ONLY,
        ):
            raise PolicyViolation(
                f"Policy '{self.profile.value}' does not allow mutation workers. "
                "Use CONTROLLED_PARALLEL or CUSTOM for parallel mutation."
            )

    def check_worker_count(self, requested: int) -> None:
        if requested > self.budget.maximum_workers:
            raise PolicyViolation(
                f"Requested {requested} workers exceeds policy maximum "
                f"({self.budget.maximum_workers})."
            )

    def check_parallel_count(self, parallel: int) -> None:
        if parallel > self.budget.maximum_parallel_workers:
            raise PolicyViolation(
                f"Requested {parallel} parallel workers exceeds policy maximum "
                f"({self.budget.maximum_parallel_workers})."
            )

    def check_cloud_routing(self, worker_id: str) -> None:
        if self.local_only:
            raise PolicyViolation(
                f"Worker '{worker_id}' attempted cloud routing but local_only=True. "
                "Route to a local model."
            )

    def check_recursive_delegation(self, requester_role_can_create: bool) -> None:
        if not requester_role_can_create:
            raise PolicyViolation(
                "Worker attempted recursive delegation. "
                "Only roles with can_create_workers=True may spawn sub-workers, "
                "and only when explicitly permitted by policy."
            )

    def check_budget_remaining(
        self,
        spent_model_calls: int,
        spent_tool_calls: int,
        spent_tokens: int,
        spent_cost_usd: Optional[Decimal] = None,
    ) -> None:
        if spent_model_calls >= self.budget.maximum_total_model_calls:
            raise PolicyViolation(
                f"Total model calls ({spent_model_calls}) reached policy maximum "
                f"({self.budget.maximum_total_model_calls})."
            )
        if spent_tool_calls >= self.budget.maximum_total_tool_calls:
            raise PolicyViolation(
                f"Total tool calls ({spent_tool_calls}) reached policy maximum "
                f"({self.budget.maximum_total_tool_calls})."
            )
        if spent_tokens >= self.budget.maximum_total_tokens:
            raise PolicyViolation(
                f"Total tokens ({spent_tokens}) reached policy maximum "
                f"({self.budget.maximum_total_tokens})."
            )
        if (
            self.budget.maximum_total_cost_usd is not None
            and spent_cost_usd is not None
            and spent_cost_usd >= self.budget.maximum_total_cost_usd
        ):
            raise PolicyViolation(
                f"Total cost (${spent_cost_usd}) reached policy maximum "
                f"(${self.budget.maximum_total_cost_usd})."
            )
