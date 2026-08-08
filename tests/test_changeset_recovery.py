"""Tests for MultiFileRecoveryHandler (Sprint 8)."""
from __future__ import annotations

import pytest
from nexus.multifile.recovery import (
    MultiFileRecoveryHandler,
    RecoveryContext,
    RecoveryDecision,
)
from nexus.multifile.contracts import (
    ChangeType,
    EngineeringChangeSet,
    MissingChange,
    PlannedFileChange,
    Reference,
    ImpactCategory,
)


def _cs(**kwargs) -> EngineeringChangeSet:
    return EngineeringChangeSet(run_id="r1", change_set_id="cs-1", **kwargs)


# ---------------------------------------------------------------------------
# Missed caller → scope expansion
# ---------------------------------------------------------------------------


def test_missed_caller_triggers_scope_expansion():
    handler = MultiFileRecoveryHandler()
    cs = _cs(
        file_changes=[
            PlannedFileChange(path="nexus/api.py", reason="Change API", change_type=ChangeType.MODIFY)
        ]
    )
    context = RecoveryContext(
        stage_id="stage-1",
        failure_reason="TypeError: authenticate() missing 1 required argument",
        error_type="TYPEERROR",
        missed_callers=["nexus/login.py", "nexus/admin.py"],
    )
    action = handler.handle_stage_failure(cs, context)
    assert action.decision == RecoveryDecision.EXPAND_SCOPE
    assert any("nexus/login.py" in fc.path for fc in action.new_scope)
    assert any("nexus/admin.py" in fc.path for fc in action.new_scope)


def test_intermediate_verification_failure_rollback_stage():
    handler = MultiFileRecoveryHandler()
    cs = _cs(file_changes=[])
    context = RecoveryContext(
        stage_id="stage-2",
        failure_reason="FAILED tests/test_auth.py::test_login - AssertionError",
        error_type="TEST",
    )
    action = handler.handle_stage_failure(cs, context)
    assert action.decision == RecoveryDecision.ROLLBACK_STAGE
    assert action.rollback_target_stage == "stage-2"


def test_syntax_error_triggers_stage_rollback():
    handler = MultiFileRecoveryHandler()
    cs = _cs(file_changes=[])
    context = RecoveryContext(
        stage_id="stage-1",
        failure_reason="SyntaxError: invalid syntax",
        error_type="SYNTAX",
    )
    action = handler.handle_stage_failure(cs, context)
    assert action.decision == RecoveryDecision.ROLLBACK_STAGE


def test_import_error_triggers_dependency_reorder():
    handler = MultiFileRecoveryHandler()
    cs = _cs(file_changes=[])
    context = RecoveryContext(
        stage_id="stage-2",
        failure_reason="ImportError: cannot import name 'NewClass' from 'nexus.base'",
        error_type="IMPORTERROR",
    )
    action = handler.handle_stage_failure(cs, context)
    assert action.decision == RecoveryDecision.REVISE_DEPENDENCY_ORDER


def test_full_rollback_when_many_files_partially_modified():
    handler = MultiFileRecoveryHandler()
    cs = _cs(
        file_changes=[
            PlannedFileChange(path=f"nexus/file_{i}.py", reason="f", change_type=ChangeType.MODIFY,
                              relevant_symbols=["sym"])
            for i in range(10)
        ]
    )
    context = RecoveryContext(
        stage_id="stage-1",
        failure_reason="Unknown error",
        error_type="UNKNOWN",
        files_partially_modified=[f"nexus/file_{i}.py" for i in range(8)],
    )
    action = handler.handle_stage_failure(cs, context)
    assert action.decision == RecoveryDecision.ROLLBACK_FULL


def test_repeated_strategy_prevention():
    handler = MultiFileRecoveryHandler()
    cs = _cs(file_changes=[])
    context = RecoveryContext(
        stage_id="stage-1",
        failure_reason="Test failed",
        error_type="TEST",
    )
    # First failure
    handler.handle_stage_failure(cs, context)
    # Second failure with same type — loop detected
    action = handler.handle_stage_failure(cs, context)
    assert action.decision == RecoveryDecision.STOP_FAILED
    assert "loop" in action.description.lower() or "repeated" in action.description.lower()


def test_plan_revision_scope_expansion_bounded():
    """After MAX_SCOPE_EXPANSIONS, stop rather than expand infinitely."""
    handler = MultiFileRecoveryHandler(max_scope_expansions=1)
    cs = _cs(file_changes=[])

    context = RecoveryContext(
        stage_id="stage-1",
        failure_reason="TypeError: missing arg",
        error_type="TYPEERROR",
        missed_callers=["nexus/a.py"],
    )
    # First expansion allowed
    action1 = handler.handle_stage_failure(cs, context)
    assert action1.decision == RecoveryDecision.EXPAND_SCOPE

    # Second expansion → stop
    context2 = RecoveryContext(
        stage_id="stage-2",
        failure_reason="TypeError: missing arg",
        error_type="TYPEERROR",
        missed_callers=["nexus/b.py"],
    )
    action2 = handler.handle_stage_failure(cs, context2)
    assert action2.decision == RecoveryDecision.STOP_FAILED


def test_consistency_failure_with_missing_changes_expands_scope():
    handler = MultiFileRecoveryHandler()
    cs = _cs(file_changes=[])
    missing = [
        MissingChange(
            path="nexus/caller.py",
            reason="Caller not in change set",
            category=ImpactCategory.MUST_CHANGE,
        )
    ]
    action = handler.handle_consistency_failure(cs, missing, stale_references=[])
    assert action.decision == RecoveryDecision.EXPAND_SCOPE
    assert any(fc.path == "nexus/caller.py" for fc in action.new_scope)


def test_consistency_failure_with_stale_references_blocks():
    handler = MultiFileRecoveryHandler()
    cs = _cs(file_changes=[])
    stale = [Reference(path="nexus/a.py", symbol="old_import")]
    action = handler.handle_consistency_failure(cs, missing_changes=[], stale_references=stale)
    assert action.decision == RecoveryDecision.STOP_BLOCKED
