"""Tests for migration workflows (Sprint 8)."""
from __future__ import annotations

import pytest
from pathlib import Path

from nexus.multifile.migrations import (
    ConfigurationMigration,
    DependencyChange,
    FrameworkMigrationStage,
    MigrationKind,
    MigrationOrchestrator,
    MigrationStatus,
    SchemaMigration,
)
from nexus.multifile.contracts import CompatibilityPolicy, ChangeType


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    _write(tmp_path / "config" / "app.yaml", "db_host: localhost\nold_timeout: 30\n")
    _write(tmp_path / "nexus" / "settings.py", "OLD_TIMEOUT = 30\n")
    return tmp_path


# ---------------------------------------------------------------------------
# Configuration migration
# ---------------------------------------------------------------------------


def test_configuration_migration_rename_key(repo):
    """Config key rename produces planned changes for all referencing files."""
    orchestrator = MigrationOrchestrator(repo)
    migration = ConfigurationMigration(
        old_key="old_timeout",
        new_key="request_timeout",
    )
    plan = orchestrator.plan_configuration_migration(migration)
    assert plan.kind == MigrationKind.CONFIGURATION
    planned_paths = [fc.path for fc in plan.planned_file_changes]
    # config/app.yaml references old_timeout
    assert any("app.yaml" in p for p in planned_paths)


def test_configuration_migration_rollback_plan(repo):
    """Config migration plan includes a rollback plan."""
    orchestrator = MigrationOrchestrator(repo)
    migration = ConfigurationMigration(old_key="old_timeout", new_key="new_timeout")
    plan = orchestrator.plan_configuration_migration(migration)
    assert plan.rollback_plan.notes  # rollback description not empty


def test_configuration_migration_deprecation_warning(repo):
    """Deprecation warning is emitted when enabled."""
    orchestrator = MigrationOrchestrator(repo)
    migration = ConfigurationMigration(
        old_key="old_timeout", new_key="new_timeout", deprecation_warning=True
    )
    plan = orchestrator.plan_configuration_migration(migration)
    assert any("deprecation" in w.lower() for w in plan.warnings)


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------


def test_schema_migration_generates_migration_file(repo):
    """Schema migration generates a forward migration file path."""
    orchestrator = MigrationOrchestrator(repo)
    migration = SchemaMigration(
        migration_id="add_email_column",
        description="Add email column to users table",
        current_schema="users(id, name)",
        target_schema="users(id, name, email)",
        forward_migration="ALTER TABLE users ADD COLUMN email VARCHAR(255);",
        backward_migration="ALTER TABLE users DROP COLUMN email;",
    )
    plan = orchestrator.plan_schema_migration(migration)
    planned_paths = [fc.path for fc in plan.planned_file_changes]
    assert any("migration" in p.lower() for p in planned_paths)


def test_destructive_schema_migration_requires_approval(repo):
    """Destructive migrations are blocked until explicitly approved."""
    orchestrator = MigrationOrchestrator(repo)
    migration = SchemaMigration(
        migration_id="drop_users_table",
        description="Drop users table",
        current_schema="users(id, name)",
        target_schema="",
        forward_migration="DROP TABLE users;",
        backward_migration="",
        is_destructive=True,
    )
    plan = orchestrator.plan_schema_migration(migration, auto_approve=False)
    assert plan.status == MigrationStatus.REQUIRES_APPROVAL
    assert plan.requires_approval is True


def test_irreversible_migration_warning(repo):
    """Irreversible migration emits a warning about backups."""
    orchestrator = MigrationOrchestrator(repo)
    migration = SchemaMigration(
        migration_id="irreversible",
        description="Irreversible change",
        current_schema="old",
        target_schema="new",
        forward_migration="UPDATE ...",
        backward_migration="",
        is_reversible=False,
    )
    plan = orchestrator.plan_schema_migration(migration)
    assert any("not reversible" in w.lower() or "backup" in w.lower() for w in plan.warnings)


