"""
Recovery Budget Governance for Nexus CLI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class RecoveryBudget:
    max_command_retries: int = 3
    max_tool_retries: int = 5
    max_plan_revisions: int = 3
    max_context_expansions: int = 3
    max_mutation_cycles: int = 5
    max_verification_cycles: int = 5
    max_model_escalations: int = 2
    max_elapsed_seconds: int | None = 300
    max_cost: Decimal | None = None

    # Current usage counters
    command_retries: int = 0
    tool_retries: int = 0
    plan_revisions: int = 0
    context_expansions: int = 0
    mutation_cycles: int = 0
    verification_cycles: int = 0
    model_escalations: int = 0
    elapsed_seconds: float = 0.0
    accumulated_cost: Decimal = field(default_factory=lambda: Decimal("0.00"))

    def is_exhausted(self) -> tuple[bool, str]:
        if self.command_retries >= self.max_command_retries:
            return True, f"Command retry limit reached ({self.command_retries}/{self.max_command_retries})."
        if self.tool_retries >= self.max_tool_retries:
            return True, f"Tool retry limit reached ({self.tool_retries}/{self.max_tool_retries})."
        if self.plan_revisions >= self.max_plan_revisions:
            return True, f"Plan revision limit reached ({self.plan_revisions}/{self.max_plan_revisions})."
        if self.context_expansions >= self.max_context_expansions:
            return True, f"Context expansion limit reached ({self.context_expansions}/{self.max_context_expansions})."
        if self.mutation_cycles >= self.max_mutation_cycles:
            return True, f"Mutation cycle limit reached ({self.mutation_cycles}/{self.max_mutation_cycles})."
        if self.verification_cycles >= self.max_verification_cycles:
            return True, f"Verification cycle limit reached ({self.verification_cycles}/{self.max_verification_cycles})."
        if self.model_escalations >= self.max_model_escalations:
            return True, f"Model escalation limit reached ({self.model_escalations}/{self.max_model_escalations})."
        if self.max_elapsed_seconds and self.elapsed_seconds >= self.max_elapsed_seconds:
            return True, f"Elapsed time limit reached ({self.elapsed_seconds:.1f}s/{self.max_elapsed_seconds}s)."
        if self.max_cost and self.accumulated_cost >= self.max_cost:
            return True, f"Cost limit reached (${self.accumulated_cost}/${self.max_cost})."

        return False, ""

    def to_dict(self) -> dict:
        return {
            "limits": {
                "max_command_retries": self.max_command_retries,
                "max_tool_retries": self.max_tool_retries,
                "max_plan_revisions": self.max_plan_revisions,
                "max_context_expansions": self.max_context_expansions,
                "max_mutation_cycles": self.max_mutation_cycles,
                "max_verification_cycles": self.max_verification_cycles,
                "max_model_escalations": self.max_model_escalations,
                "max_elapsed_seconds": self.max_elapsed_seconds,
                "max_cost": str(self.max_cost) if self.max_cost else None,
            },
            "usage": {
                "command_retries": self.command_retries,
                "tool_retries": self.tool_retries,
                "plan_revisions": self.plan_revisions,
                "context_expansions": self.context_expansions,
                "mutation_cycles": self.mutation_cycles,
                "verification_cycles": self.verification_cycles,
                "model_escalations": self.model_escalations,
                "elapsed_seconds": round(self.elapsed_seconds, 2),
                "accumulated_cost": str(self.accumulated_cost),
            },
        }
