"""
Unit Test Suite for Nexus CLI Recovery Intelligence (Sprint 7).
"""

from __future__ import annotations

import json
from decimal import Decimal
import pytest

from nexus.recovery import (
    AttemptSignature,
    BaselineAnalyzer,
    BuildLintTypeDiagnoser,
    DiagnosisEngine,
    EnvironmentDiagnoser,
    FailureCategory,
    FailureKind,
    FailureNormalizer,
    FailureRecord,
    FailureRelation,
    FailureSeverity,
    HypothesisStatus,
    LoopDetector,
    PatchQualityDiagnoser,
    RecoveryBudget,
    RecoveryController,
    RecoveryStrategyType,
    RollbackDecisionEngine,
    SignalExtractor,
    StrategyRegistry,
    TerminalState,
    TerminalStateGovernance,
    TestFailureDiagnoser,
    UserInterventionManager,
)


def test_failure_taxonomy_records():
    record = FailureRecord(
        failure_id="fail-101",
        run_id="run-101",
        category=FailureCategory.VERIFICATION,
        kind=FailureKind.TARGETED_TEST_FAILED,
        source_component="verifier",
        phase="verification",
        summary="Test assertion failed",
    )
    d = record.to_dict()
    assert d["failure_id"] == "fail-101"
    assert d["category"] == "verification"
    assert d["kind"] == "targeted_test_failed"


def test_failure_normalizer():
    raw_py = "FAILED tests/test_demo.py::test_fn - AssertionError: assert 1 == 2\nFile \"nexus/demo.py\", line 42"
    record = FailureNormalizer.normalize(raw_py, command="pytest", exit_code=1)
    assert record.category == FailureCategory.VERIFICATION
    assert record.kind == FailureKind.TARGETED_TEST_FAILED
    assert "test_fn" in record.failing_tests or "test_demo.py" in record.file_paths or record.summary


def test_signal_extractor():
    raw_stack = (
        "Traceback (most recent call last):\n"
        "  File \"nexus/core.py\", line 15, in run\n"
        "    result = process()\n"
        "TypeError: process() missing 1 required positional argument: 'config'"
    )
    signal = SignalExtractor.extract(raw_stack)
    assert signal.exception_type == "TypeError"
    assert "nexus/core.py" in signal.relevant_paths


def test_baseline_analyzer():
    failing = ["test_a", "test_b"]
    baseline = ["test_a"]
    rel = BaselineAnalyzer.analyze(failing, baseline_failures=baseline, target_tests=["test_b"])
    assert rel["test_a"] == FailureRelation.INHERITED
    assert rel["test_b"] == FailureRelation.PERSISTENT_TARGET_FAILURE


def test_diagnosis_engine():
    fail_rec = FailureNormalizer.normalize(
        "PermissionError: [Errno 13] Permission denied: '/tmp/locked'",
        source_component="tool",
    )
    diag = DiagnosisEngine.diagnose(fail_rec)
    assert diag.recommended_strategy == "REQUEST_MISSING_PERMISSION"
    assert len(diag.likely_root_causes) > 0


def test_strategy_registry():
    strat = StrategyRegistry.get(RecoveryStrategyType.REVISE_PLAN)
    assert strat.strategy_type == RecoveryStrategyType.REVISE_PLAN
    assert strat.max_repetitions > 0


def test_loop_detector():
    detector = LoopDetector(max_consecutive_identical=2)
    sig = AttemptSignature(
        plan_version=1,
        strategy_type="REVISE_PLAN",
        model="nova_codex",
        selected_context_hash="h1",
        target_files=["a.py"],
        patch_digest="d1",
        command="pytest",
        repo_state_hash="r1",
        failure_category="verification",
    )
    detector.record_attempt(sig)
    is_loop, msg = detector.is_looping(sig)
    assert not is_loop

    detector.record_attempt(sig)
    is_loop, msg = detector.is_looping(sig)
    assert is_loop
    assert "Identical recovery attempt strategy" in msg


def test_recovery_budget():
    budget = RecoveryBudget(max_command_retries=2)
    budget.command_retries = 1
    exhausted, _ = budget.is_exhausted()
    assert not exhausted

    budget.command_retries = 2
    exhausted, msg = budget.is_exhausted()
    assert exhausted
    assert "Command retry limit reached" in msg


def test_rollback_decision_engine():
    dec = RollbackDecisionEngine.evaluate(protected_path_changed=True)
    assert dec.should_rollback
    assert dec.immediate

    dec_clean = RollbackDecisionEngine.evaluate()
    assert not dec_clean.should_rollback


def test_patch_quality_diagnoser():
    bad_py = "def foo(\n  return 1"
    res = PatchQualityDiagnoser.analyze_patch(bad_py, "test.py")
    assert not res.is_valid
    assert "syntax error" in res.summary.lower()

    good_py = "def foo():\n    return 1\n"
    res_good = PatchQualityDiagnoser.analyze_patch(good_py, "test.py")
    assert res_good.is_valid


def test_environment_diagnoser():
    is_env = EnvironmentDiagnoser.is_environment_issue("sh: command not found: pytest")
    assert is_env


def test_terminal_state_governance():
    with pytest.raises(PermissionError):
        TerminalStateGovernance.validate_terminal_state(TerminalState.VERIFIED, is_canonical_verifier=False)

    val = TerminalStateGovernance.validate_terminal_state(TerminalState.VERIFIED, is_canonical_verifier=True)
    assert val == TerminalState.VERIFIED


def test_recovery_controller_flow(tmp_path):
    ctrl = RecoveryController(run_id="test-run-1", working_dir=str(tmp_path))
    strat, diag, term = ctrl.handle_failure(
        "FAILED tests/test_x.py::test_y - AssertionError: assert 1 == 2",
        source_component="test",
    )
    assert strat is not None
    assert diag is not None
    assert (tmp_path / ".nexus" / "runs" / "test-run-1" / "failures" / "failure-001.json").exists()
