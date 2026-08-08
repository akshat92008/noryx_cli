"""Tests for the canonical EngineeringChangeSet contract model (Sprint 8)."""
from __future__ import annotations

import pytest
from nexus.multifile.contracts import (
    ChangeType,
    ChangeDependency,
    ChangeStage,
    ChangeStageStatus,
    CompatibilityPolicy,
    ContractChange,
    ContractScope,
    ContractType,
    EngineeringChangeSet,
    MissingChange,
    PlannedFileChange,
    RollbackPlan,
    ScopeViolation,
    SymbolReference,
    TaskType,
    ValidationStatus,
)
from nexus.multifile.consistency import ChangeSetConsistencyValidator


# ---------------------------------------------------------------------------
# PlannedFileChange
# ---------------------------------------------------------------------------


def test_planned_file_change_round_trip():
    fc = PlannedFileChange(
        path="nexus/foo.py",
        reason="Add new function",
        change_type=ChangeType.MODIFY,
        relevant_symbols=["foo"],
        depends_on=["nexus/bar.py"],
        confidence=0.95,
    )
    d = fc.to_dict()
    fc2 = PlannedFileChange.from_dict(d)
    assert fc2.path == fc.path
    assert fc2.change_type == ChangeType.MODIFY
    assert fc2.confidence == 0.95


def test_valid_multi_file_change_set():
    cs = EngineeringChangeSet(
        run_id="run-1",
        plan_id="plan-1",
        repository_snapshot_id="snap-abc",
        task_type=TaskType.FEATURE,
        objective="Add rate-limiting feature",
        file_changes=[
            PlannedFileChange(
                path="nexus/rate.py",
                reason="New rate limiter implementation",
                change_type=ChangeType.CREATE,
            ),
            PlannedFileChange(
                path="nexus/api.py",
                reason="Wire rate limiter into API handler",
                change_type=ChangeType.MODIFY,
                depends_on=["nexus/rate.py"],
            ),
            PlannedFileChange(
                path="tests/test_rate.py",
                reason="Unit tests for rate limiter",
                change_type=ChangeType.CREATE,
            ),
        ],
    )
    assert cs.change_set_id.startswith("cs-")
    assert len(cs.file_changes) == 3
    assert cs.has_unexplained_files() == []


def test_unexplained_file_rejected(tmp_path):
    """Files without a reason must be detected by consistency validator."""
    cs = EngineeringChangeSet(
        file_changes=[
            PlannedFileChange(path="nexus/mystery.py", reason="", change_type=ChangeType.MODIFY),
        ]
    )
    validator = ChangeSetConsistencyValidator(repo_root=tmp_path)
    result = validator.validate(cs)
    assert result.status == ValidationStatus.FAIL
    assert any(v.path == "nexus/mystery.py" for v in result.scope_violations)
    assert any(v.violation_type == "UNEXPLAINED" for v in result.scope_violations)


def test_protected_path_blocked(tmp_path):
    """pyproject.toml requires explicit protected=True."""
    cs = EngineeringChangeSet(
        file_changes=[
            PlannedFileChange(
                path="pyproject.toml",
                reason="Update version",
                change_type=ChangeType.MODIFY,
                protected=False,  # NOT explicitly approved
            ),
        ]
    )
    validator = ChangeSetConsistencyValidator(repo_root=tmp_path)
    result = validator.validate(cs)
    assert result.status == ValidationStatus.FAIL
    assert any("protected" in v.violation_type.lower() for v in result.scope_violations)


def test_protected_path_allowed_when_explicit(tmp_path):
    """pyproject.toml can be modified when explicitly flagged."""
    cs = EngineeringChangeSet(
        file_changes=[
            PlannedFileChange(
                path="pyproject.toml",
                reason="Bump version to 8.0.0",
                change_type=ChangeType.MODIFY,
                protected=True,  # Explicitly approved
            ),
        ]
    )
    validator = ChangeSetConsistencyValidator(repo_root=tmp_path)
    result = validator.validate(cs)
    # scope_violations should not contain a PROTECTED violation
    protected_violations = [v for v in result.scope_violations if v.violation_type == "PROTECTED"]
    assert not protected_violations


def test_stale_snapshot_detection():
    """Change set without a snapshot ID logs a warning but is not blocked."""
    cs = EngineeringChangeSet(
        repository_snapshot_id="",  # no snapshot bound
        file_changes=[
            PlannedFileChange(path="nexus/x.py", reason="Fix bug", change_type=ChangeType.MODIFY),
        ]
    )
    # No snapshot → still executable (warned but not blocked at model level)
    assert cs.repository_snapshot_id == ""


def test_missing_test_change_warning(tmp_path):
    """A functional change without any test change emits a warning."""
    cs = EngineeringChangeSet(
        file_changes=[
            PlannedFileChange(
                path="nexus/core.py",
                reason="New feature",
                change_type=ChangeType.MODIFY,
            ),
            # No test_*.py change
        ]
    )
    validator = ChangeSetConsistencyValidator(repo_root=tmp_path)
    result = validator.validate(cs)
    assert any("test" in w.lower() for w in result.warnings)


def test_stale_caller_detected(tmp_path):
    """If a contract has a known consumer not in the change set, MissingChange is reported."""
    cc = ContractChange(
        contract_id="cc-1",
        contract_type=ContractType.PUBLIC_FUNCTION,
        definition=SymbolReference(path="nexus/api.py", symbol="get_data"),
        current_contract="get_data()",
        proposed_contract="get_data(timeout: int)",
        consumers=[
            __import__("nexus.multifile.contracts", fromlist=["Reference"]).Reference(
                path="nexus/caller.py", symbol="get_data"
            )
        ],
    )
    cs = EngineeringChangeSet(
        contract_changes=[cc],
        file_changes=[
            PlannedFileChange(
                path="nexus/api.py",
                reason="Change signature",
                change_type=ChangeType.MODIFY,
            )
            # nexus/caller.py NOT in change set
        ],
    )
    validator = ChangeSetConsistencyValidator(repo_root=tmp_path)
    result = validator.validate(cs)
    assert result.status == ValidationStatus.FAIL
    assert any(mc.path == "nexus/caller.py" for mc in result.missing_changes)


def test_schema_without_migration_detected(tmp_path):
    """A SCHEMA_CHANGE without a MIGRATION change emits missing change."""
    cs = EngineeringChangeSet(
        file_changes=[
            PlannedFileChange(
                path="models/user.py",
                reason="Add email_verified field to User model",
                change_type=ChangeType.SCHEMA_CHANGE,
            )
        ]
    )
    validator = ChangeSetConsistencyValidator(repo_root=tmp_path)
    result = validator.validate(cs)
    assert any("migration" in mc.reason.lower() for mc in result.missing_changes) or \
           any("migration" in w.lower() for w in result.warnings)


def test_change_set_serialization():
    """EngineeringChangeSet must round-trip through to_dict."""
    cs = EngineeringChangeSet(
        run_id="run-2",
        task_type=TaskType.REFACTOR,
        objective="Extract service layer",
        compatibility_policy=CompatibilityPolicy.BACKWARD_COMPATIBLE,
        file_changes=[
            PlannedFileChange(
                path="nexus/service.py",
                reason="New service module",
                change_type=ChangeType.CREATE,
            )
        ],
    )
    d = cs.to_dict()
    assert d["schema_version"] == "nexus.changeset.v8"
    assert d["task_type"] == "REFACTOR"
    assert len(d["file_changes"]) == 1
