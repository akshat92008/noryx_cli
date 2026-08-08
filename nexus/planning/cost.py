"""Cost and Effort Estimation Interface (Sprint 6)."""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Any, Dict, Optional
from nexus.planning.engineering_plan import EngineeringPlan


@dataclass
class PlanCostEstimate:
    estimated_model_calls: int = 2
    estimated_context_tokens: int = 4000
    estimated_tool_calls: int = 3
    estimated_test_commands: int = 1
    complexity_class: str = "MODERATE"
    estimated_latency_seconds: float = 12.0
    estimated_usd_cost: float = 0.005

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> PlanCostEstimate:
        return cls(**data)


class CostEstimator:
    """Computes bounded resource, token, cost, and latency estimates for engineering plans."""

    def estimate(self, plan: EngineeringPlan) -> PlanCostEstimate:
        step_count = len(plan.steps)
        if step_count <= 1:
            complexity = "TRIVIAL"
            model_calls = 1
            tokens = 2000
            tool_calls = 2
            latency = 4.0
            cost = 0.002
        elif step_count <= 4:
            complexity = "MODERATE"
            model_calls = 3
            tokens = 6000
            tool_calls = 6
            latency = 12.0
            cost = 0.008
        else:
            complexity = "COMPLEX"
            model_calls = step_count + 2
            tokens = 15000
            tool_calls = step_count * 3
            latency = 30.0
            cost = 0.025

        return PlanCostEstimate(
            estimated_model_calls=model_calls,
            estimated_context_tokens=tokens,
            estimated_tool_calls=tool_calls,
            estimated_test_commands=max(1, len(plan.acceptance_criteria)),
            complexity_class=complexity,
            estimated_latency_seconds=latency,
            estimated_usd_cost=cost,
        )
