from nexus.matched_benchmark import ComparisonThresholds, TrialResult, compare_matched


def _trial(task_id: str, verified: bool, *, claimed: bool | None = None, cost: float = 0.5):
    return TrialResult(
        task_id=task_id,
        model="affordable-model-v1",
        source_revision="abc123",
        budget_usd=1.0,
        status="VERIFIED" if verified else "FAILED",
        verified=verified,
        claimed_success=verified if claimed is None else claimed,
        cost_usd=cost,
    )


def test_matched_comparison_passes_only_identical_trial_keys():
    direct = [_trial(f"t{i}", i < 2) for i in range(6)]
    nexus = [_trial(f"t{i}", i < 4) for i in range(6)]
    report = compare_matched(direct, nexus)
    assert report.passed
    assert report.uplift == 2.0
    assert report.nexus_false_completion_rate == 0.0


def test_matched_comparison_fails_false_completion_and_unmatched_trials():
    direct = [_trial(f"t{i}", i < 2) for i in range(6)]
    nexus = [_trial(f"t{i}", i < 3, claimed=True) for i in range(5)]
    nexus.append(_trial("different", True))
    report = compare_matched(
        direct,
        nexus,
        thresholds=ComparisonThresholds(minimum_uplift=1.0),
    )
    assert not report.passed
    assert "unmatched_trials_present" in report.failures
    assert report.nexus_false_completions > 0
