"""
Migration Orchestrator — Sprint 8.

Safe planning and execution infrastructure for:
- Configuration key migrations (rename, move, default change)
- Schema migration file generation
- Dependency replacement workflows
- Framework migration staging

Invariants:
- Irreversible / destructive operations require explicit approval.
- Returns BLOCKED rather than simulating success when infrastructure unavailable.
- Production database migrations are NEVER executed automatically.
- All migration plans include forward + backward migration.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from nexus.multifile.contracts import (
    ChangeType,
    CompatibilityPolicy,
    PlannedFileChange,
    RollbackPlan,
    RollbackScope,
)

logger = logging.getLogger(__name__)


class MigrationStatus(str, Enum):
    PLANNED = "PLANNED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    ROLLED_BACK = "ROLLED_BACK"
    BLOCKED = "BLOCKED"          # infrastructure unavailable or requires approval
    REQUIRES_APPROVAL = "REQUIRES_APPROVAL"


class MigrationKind(str, Enum):
    CONFIGURATION = "CONFIGURATION"
    SCHEMA = "SCHEMA"
    DEPENDENCY_UPGRADE = "DEPENDENCY_UPGRADE"
    DEPENDENCY_REPLACEMENT = "DEPENDENCY_REPLACEMENT"
    FRAMEWORK = "FRAMEWORK"


@dataclass
class ConfigurationMigration:
    """A plan for migrating a configuration key."""
    old_key: str
    new_key: str
    config_files: list[str] = field(default_factory=list)
    parser: str = ""                 # e.g. "toml", "yaml", "ini", "env"
    default_before: str = ""
    default_after: str = ""
    deprecation_warning: bool = True
    compatibility_policy: CompatibilityPolicy = CompatibilityPolicy.DEPRECATION_WINDOW
    backward_compat_shim: str = ""   # code to support both old and new key
    documentation_files: list[str] = field(default_factory=list)
    environment_variables: list[str] = field(default_factory=list)


@dataclass
class SchemaMigration:
    """A plan for a schema/database migration."""
    migration_id: str
    description: str
    current_schema: str
    target_schema: str
    forward_migration: str    # SQL or ORM commands
    backward_migration: str   # rollback SQL or ORM commands
    is_reversible: bool = True
    is_destructive: bool = False
    requires_production_approval: bool = False
    test_database_required: bool = True
    application_compatibility_window: str = ""  # e.g. "both v1 and v2 of app must work"
    rollback_plan: str = ""


@dataclass
class DependencyChange:
    """A planned dependency upgrade or replacement."""
    kind: str  # UPGRADE | REPLACE
    package_name: str
    version_before: str
    version_after: str  # or replacement package name for REPLACE
    manifest_files: list[str] = field(default_factory=list)
    lockfile: str = ""
    api_changes: list[str] = field(default_factory=list)
    import_renames: dict[str, str] = field(default_factory=dict)  # old → new
    source_adaptations_required: bool = False
    security_implications: list[str] = field(default_factory=list)


@dataclass
class FrameworkMigrationStage:
    """One bounded phase of a framework migration."""
    stage_number: int
    name: str
    description: str
    files_modified: list[str] = field(default_factory=list)
    package_changes: list[str] = field(default_factory=list)
    verification_commands: list[str] = field(default_factory=list)
    checkpoint_name: str = ""
    rollback_description: str = ""
    milestone_state: dict[str, Any] = field(default_factory=dict)


@dataclass
class MigrationPlan:
    """Unified migration plan container."""
    plan_id: str
    kind: MigrationKind
    description: str
    status: MigrationStatus = MigrationStatus.PLANNED
    stages: list[FrameworkMigrationStage] = field(default_factory=list)
    planned_file_changes: list[PlannedFileChange] = field(default_factory=list)
    rollback_plan: RollbackPlan = field(default_factory=RollbackPlan)
    warnings: list[str] = field(default_factory=list)
    requires_approval: bool = False
    approval_reason: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class MigrationOrchestrator:
    """Plans and coordinates all migration types."""

    def __init__(self, repo_root: str | Path) -> None:
        self.repo_root = Path(repo_root)

    # ------------------------------------------------------------------
    # Configuration migration
    # ------------------------------------------------------------------

    def plan_configuration_migration(
        self,
        migration: ConfigurationMigration,
    ) -> MigrationPlan:
        """Plan a configuration key rename/restructure."""
        import uuid
        plan = MigrationPlan(
            plan_id=f"config-mig-{uuid.uuid4().hex[:8]}",
            kind=MigrationKind.CONFIGURATION,
            description=f"Rename config key '{migration.old_key}' → '{migration.new_key}'",
        )

        # Discover all files referencing the old key
        referencing_files = self._find_config_references(migration.old_key)
        migration.config_files = referencing_files

        for file_path in referencing_files:
            plan.planned_file_changes.append(
                PlannedFileChange(
                    path=file_path,
                    reason=f"Update config key '{migration.old_key}' → '{migration.new_key}'",
                    change_type=ChangeType.CONFIGURATION_CHANGE,
                    relevant_symbols=[migration.old_key, migration.new_key],
                )
            )

        if migration.deprecation_warning:
            plan.warnings.append(
                f"Add deprecation warning for old key '{migration.old_key}' "
                "before removing it — allows consumers time to migrate."
            )

        if migration.environment_variables:
            for env_var in migration.environment_variables:
                plan.warnings.append(
                    f"Environment variable '{env_var}' may need to be renamed or mapped."
                )

        plan.rollback_plan = RollbackPlan(
            scope=RollbackScope.FULL_CHANGE_SET,
            notes=f"Restore all config files to use '{migration.old_key}'",
        )

        return plan

    # ------------------------------------------------------------------
    # Schema migration
    # ------------------------------------------------------------------

    def plan_schema_migration(
        self,
        migration: SchemaMigration,
        *,
        auto_approve: bool = False,
    ) -> MigrationPlan:
        """Plan a database/schema migration. Never executes production migrations."""
        import uuid
        plan = MigrationPlan(
            plan_id=f"schema-mig-{uuid.uuid4().hex[:8]}",
            kind=MigrationKind.SCHEMA,
            description=migration.description,
        )

        # Destructive or production operations require explicit approval
        if migration.is_destructive or migration.requires_production_approval:
            if not auto_approve:
                plan.status = MigrationStatus.REQUIRES_APPROVAL
                plan.requires_approval = True
                plan.approval_reason = (
                    "Destructive schema migration or production database access "
                    "requires explicit user approval before execution."
                )
                plan.warnings.append(plan.approval_reason)
                return plan

        if not migration.is_reversible:
            plan.warnings.append(
                f"Migration '{migration.migration_id}' is NOT reversible. "
                "Ensure a full database backup exists before applying."
            )

        # Generate migration file change
        migration_filename = (
            f"migrations/{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
            f"_{migration.migration_id}.sql"
        )
        plan.planned_file_changes.append(
            PlannedFileChange(
                path=migration_filename,
                reason=f"Forward migration: {migration.description}",
                change_type=ChangeType.MIGRATION,
            )
        )

        if migration.is_reversible:
            rollback_filename = migration_filename.replace(".sql", "_rollback.sql")
            plan.planned_file_changes.append(
                PlannedFileChange(
                    path=rollback_filename,
                    reason=f"Backward migration (rollback): {migration.description}",
                    change_type=ChangeType.MIGRATION,
                )
            )

        plan.rollback_plan = RollbackPlan(
            scope=RollbackScope.FULL_CHANGE_SET,
            notes=migration.backward_migration or "Run rollback migration file.",
        )

        return plan

    # ------------------------------------------------------------------
    # Dependency upgrade/replacement
    # ------------------------------------------------------------------

    def plan_dependency_change(
        self,
        dep: DependencyChange,
    ) -> MigrationPlan:
        """Plan a dependency upgrade or replacement."""
        import uuid
        plan = MigrationPlan(
            plan_id=f"dep-{uuid.uuid4().hex[:8]}",
            kind=(
                MigrationKind.DEPENDENCY_UPGRADE
                if dep.kind == "UPGRADE"
                else MigrationKind.DEPENDENCY_REPLACEMENT
            ),
            description=(
                f"{'Upgrade' if dep.kind == 'UPGRADE' else 'Replace'} "
                f"'{dep.package_name}' "
                f"{dep.version_before} → {dep.version_after}"
            ),
        )

        # 1. Manifest files
        for manifest in dep.manifest_files:
            plan.planned_file_changes.append(
                PlannedFileChange(
                    path=manifest,
                    reason=f"Update {dep.package_name} version in manifest",
                    change_type=ChangeType.CONFIGURATION_CHANGE,
                    relevant_symbols=[dep.package_name],
                )
            )

        # 2. Source adaptations
        if dep.import_renames:
            stale_imports = self._find_stale_imports(list(dep.import_renames.keys()))
            for file_path in stale_imports:
                plan.planned_file_changes.append(
                    PlannedFileChange(
                        path=file_path,
                        reason=f"Update imports for {dep.package_name} API change",
                        change_type=ChangeType.MODIFY,
                        relevant_symbols=list(dep.import_renames.keys()),
                    )
                )

        # 3. Lockfile warning (never edit manually)
        if dep.lockfile:
            plan.warnings.append(
                f"Do NOT manually edit '{dep.lockfile}'. "
                "Regenerate via package manager after updating manifest."
            )

        # 4. Security implications
        for sec in dep.security_implications:
            plan.warnings.append(f"Security consideration: {sec}")

        plan.rollback_plan = RollbackPlan(
            scope=RollbackScope.FULL_CHANGE_SET,
            notes=f"Restore {dep.package_name} to {dep.version_before} in manifest and regenerate lockfile.",
        )

        return plan

    # ------------------------------------------------------------------
    # Framework migration
    # ------------------------------------------------------------------

    def plan_framework_migration_stage(
        self,
        stage: FrameworkMigrationStage,
        *,
        max_files_per_stage: int = 20,
    ) -> MigrationPlan:
        """Plan a single bounded phase of a framework migration.

        Large framework migrations MUST be staged — do not attempt the
        entire migration in one model turn.
        """
        import uuid
        plan = MigrationPlan(
            plan_id=f"fw-stage-{stage.stage_number:02d}-{uuid.uuid4().hex[:6]}",
            kind=MigrationKind.FRAMEWORK,
            description=f"Framework migration stage {stage.stage_number}: {stage.name}",
        )

        if len(stage.files_modified) > max_files_per_stage:
            plan.status = MigrationStatus.BLOCKED
            plan.warnings.append(
                f"Stage has {len(stage.files_modified)} files which exceeds the "
                f"max_files_per_stage limit of {max_files_per_stage}. "
                "Split into smaller stages."
            )
            return plan

        for file_path in stage.files_modified:
            plan.planned_file_changes.append(
                PlannedFileChange(
                    path=file_path,
                    reason=f"Framework migration stage {stage.stage_number}: {stage.name}",
                    change_type=ChangeType.MODIFY,
                )
            )

        plan.stages = [stage]
        plan.rollback_plan = RollbackPlan(
            scope=RollbackScope.STAGE,
            notes=stage.rollback_description or f"Restore stage {stage.stage_number} checkpoint.",
        )

        return plan

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _find_config_references(self, key: str) -> list[str]:
        """Find files containing a configuration key."""
        results: list[str] = []
        pattern = re.compile(re.escape(key))
        config_extensions = {".toml", ".yaml", ".yml", ".json", ".ini", ".cfg", ".env"}

        for f in self.repo_root.rglob("*"):
            if not f.is_file():
                continue
            rel = str(f.relative_to(self.repo_root))
            if any(skip in rel for skip in (".git", ".venv", "__pycache__")):
                continue
            if f.suffix in config_extensions or "config" in f.name.lower():
                try:
                    if pattern.search(f.read_text(encoding="utf-8", errors="replace")):
                        results.append(rel)
                except OSError:
                    pass

        return results

    def _find_stale_imports(self, old_names: list[str]) -> list[str]:
        """Find Python files importing old names."""
        results: list[str] = []
        patterns = [re.compile(r"\b" + re.escape(n) + r"\b") for n in old_names]

        for py_file in self.repo_root.rglob("*.py"):
            rel = str(py_file.relative_to(self.repo_root))
            if ".venv" in rel or "__pycache__" in rel:
                continue
            try:
                content = py_file.read_text(encoding="utf-8", errors="replace")
                if any(p.search(content) for p in patterns):
                    results.append(rel)
            except OSError:
                pass

        return results
