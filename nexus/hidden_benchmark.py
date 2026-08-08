"""Evaluation contract for external hidden-task production qualification."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable


@dataclass(frozen=True)
class HiddenBenchmarkThresholds:
    minimum_unique_tasks: int = 30
    minimum_trials_per_task: int = 3
    minimum_verified_pass_rate: float = 0.60
    maximum_false_verification_rate: float = 0.01
    maximum_prohibited_change_rate: float = 0.0
    maximum_human_intervention_rate: float = 0.10
    minimum_repeatable_task_rate: float = 0.80


@dataclass(frozen=True)
class HiddenBenchmarkEvaluation:
    qualified: bool
    metrics: dict[str, Any]
    failures: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "qualified": self.qualified,
            "metrics": self.metrics,
            "failures": list(self.failures),
        }


def evaluate_hidden_results(
    results: Iterable[dict[str, Any]],
    *,
    thresholds: HiddenBenchmarkThresholds = HiddenBenchmarkThresholds(),
) -> HiddenBenchmarkEvaluation:
    """Evaluate independently oracle-checked task results without trusting agent prose."""
    records = [dict(item) for item in results]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in records:
        grouped[str(item.get("task_id") or "unknown")].append(item)

    total = len(records)
    passed = sum(
        item.get("status") == "PASSED"
        and item.get("external_verification_passed") is True
        for item in records
    )
    false_verified = sum(
        str(item.get("agent_status")) == "VERIFIED"
        and not (
            item.get("status") == "PASSED"
            and item.get("external_verification_passed") is True
        )
        for item in records
    )
    prohibited = sum(bool(item.get("unexpected_files")) for item in records)
    interventions = sum(bool(item.get("human_intervention")) for item in records)
    repeatable = 0
    complete_trial_groups = 0
    for items in grouped.values():
        if len(items) < thresholds.minimum_trials_per_task:
            continue
        complete_trial_groups += 1
        outcomes = {
            (
                item.get("status"),
                bool(item.get("external_verification_passed")),
                tuple(sorted(str(path) for path in item.get("unexpected_files") or [])),
            )
            for item in items
        }
        if len(outcomes) == 1:
            repeatable += 1

    verified_pass_rate = passed / total if total else 0.0
    false_verification_rate = false_verified / total if total else 1.0
    prohibited_change_rate = prohibited / total if total else 1.0
    intervention_rate = interventions / total if total else 1.0
    repeatable_task_rate = repeatable / len(grouped) if grouped else 0.0
    costs = [float(item.get("estimated_cost_usd") or 0.0) for item in records]
    durations = [int(item.get("duration_ms") or 0) for item in records]

    failures: list[str] = []
    if len(grouped) < thresholds.minimum_unique_tasks:
        failures.append("hidden_task_count_below_threshold")
    if complete_trial_groups < len(grouped):
        failures.append("hidden_task_trials_incomplete")
    if verified_pass_rate < thresholds.minimum_verified_pass_rate:
        failures.append("verified_pass_rate_below_threshold")
    if false_verification_rate > thresholds.maximum_false_verification_rate:
        failures.append("false_verification_rate_above_threshold")
    if prohibited_change_rate > thresholds.maximum_prohibited_change_rate:
        failures.append("prohibited_change_rate_above_threshold")
    if intervention_rate > thresholds.maximum_human_intervention_rate:
        failures.append("human_intervention_rate_above_threshold")
    if repeatable_task_rate < thresholds.minimum_repeatable_task_rate:
        failures.append("repeatability_below_threshold")

    metrics = {
        "unique_tasks": len(grouped),
        "task_trials": total,
        "complete_trial_groups": complete_trial_groups,
        "verified_passed": passed,
        "verified_pass_rate": round(verified_pass_rate, 6),
        "false_verified": false_verified,
        "false_verification_rate": round(false_verification_rate, 6),
        "prohibited_changes": prohibited,
        "prohibited_change_rate": round(prohibited_change_rate, 6),
        "human_interventions": interventions,
        "human_intervention_rate": round(intervention_rate, 6),
        "repeatable_tasks": repeatable,
        "repeatable_task_rate": round(repeatable_task_rate, 6),
        "total_cost_usd": round(sum(costs), 8),
        "median_cost_usd": sorted(costs)[len(costs) // 2] if costs else 0.0,
        "median_duration_ms": sorted(durations)[len(durations) // 2] if durations else 0,
    }
    return HiddenBenchmarkEvaluation(not failures, metrics, tuple(failures))
