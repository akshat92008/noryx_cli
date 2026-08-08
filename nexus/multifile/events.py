"""
Sprint 8 observability events for multi-file engineering operations.

Every significant event in a coordinated change-set execution emits a structured
event that includes run ID, change-set ID, stage, plan version, repository state,
affected files, contracts, evidence, cost, and duration.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class MultiFileEvent:
    """Base class for all Sprint 8 events."""
    event_type: str
    run_id: str = ""
    change_set_id: str = ""
    plan_id: str = ""
    plan_version: int = 1
    repository_snapshot_id: str = ""
    timestamp: str = field(default_factory=_now)
    cost_usd: float = 0.0
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ImpactAnalysisStarted(MultiFileEvent):
    event_type: str = "ImpactAnalysisStarted"
    changed_symbols: list[str] = field(default_factory=list)
    root_paths: list[str] = field(default_factory=list)


@dataclass
class ImpactAnalysisCompleted(MultiFileEvent):
    event_type: str = "ImpactAnalysisCompleted"
    directly_affected_count: int = 0
    transitively_affected_count: int = 0
    unresolved_count: int = 0
    confidence: float = 1.0
    impact_report_path: str = ""


@dataclass
class ContractInventoryCreated(MultiFileEvent):
    event_type: str = "ContractInventoryCreated"
    contracts_found: int = 0
    breaking_changes: int = 0
    unresolved_consumers: int = 0


@dataclass
class ChangeSetCreated(MultiFileEvent):
    event_type: str = "ChangeSetCreated"
    file_count: int = 0
    stage_count: int = 0
    task_type: str = ""
    risk_level: str = ""


@dataclass
class ChangeSetValidated(MultiFileEvent):
    event_type: str = "ChangeSetValidated"
    status: str = "PASS"
    missing_changes: int = 0
    stale_references: int = 0
    contract_mismatches: int = 0
    scope_violations: int = 0


@dataclass
class ChangeStageStarted(MultiFileEvent):
    event_type: str = "ChangeStageStarted"
    stage_id: str = ""
    stage_name: str = ""
    file_count: int = 0
    mandatory: bool = True


@dataclass
class ChangeStageCompleted(MultiFileEvent):
    event_type: str = "ChangeStageCompleted"
    stage_id: str = ""
    stage_name: str = ""
    verification_passed: bool = True
    files_modified: list[str] = field(default_factory=list)


@dataclass
class ChangeStageFailed(MultiFileEvent):
    event_type: str = "ChangeStageFailed"
    stage_id: str = ""
    stage_name: str = ""
    failure_reason: str = ""
    files_partially_modified: list[str] = field(default_factory=list)
    rolled_back: bool = False


@dataclass
class ContractChanged(MultiFileEvent):
    event_type: str = "ContractChanged"
    contract_id: str = ""
    contract_type: str = ""
    scope: str = ""
    consumer_count: int = 0
    unresolved_consumer_count: int = 0
    compatibility_policy: str = ""


@dataclass
class CallerMigrationStarted(MultiFileEvent):
    event_type: str = "CallerMigrationStarted"
    contract_id: str = ""
    caller_paths: list[str] = field(default_factory=list)


@dataclass
class IntermediateVerificationStarted(MultiFileEvent):
    event_type: str = "IntermediateVerificationStarted"
    stage_id: str = ""
    commands: list[str] = field(default_factory=list)


@dataclass
class IntermediateVerificationCompleted(MultiFileEvent):
    event_type: str = "IntermediateVerificationCompleted"
    stage_id: str = ""
    passed: bool = True
    output_summary: str = ""


@dataclass
class ScopeExpansionRequested(MultiFileEvent):
    event_type: str = "ScopeExpansionRequested"
    reason: str = ""
    new_paths: list[str] = field(default_factory=list)
    approved: bool = False


@dataclass
class CompatibilityDecisionRequired(MultiFileEvent):
    event_type: str = "CompatibilityDecisionRequired"
    contract_id: str = ""
    breaking_change_description: str = ""
    options: list[str] = field(default_factory=list)


@dataclass
class ChangeSetRolledBack(MultiFileEvent):
    event_type: str = "ChangeSetRolledBack"
    scope: str = "FULL_CHANGE_SET"
    stage_id: str = ""
    files_restored: list[str] = field(default_factory=list)
    rollback_verified: bool = False
    reason: str = ""


@dataclass
class MultiFileVerificationCompleted(MultiFileEvent):
    event_type: str = "MultiFileVerificationCompleted"
    status: str = "VERIFIED"
    acceptance_criteria_passed: list[str] = field(default_factory=list)
    acceptance_criteria_failed: list[str] = field(default_factory=list)
    evidence_path: str = ""
    final_tree_hash: str = ""
