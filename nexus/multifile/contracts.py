"""
Canonical typed data model for multi-file coordinated repository changes.

Sprint 8: EngineeringChangeSet is the authoritative representation for every
multi-file operation Nexus executes. All multi-file paths MUST produce one
before mutating the repository.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Any


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ChangeType(str, Enum):
    """The kind of change a PlannedFileChange represents."""
    CREATE = "CREATE"
    MODIFY = "MODIFY"
    DELETE = "DELETE"
    MOVE = "MOVE"
    RENAME = "RENAME"
    GENERATED_UPDATE = "GENERATED_UPDATE"
    CONFIGURATION_CHANGE = "CONFIGURATION_CHANGE"
    SCHEMA_CHANGE = "SCHEMA_CHANGE"
    MIGRATION = "MIGRATION"
    TEST_CHANGE = "TEST_CHANGE"
    DOCUMENTATION_CHANGE = "DOCUMENTATION_CHANGE"


class TaskType(str, Enum):
    """High-level classification of what the change set accomplishes."""
    FEATURE = "FEATURE"
    REFACTOR = "REFACTOR"
    BUG_FIX = "BUG_FIX"
    RENAME = "RENAME"
    SIGNATURE_CHANGE = "SIGNATURE_CHANGE"
    MODULE_MOVE = "MODULE_MOVE"
    DEPENDENCY_UPGRADE = "DEPENDENCY_UPGRADE"
    DEPENDENCY_REPLACEMENT = "DEPENDENCY_REPLACEMENT"
    CONFIGURATION_MIGRATION = "CONFIGURATION_MIGRATION"
    SCHEMA_MIGRATION = "SCHEMA_MIGRATION"
    FRAMEWORK_MIGRATION = "FRAMEWORK_MIGRATION"
    API_CHANGE = "API_CHANGE"
    TEST_ADAPTATION = "TEST_ADAPTATION"
    CLEANUP = "CLEANUP"


class ContractType(str, Enum):
    """Category of a shared interface that can be broken."""
    PUBLIC_FUNCTION = "PUBLIC_FUNCTION"
    CLASS_METHOD = "CLASS_METHOD"
    ABSTRACT_BASE = "ABSTRACT_BASE"
    PROTOCOL = "PROTOCOL"
    TYPESCRIPT_INTERFACE = "TYPESCRIPT_INTERFACE"
    EXPORTED_TYPE = "EXPORTED_TYPE"
    API_ENDPOINT = "API_ENDPOINT"
    REQUEST_SCHEMA = "REQUEST_SCHEMA"
    RESPONSE_SCHEMA = "RESPONSE_SCHEMA"
    CONFIGURATION_KEY = "CONFIGURATION_KEY"
    EVENT_SCHEMA = "EVENT_SCHEMA"
    DATABASE_MODEL = "DATABASE_MODEL"
    SERIALIZATION_FORMAT = "SERIALIZATION_FORMAT"
    CLI_FLAG = "CLI_FLAG"
    PLUGIN_INTERFACE = "PLUGIN_INTERFACE"
    MCP_CONTRACT = "MCP_CONTRACT"
    ENVIRONMENT_VARIABLE = "ENVIRONMENT_VARIABLE"


class ContractScope(str, Enum):
    """Visibility scope of the contract being changed."""
    INTERNAL_PRIVATE = "INTERNAL_PRIVATE"
    REPOSITORY_PUBLIC = "REPOSITORY_PUBLIC"
    PACKAGE_PUBLIC = "PACKAGE_PUBLIC"
    EXTERNAL_API = "EXTERNAL_API"


class CompatibilityPolicy(str, Enum):
    """How to handle consumers of a changed contract."""
    NONE = "NONE"                          # breaking change with no compatibility window
    BACKWARD_COMPATIBLE = "BACKWARD_COMPATIBLE"
    DEPRECATION_WINDOW = "DEPRECATION_WINDOW"
    DUAL_PATH = "DUAL_PATH"               # both old and new paths active during migration
    FEATURE_FLAG = "FEATURE_FLAG"
    VERSIONED_API = "VERSIONED_API"
    EXPLICIT_BREAKING = "EXPLICIT_BREAKING"  # user explicitly approved breaking change


class ImpactCategory(str, Enum):
    """How certain we are that a discovered file must change."""
    MUST_CHANGE = "MUST_CHANGE"
    MUST_VERIFY = "MUST_VERIFY"
    LIKELY_AFFECTED = "LIKELY_AFFECTED"
    POSSIBLY_AFFECTED = "POSSIBLY_AFFECTED"
    UNRESOLVED = "UNRESOLVED"
    OUT_OF_SCOPE = "OUT_OF_SCOPE"


class RollbackScope(str, Enum):
    """Granularity of a rollback operation."""
    NONE = "NONE"
    HUNK = "HUNK"
    FILE = "FILE"
    STAGE = "STAGE"
    FULL_CHANGE_SET = "FULL_CHANGE_SET"


class ChangeStageStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    ROLLED_BACK = "ROLLED_BACK"


class ValidationStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"


# ---------------------------------------------------------------------------
# Reference types
# ---------------------------------------------------------------------------


@dataclass
class SymbolReference:
    """Pointer to a specific symbol within the repository."""
    path: str
    symbol: str
    line: int = 0
    kind: str = ""  # e.g. 'function', 'class', 'constant'

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Reference:
    """Generic file/line reference."""
    path: str
    line: int = 0
    symbol: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Core change-set building blocks
# ---------------------------------------------------------------------------


@dataclass
class PlannedFileChange:
    """Describes a single file that will be modified as part of an EngineeringChangeSet.

    Every file in a change set must have a reason. Files without reasons are
    rejected by ChangeSetConsistencyValidator.
    """
    path: str
    reason: str
    change_type: ChangeType = ChangeType.MODIFY
    relevant_symbols: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)      # paths this change depends on
    expected_diff_scope: str = ""                            # e.g. "2-5 lines in function foo"
    verification_requirements: list[str] = field(default_factory=list)
    generated: bool = False        # True → must regenerate via generator, not direct edit
    protected: bool = False        # True → requires explicit approval to touch
    confidence: float = 1.0       # 0.0–1.0; <0.7 requires user review
    file_hash_before: str = ""     # expected SHA256 of file before mutation
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["change_type"] = self.change_type.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlannedFileChange:
        data = dict(data)
        if "change_type" in data:
            data["change_type"] = ChangeType(data["change_type"])
        return cls(**data)


@dataclass
class ContractChange:
    """Describes a single shared contract (interface, API, config key, etc.) being changed."""
    contract_id: str
    contract_type: ContractType
    definition: SymbolReference
    current_contract: str
    proposed_contract: str
    scope: ContractScope = ContractScope.REPOSITORY_PUBLIC
    producers: list[Reference] = field(default_factory=list)
    consumers: list[Reference] = field(default_factory=list)
    tests: list[Reference] = field(default_factory=list)
    compatibility_risk: str = "MEDIUM"  # RiskLevel value
    migration_strategy: str = ""
    unresolved_consumers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "contract_type": self.contract_type.value,
            "definition": self.definition.to_dict(),
            "current_contract": self.current_contract,
            "proposed_contract": self.proposed_contract,
            "scope": self.scope.value,
            "producers": [r.to_dict() for r in self.producers],
            "consumers": [r.to_dict() for r in self.consumers],
            "tests": [r.to_dict() for r in self.tests],
            "compatibility_risk": self.compatibility_risk,
            "migration_strategy": self.migration_strategy,
            "unresolved_consumers": self.unresolved_consumers,
        }


@dataclass
class ChangeDependency:
    """Edge in the change dependency graph: `source` must complete before `target`."""
    source_path: str   # PlannedFileChange.path that must happen first
    target_path: str   # PlannedFileChange.path that depends on source
    reason: str = ""
    conditional: bool = False   # True → dependency only applies when condition holds

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChangeStage:
    """One bounded phase of a staged multi-file execution."""
    stage_id: str
    name: str
    description: str
    file_paths: list[str] = field(default_factory=list)       # PlannedFileChange paths
    required_state: str = ""     # expected repository state precondition
    allowed_scope: list[str] = field(default_factory=list)    # permitted paths
    expected_outputs: list[str] = field(default_factory=list)
    verification_commands: list[str] = field(default_factory=list)
    mandatory: bool = True       # if True, failure blocks all subsequent stages
    checkpoint_required: bool = True
    rollback_target: str = ""    # stage_id to roll back to on failure
    status: ChangeStageStatus = ChangeStageStatus.PENDING
    started_at: str = ""
    completed_at: str = ""
    verification_passed: bool = False
    failure_reason: str = ""
    artifacts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d


@dataclass
class RollbackPlan:
    """Describes how to undo an EngineeringChangeSet."""
    scope: RollbackScope = RollbackScope.FULL_CHANGE_SET
    checkpoint_id: str = ""
    stage_rollback_targets: dict[str, str] = field(default_factory=dict)  # stage_id → checkpoint
    files_to_restore: list[str] = field(default_factory=list)
    commands_to_run: list[str] = field(default_factory=list)
    verified: bool = False
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["scope"] = self.scope.value
        return d


# ---------------------------------------------------------------------------
# Impact analysis types
# ---------------------------------------------------------------------------


@dataclass
class ImpactTarget:
    """A repository entity affected by a contract change."""
    path: str
    symbol: str = ""
    category: ImpactCategory = ImpactCategory.MUST_CHANGE
    reason: str = ""
    confidence: float = 1.0
    dynamic: bool = False  # True → discovered via heuristic, not static analysis

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["category"] = self.category.value
        return d


@dataclass
class Risk:
    """An architectural or compatibility risk."""
    risk_id: str
    description: str
    severity: str = "MEDIUM"
    mitigation: str = ""


@dataclass
class TestTarget:
    """A test that must be run for the change set."""
    path: str
    test_id: str = ""
    reason: str = ""
    level: int = 1  # 1=unit, 2=integration, 3=e2e


@dataclass
class ImpactReport:
    """Complete impact analysis for a proposed change set."""
    directly_affected: list[ImpactTarget] = field(default_factory=list)
    transitively_affected: list[ImpactTarget] = field(default_factory=list)
    potentially_affected: list[ImpactTarget] = field(default_factory=list)
    tests_required: list[TestTarget] = field(default_factory=list)
    contracts_changed: list[ContractChange] = field(default_factory=list)
    architecture_risks: list[Risk] = field(default_factory=list)
    unresolved_dynamic_dependencies: list[str] = field(default_factory=list)
    confidence: float = 1.0
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    repository_snapshot_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "directly_affected": [t.to_dict() for t in self.directly_affected],
            "transitively_affected": [t.to_dict() for t in self.transitively_affected],
            "potentially_affected": [t.to_dict() for t in self.potentially_affected],
            "tests_required": [asdict(t) for t in self.tests_required],
            "contracts_changed": [c.to_dict() for c in self.contracts_changed],
            "architecture_risks": [asdict(r) for r in self.architecture_risks],
            "unresolved_dynamic_dependencies": self.unresolved_dynamic_dependencies,
            "confidence": self.confidence,
            "generated_at": self.generated_at,
            "repository_snapshot_id": self.repository_snapshot_id,
        }


# ---------------------------------------------------------------------------
# Consistency validation types
# ---------------------------------------------------------------------------


@dataclass
class MissingChange:
    """A required file change that is absent from the change set."""
    path: str
    reason: str
    category: ImpactCategory = ImpactCategory.MUST_CHANGE
    related_change: str = ""  # PlannedFileChange.path that triggered this requirement


@dataclass
class ContractMismatch:
    """A contract that was changed in definition but not updated in an implementation."""
    contract_id: str
    definition_path: str
    stale_implementation_path: str
    description: str = ""


@dataclass
class ScopeViolation:
    """A file in the change set that violates scope or protection rules."""
    path: str
    reason: str
    violation_type: str = "UNKNOWN_PATH"  # UNKNOWN_PATH | PROTECTED | UNEXPLAINED | GENERATED


@dataclass
class ChangeSetValidationResult:
    """Result of running ChangeSetConsistencyValidator against an EngineeringChangeSet."""
    status: ValidationStatus = ValidationStatus.PASS
    missing_changes: list[MissingChange] = field(default_factory=list)
    stale_references: list[Reference] = field(default_factory=list)
    contract_mismatches: list[ContractMismatch] = field(default_factory=list)
    scope_violations: list[ScopeViolation] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def is_passing(self) -> bool:
        return (
            self.status == ValidationStatus.PASS
            and not self.missing_changes
            and not self.stale_references
            and not self.contract_mismatches
            and not self.scope_violations
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "missing_changes": [asdict(m) for m in self.missing_changes],
            "stale_references": [r.to_dict() for r in self.stale_references],
            "contract_mismatches": [asdict(c) for c in self.contract_mismatches],
            "scope_violations": [asdict(s) for s in self.scope_violations],
            "warnings": self.warnings,
        }


# ---------------------------------------------------------------------------
# The canonical change set
# ---------------------------------------------------------------------------


@dataclass
class EngineeringChangeSet:
    """The authoritative representation of a repository-scale coordinated change.

    Every multi-file operation in Nexus MUST be captured in an EngineeringChangeSet
    before any file in the repository is mutated. The change set is:
    - bound to a repository snapshot (stale snapshots are rejected)
    - bound to a plan version (stale plans are rejected)
    - a dependency-aware transaction (partial success is not complete success)
    - staged (large changes run through bounded stages with verification gates)
    """
    change_set_id: str = field(
        default_factory=lambda: f"cs-{uuid.uuid4().hex[:12]}"
    )
    run_id: str = ""
    plan_id: str = ""
    plan_version: int = 1
    repository_snapshot_id: str = ""
    task_type: TaskType = TaskType.FEATURE
    objective: str = ""
    contract_changes: list[ContractChange] = field(default_factory=list)
    file_changes: list[PlannedFileChange] = field(default_factory=list)
    dependency_edges: list[ChangeDependency] = field(default_factory=list)
    stages: list[ChangeStage] = field(default_factory=list)
    acceptance_criteria: list[str] = field(default_factory=list)
    compatibility_policy: CompatibilityPolicy = CompatibilityPolicy.BACKWARD_COMPATIBLE
    rollback_plan: RollbackPlan = field(default_factory=RollbackPlan)
    risk_level: str = "MEDIUM"
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    schema_version: str = "nexus.changeset.v8"

    # Runtime state (mutable)
    completed_stage_ids: list[str] = field(default_factory=list)
    applied_file_paths: list[str] = field(default_factory=list)
    validation_result: ChangeSetValidationResult | None = None
    final_verified: bool = False

    def file_paths(self) -> list[str]:
        return [fc.path for fc in self.file_changes]

    def get_file_change(self, path: str) -> PlannedFileChange | None:
        for fc in self.file_changes:
            if fc.path == path:
                return fc
        return None

    def has_unexplained_files(self) -> list[str]:
        return [fc.path for fc in self.file_changes if not fc.reason.strip()]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "change_set_id": self.change_set_id,
            "run_id": self.run_id,
            "plan_id": self.plan_id,
            "plan_version": self.plan_version,
            "repository_snapshot_id": self.repository_snapshot_id,
            "task_type": self.task_type.value,
            "objective": self.objective,
            "contract_changes": [c.to_dict() for c in self.contract_changes],
            "file_changes": [fc.to_dict() for fc in self.file_changes],
            "dependency_edges": [e.to_dict() for e in self.dependency_edges],
            "stages": [s.to_dict() for s in self.stages],
            "acceptance_criteria": self.acceptance_criteria,
            "compatibility_policy": self.compatibility_policy.value,
            "rollback_plan": self.rollback_plan.to_dict(),
            "risk_level": self.risk_level,
            "created_at": self.created_at,
            "completed_stage_ids": self.completed_stage_ids,
            "applied_file_paths": self.applied_file_paths,
            "final_verified": self.final_verified,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EngineeringChangeSet:
        data = dict(data)
        if "task_type" in data:
            data["task_type"] = TaskType(data["task_type"])
        if "compatibility_policy" in data:
            data["compatibility_policy"] = CompatibilityPolicy(data["compatibility_policy"])
        if "file_changes" in data:
            data["file_changes"] = [
                fc if isinstance(fc, PlannedFileChange) else PlannedFileChange.from_dict(fc)
                for fc in data["file_changes"]
            ]
        if "stages" in data:
            stages_objs = []
            for s in data["stages"]:
                if isinstance(s, ChangeStage):
                    stages_objs.append(s)
                else:
                    s_dict = dict(s)
                    if "status" in s_dict and isinstance(s_dict["status"], str):
                        s_dict["status"] = ChangeStageStatus(s_dict["status"])
                    stages_objs.append(ChangeStage(**s_dict))
            data["stages"] = stages_objs
        # Remove fields not in constructor
        for key in ("schema_version", "validation_result"):
            data.pop(key, None)
        return cls(**data)
