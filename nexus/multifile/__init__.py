"""
nexus.multifile — Sprint 8 Multi-File Engineering Package.

Provides the canonical typed data model and execution infrastructure for
repository-scale coordinated changes.
"""

from __future__ import annotations

from nexus.multifile.contracts import (
    ChangeType,
    CompatibilityPolicy,
    ContractChange,
    ContractScope,
    ContractType,
    ChangeDependency,
    ChangeStage,
    ChangeStageStatus,
    EngineeringChangeSet,
    ImpactCategory,
    ImpactReport,
    ImpactTarget,
    MissingChange,
    ContractMismatch,
    ScopeViolation,
    PlannedFileChange,
    RollbackPlan,
    RollbackScope,
    TaskType,
    ChangeSetValidationResult,
    ValidationStatus,
)

from nexus.multifile.orchestrator import (
    CompletionAssessment,
    FileObligation,
    MultiFileCompletionContract,
    MultiFileOrchestrator,
)

from nexus.multifile.events import (
    CallerMigrationStarted,
    ChangeSetCreated,
    ChangeSetRolledBack,
    ChangeSetValidated,
    ChangeStageFailed,
    ChangeStageCompleted,
    ChangeStageStarted,
    CompatibilityDecisionRequired,
    ContractChanged,
    ContractInventoryCreated,
    ImpactAnalysisCompleted,
    ImpactAnalysisStarted,
    IntermediateVerificationCompleted,
    IntermediateVerificationStarted,
    MultiFileVerificationCompleted,
    ScopeExpansionRequested,
)

__all__ = [
    "CompletionAssessment",
    "FileObligation",
    "MultiFileCompletionContract",
    "MultiFileOrchestrator",
    # Contracts
    "ChangeType",
    "CompatibilityPolicy",
    "ContractChange",
    "ContractScope",
    "ContractType",
    "ChangeDependency",
    "ChangeStage",
    "ChangeStageStatus",
    "EngineeringChangeSet",
    "ImpactCategory",
    "ImpactReport",
    "ImpactTarget",
    "MissingChange",
    "ContractMismatch",
    "ScopeViolation",
    "PlannedFileChange",
    "RollbackPlan",
    "RollbackScope",
    "TaskType",
    "ChangeSetValidationResult",
    "ValidationStatus",
    # Events
    "CallerMigrationStarted",
    "ChangeSetCreated",
    "ChangeSetRolledBack",
    "ChangeSetValidated",
    "ChangeStageFailed",
    "ChangeStageCompleted",
    "ChangeStageStarted",
    "CompatibilityDecisionRequired",
    "ContractChanged",
    "ContractInventoryCreated",
    "ImpactAnalysisCompleted",
    "ImpactAnalysisStarted",
    "IntermediateVerificationCompleted",
    "IntermediateVerificationStarted",
    "MultiFileVerificationCompleted",
    "ScopeExpansionRequested",
]