# ---------------------------------------------------------------------------
# Dependency upgrade/replacement
# ---------------------------------------------------------------------------


def test_dependency_upgrade_manifest_included(repo):
    """Dependency upgrade plan includes manifest file."""
    orchestrator = MigrationOrchestrator(repo)
    dep = DependencyChange(
        kind="UPGRADE",
        package_name="requests",
        version_before="2.28.0",
        version_after="2.32.0",
        manifest_files=["pyproject.toml"],
        lockfile="uv.lock",
    )
    plan = orchestrator.plan_dependency_change(dep)
    planned_paths = [fc.path for fc in plan.planned_file_changes]
    assert "pyproject.toml" in planned_paths


def test_lockfile_not_directly_editable(repo):
    """Lockfile edit warning is emitted."""
    orchestrator = MigrationOrchestrator(repo)
    dep = DependencyChange(
        kind="UPGRADE",
        package_name="pytest",
        version_before="7.0",
        version_after="8.0",
        manifest_files=["pyproject.toml"],
        lockfile="uv.lock",
    )
    plan = orchestrator.plan_dependency_change(dep)
    assert any("lockfile" in w.lower() or "uv.lock" in w for w in plan.warnings)


# ---------------------------------------------------------------------------
# Framework migration staging
# ---------------------------------------------------------------------------


def test_framework_migration_stage_bounded(repo):
    """A framework migration stage with too many files is blocked."""
    orchestrator = MigrationOrchestrator(repo)
    stage = FrameworkMigrationStage(
        stage_number=1,
        name="Mass migration",
        description="Too many files",
        files_modified=[f"nexus/file_{i}.py" for i in range(25)],  # > max
    )
    plan = orchestrator.plan_framework_migration_stage(stage, max_files_per_stage=20)
    assert plan.status == MigrationStatus.BLOCKED


def test_framework_migration_stage_valid(repo):
    """A bounded framework migration stage produces a valid plan."""
    orchestrator = MigrationOrchestrator(repo)
    stage = FrameworkMigrationStage(
        stage_number=1,
        name="Convert routing",
        description="Migrate Flask routes to FastAPI",
        files_modified=["nexus/routes.py", "nexus/handlers.py"],
        verification_commands=["python -m pytest tests/ -x -q"],
        rollback_description="Restore Flask routes from checkpoint.",
    )
    plan = orchestrator.plan_framework_migration_stage(stage)
    assert plan.status == MigrationStatus.PLANNED
    assert plan.rollback_plan.notes

def test_failed_intermediate_stage_blocks_continuation(tmp_path):
    """StagedChangeSetExecutor does not run later stages after a mandatory one fails."""
    from nexus.multifile.contracts import ChangeStage, ChangeStageStatus, EngineeringChangeSet, PlannedFileChange, ChangeType
    from nexus.multifile.staged_execution import StagedChangeSetExecutor

    cs = EngineeringChangeSet(
        run_id="r1",
        file_changes=[
            PlannedFileChange(path="nexus/a.py", reason="a", change_type=ChangeType.MODIFY),
            PlannedFileChange(path="nexus/b.py", reason="b", change_type=ChangeType.MODIFY),
        ],
        stages=[
            ChangeStage(
                stage_id="stage-1",
                name="Stage 1",
                description="First stage",
                file_paths=["nexus/a.py"],
                mandatory=True,
                checkpoint_required=False,
                verification_commands=["false"],  # always fails
            ),
            ChangeStage(
                stage_id="stage-2",
                name="Stage 2",
                description="Second stage",
                file_paths=["nexus/b.py"],
                mandatory=True,
                checkpoint_required=False,
            ),
        ],
        acceptance_criteria=[],
    )

    executor = StagedChangeSetExecutor(tmp_path, run_dir=tmp_path / ".nexus")
    result = executor.execute(cs)
    # Stage 2 must be skipped because Stage 1 failed
    assert "stage-2" not in result.stages_completed
    assert "stage-1" in result.stages_failed
