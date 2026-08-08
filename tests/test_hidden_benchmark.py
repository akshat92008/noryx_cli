from __future__ import annotations

from nexus.hidden_benchmark import HiddenBenchmarkThresholds, evaluate_hidden_results


def _records(tasks: int = 30, trials: int = 3, *, passed: bool = True):
    output = []
    for task in range(tasks):
        for _trial in range(trials):
            output.append(
                {
                    "task_id": f"task-{task:03d}",
                    "status": "PASSED" if passed else "FAILED",
                    "agent_status": "VERIFIED" if passed else "FAILED",
                    "external_verification_passed": passed,
                    "unexpected_files": [],
                    "human_intervention": False,
                    "estimated_cost_usd": 0.01,
                    "duration_ms": 100,
                }
            )
    return output


def test_hidden_task_gate_requires_repeated_external_success():
    evaluation = evaluate_hidden_results(_records())
    assert evaluation.qualified
    assert evaluation.metrics["unique_tasks"] == 30
    assert evaluation.metrics["task_trials"] == 90
    assert evaluation.metrics["false_verification_rate"] == 0
    assert evaluation.metrics["prohibited_change_rate"] == 0


def test_hidden_task_gate_rejects_false_verification_and_scope_violation():
    records = _records()
    records[0].update(
        {
            "status": "FAILED",
            "agent_status": "VERIFIED",
            "external_verification_passed": False,
            "unexpected_files": ["forbidden.py"],
        }
    )
    evaluation = evaluate_hidden_results(records)
    assert not evaluation.qualified
    assert "false_verification_rate_above_threshold" in evaluation.failures
    assert "prohibited_change_rate_above_threshold" in evaluation.failures


def test_hidden_task_gate_rejects_missing_trials():
    evaluation = evaluate_hidden_results(
        _records(tasks=30, trials=2),
        thresholds=HiddenBenchmarkThresholds(minimum_trials_per_task=3),
    )
    assert not evaluation.qualified
    assert "hidden_task_trials_incomplete" in evaluation.failures
