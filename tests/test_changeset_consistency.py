"""Tests for ChangeSetConsistencyValidator (Sprint 8)."""
from __future__ import annotations

import pytest
from pathlib import Path

from nexus.multifile.contracts import (
    ChangeType,
    ContractChange,
    ContractType,
    EngineeringChangeSet,
    ImpactCategory,
    PlannedFileChange,
    Reference,
    SymbolReference,
    ValidationStatus,
)
from nexus.multifile.consistency import ChangeSetConsistencyValidator


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Stale callers
# ---------------------------------------------------------------------------


def test_definition_changed_caller_stale_detected(tmp_path):
    """ContractChange with a consumer not in change set → MissingChange."""
    consumer_ref = Reference(path="nexus/caller.py", symbol="render")
    cc = ContractChange(
        contract_id="cc-render",
        contract_type=ContractType.PUBLIC_FUNCTION,
        definition=SymbolReference(path="nexus/ui.py", symbol="render"),
        current_contract="render()",
        proposed_contract="render(theme: str)",
        consumers=[consumer_ref],
    )
    cs = EngineeringChangeSet(
        contract_changes=[cc],
        file_changes=[
            PlannedFileChange(path="nexus/ui.py", reason="Change render signature", change_type=ChangeType.MODIFY),
            # nexus/caller.py NOT included → should be flagged
        ],
    )
    validator = ChangeSetConsistencyValidator(repo_root=tmp_path)
    result = validator.validate(cs)
    assert result.status == ValidationStatus.FAIL
    assert any(mc.path == "nexus/caller.py" for mc in result.missing_changes)


# ---------------------------------------------------------------------------
# Stale imports after move
# ---------------------------------------------------------------------------


def test_exported_symbol_removed_import_remains(tmp_path):
    """After MOVE, a file that imports the old path is a stale reference."""
    # Create the importer
    _write(tmp_path / "nexus" / "core.py", "from nexus.helpers import do_stuff\n")

    cs = EngineeringChangeSet(
        file_changes=[
            PlannedFileChange(
                path="nexus/helpers.py",
                reason="Move helpers to utils",
                change_type=ChangeType.MOVE,
            )
            # core.py not updated — stale import
        ]
    )
    validator = ChangeSetConsistencyValidator(repo_root=tmp_path)
    result = validator.validate(cs)
    stale_paths = [r.path for r in result.stale_references]
    assert "nexus/core.py" in stale_paths


# ---------------------------------------------------------------------------
# Schema without migration
# ---------------------------------------------------------------------------


def test_schema_changed_migration_absent(tmp_path):
    """SCHEMA_CHANGE without MIGRATION triggers missing change."""
    cs = EngineeringChangeSet(
        file_changes=[
            PlannedFileChange(
                path="nexus/models.py",
                reason="Add field",
                change_type=ChangeType.SCHEMA_CHANGE,
            )
        ]
    )
    validator = ChangeSetConsistencyValidator(repo_root=tmp_path)
    result = validator.validate(cs)
    assert any(
        "migration" in mc.reason.lower() for mc in result.missing_changes
    ) or any("migration" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# New file package config
# ---------------------------------------------------------------------------


def test_new_file_missing_from_package_config_warning(tmp_path):
    """New .py file in package without __init__.py in change set → warning."""
    # Create the package __init__.py
    _write(tmp_path / "nexus" / "__init__.py", "")
    cs = EngineeringChangeSet(
        file_changes=[
            PlannedFileChange(
                path="nexus/new_module.py",
                reason="New module",
                change_type=ChangeType.CREATE,
            )
            # nexus/__init__.py NOT in change set → warning
        ]
    )
    validator = ChangeSetConsistencyValidator(repo_root=tmp_path)
    result = validator.validate(cs)
    assert any("__init__" in w or "export" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# Moved module stale reference
# ---------------------------------------------------------------------------


def test_moved_module_still_referenced_by_old_path(tmp_path):
    """After RENAME, old import path must not remain in unchanged files."""
    _write(tmp_path / "nexus" / "app.py", "from nexus.old_name import MyClass\n")
    cs = EngineeringChangeSet(
        file_changes=[
            PlannedFileChange(
                path="nexus/old_name.py",
                reason="Rename module",
                change_type=ChangeType.RENAME,
            )
            # nexus/app.py not updated
        ]
    )
    validator = ChangeSetConsistencyValidator(repo_root=tmp_path)
    result = validator.validate(cs)
    stale_paths = [r.path for r in result.stale_references]
    assert "nexus/app.py" in stale_paths


# ---------------------------------------------------------------------------
# Missing test change
# ---------------------------------------------------------------------------


def test_functional_change_missing_test_warning(tmp_path):
    """Functional modify with no test changes emits a warning."""
    cs = EngineeringChangeSet(
        file_changes=[
            PlannedFileChange(
                path="nexus/engine.py",
                reason="Refactor engine",
                change_type=ChangeType.MODIFY,
            )
        ]
    )
    validator = ChangeSetConsistencyValidator(repo_root=tmp_path)
    result = validator.validate(cs)
    assert any("test" in w.lower() for w in result.warnings)


# ---------------------------------------------------------------------------
# Clean change set passes
# ---------------------------------------------------------------------------


def test_clean_change_set_passes(tmp_path):
    """A well-formed change set with tests passes validation."""
    cs = EngineeringChangeSet(
        file_changes=[
            PlannedFileChange(
                path="nexus/service.py",
                reason="New service",
                change_type=ChangeType.CREATE,
            ),
            PlannedFileChange(
                path="tests/test_service.py",
                reason="Tests for new service",
                change_type=ChangeType.CREATE,
            ),
        ]
    )
    validator = ChangeSetConsistencyValidator(repo_root=tmp_path)
    result = validator.validate(cs)
    # May have warnings but no hard failures
    assert not result.missing_changes
    assert not result.stale_references
    assert not result.contract_mismatches
    # Note: scope violations possible if protected paths involved — but none here
    scope_hard = [v for v in result.scope_violations if v.violation_type != "UNEXPLAINED"]
    assert not scope_hard
