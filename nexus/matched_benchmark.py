"""Matched direct-vs-Nexus benchmark analysis.

A valid comparison keeps task, model, source revision, and authorized budget
identical.  This prevents a stronger model or larger budget from being credited
to the Nexus control plane.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TrialResult:
    task_id: str
    model: str
    source_revision: str
    budget_usd: float
    status: str
    verified: bool
    claimed_success: bool
    regressions: int = 0
    cost_usd: float = 0.0
    duration_seconds: float = 0.0
    human_interventions: int = 0

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TrialResult":
        return cls(
            task_id=str(data["task_id"]),
            model=str(data["model"]),
            source_revision=str(data["source_revision"]),
            budget_usd=float(data["budget_usd"]),
            status=str(data.get("status", "UNKNOWN")).upper(),
            verified=bool(data.get("verified", False)),
            claimed_success=bool(data.get("claimed_success", False)),
            regressions=max(0, int(data.get("regressions", 0))),
            cost_usd=max(0.0, float(data.get("cost_usd", 0.0))),
            duration_seconds=max(0.0, float(data.get("duration_seconds", 0.0))),
            human_interventions=max(0, int(data.get("human_interventions", 0))),
        )

    @property
    def key(self) -> tuple[str, str, str, float]:
        return (self.task_id, self.model, self.source_revision, round(self.budget_usd, 8))


@dataclass(frozen=True)
class ComparisonThresholds:
    minimum_trials: int = 6
    minimum_uplift: float = 1.5
    maximum_false_completion_rate: float = 0.01
    maximum_regression_rate: float = 0.0
    minimum_budget_compliance: float = 0.99


@dataclass
class MatchedComparisonReport:
    status: str
    passed: bool
    matched_trials: int
    direct_verified_successes: int
    nexus_verified_successes: int
    direct_success_rate: float
    nexus_success_rate: float
    uplift: float | None
    nexus_false_completions: int
    nexus_false_completion_rate: float
    nexus_regression_rate: float
    nexus_budget_compliance: float
    nexus_average_cost_usd: float
    direct_average_cost_usd: float
    failures: list[str] = field(default_factory=list)
    unmatched_direct: list[str] = field(default_factory=list)
    unmatched_nexus: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_trials(path: str | Path) -> list[TrialResult]:
    source = Path(path).expanduser().resolve()
    data = json.loads(source.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("trials", data.get("results", []))
    if not isinstance(data, list):
        raise ValueError(f"Matched benchmark file must contain a trial list: {source}")
    return [TrialResult.from_dict(item) for item in data if isinstance(item, dict)]


def compare_matched(
    direct_trials: list[TrialResult],
    nexus_trials: list[TrialResult],
    *,
    thresholds: ComparisonThresholds | None = None,
) -> MatchedComparisonReport:
    limits = thresholds or ComparisonThresholds()
    direct_by_key = {item.key: item for item in direct_trials}
    nexus_by_key = {item.key: item for item in nexus_trials}
    keys = sorted(set(direct_by_key) & set(nexus_by_key))
    unmatched_direct = [item.task_id for key, item in direct_by_key.items() if key not in nexus_by_key]
    unmatched_nexus = [item.task_id for key, item in nexus_by_key.items() if key not in direct_by_key]

    direct = [direct_by_key[key] for key in keys]
    nexus = [nexus_by_key[key] for key in keys]
    count = len(keys)
    direct_successes = sum(item.verified for item in direct)
    nexus_successes = sum(item.verified for item in nexus)
    direct_rate = direct_successes / count if count else 0.0
    nexus_rate = nexus_successes / count if count else 0.0
    uplift = None if direct_rate == 0 else nexus_rate / direct_rate
    false_completions = sum(item.claimed_success and not item.verified for item in nexus)
    false_rate = false_completions / count if count else 0.0
    regression_rate = sum(item.regressions > 0 for item in nexus) / count if count else 0.0
    budget_compliance = sum(item.cost_usd <= item.budget_usd for item in nexus) / count if count else 0.0
    nexus_cost = sum(item.cost_usd for item in nexus) / count if count else 0.0
    direct_cost = sum(item.cost_usd for item in direct) / count if count else 0.0

    failures: list[str] = []
    if count < limits.minimum_trials:
        failures.append(f"matched_trials:{count}<{limits.minimum_trials}")
    if direct_rate == 0:
        failures.append("uplift_undefined:direct_verified_success_rate_is_zero")
    elif uplift is not None and uplift < limits.minimum_uplift:
        failures.append(f"uplift:{uplift:.3f}<{limits.minimum_uplift:.3f}")
    if false_rate > limits.maximum_false_completion_rate:
        failures.append(
            f"false_completion_rate:{false_rate:.4f}>{limits.maximum_false_completion_rate:.4f}"
        )
    if regression_rate > limits.maximum_regression_rate:
        failures.append(f"regression_rate:{regression_rate:.4f}>{limits.maximum_regression_rate:.4f}")
    if budget_compliance < limits.minimum_budget_compliance:
        failures.append(
            f"budget_compliance:{budget_compliance:.4f}<{limits.minimum_budget_compliance:.4f}"
        )
    if unmatched_direct or unmatched_nexus:
        failures.append("unmatched_trials_present")

    status = "PASS" if not failures else ("INSUFFICIENT_EVIDENCE" if count < limits.minimum_trials else "FAIL")
    return MatchedComparisonReport(
        status=status,
        passed=not failures,
        matched_trials=count,
        direct_verified_successes=direct_successes,
        nexus_verified_successes=nexus_successes,
        direct_success_rate=direct_rate,
        nexus_success_rate=nexus_rate,
        uplift=uplift,
        nexus_false_completions=false_completions,
        nexus_false_completion_rate=false_rate,
        nexus_regression_rate=regression_rate,
        nexus_budget_compliance=budget_compliance,
        nexus_average_cost_usd=nexus_cost,
        direct_average_cost_usd=direct_cost,
        failures=failures,
        unmatched_direct=sorted(unmatched_direct),
        unmatched_nexus=sorted(unmatched_nexus),
    )
