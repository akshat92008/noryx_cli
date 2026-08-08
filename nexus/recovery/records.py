"""
Failure Taxonomy and Record Definitions for Nexus CLI Recovery System.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class FailureCategory(str, Enum):
    TASK_UNDERSTANDING = "task_understanding"
    CONTEXT = "context"
    PLANNING = "planning"
    MODEL = "model"
    TOOL_EXECUTION = "tool_execution"
    MUTATION = "mutation"
    VERIFICATION = "verification"
    ENVIRONMENT = "environment"
    RESOURCE = "resource"


class FailureKind(str, Enum):
    # Task-understanding failures
    AMBIGUOUS_REQUIREMENT = "ambiguous_requirement"
    CONFLICTING_REQUIREMENTS = "conflicting_requirements"
    UNRESOLVED_ASSUMPTION = "unresolved_assumption"
    WRONG_TASK_CLASSIFICATION = "wrong_task_classification"

    # Context failures
    MISSING_CONTEXT = "missing_context"
    STALE_CONTEXT = "stale_context"
    IRRELEVANT_CONTEXT = "irrelevant_context"
    MISSED_CALLER = "missed_caller"
    MISSED_INTERFACE = "missed_interface"
    MISSED_CONFIGURATION = "missed_configuration"
    MISSED_TEST = "missed_test"
    REPOSITORY_PARSE_FAILURE = "repository_parse_failure"

    # Planning failures
    INCORRECT_ROOT_CAUSE = "incorrect_root_cause"
    INVALID_PLAN = "invalid_plan"
    UNDER_SCOPED_PLAN = "under_scoped_plan"
    OVER_SCOPED_PLAN = "over_scoped_plan"
    INVALID_STEP_ORDER = "invalid_step_order"
    MISSING_ACCEPTANCE_CRITERION = "missing_acceptance_criterion"
    MISSING_VERIFICATION_STEP = "missing_verification_step"
    UNSAFE_PLAN = "unsafe_plan"
    STALE_PLAN = "stale_plan"

    # Model failures
    INVALID_STRUCTURED_OUTPUT = "invalid_structured_output"
    TOOL_CALL_HALLUCINATION = "tool_call_hallucination"
    PATH_HALLUCINATION = "path_hallucination"
    INSTRUCTION_FAILURE = "instruction_failure"
    CONTEXT_RETENTION_FAILURE = "context_retention_failure"
    PATCH_GENERATION_FAILURE = "patch_generation_failure"
    REPEATED_REASONING_FAILURE = "repeated_reasoning_failure"
    MODEL_CAPABILITY_MISMATCH = "model_capability_mismatch"

    # Tool and execution failures
    TOOL_UNAVAILABLE = "tool_unavailable"
    TOOL_ARGUMENT_INVALID = "tool_argument_invalid"
    EXECUTABLE_NOT_FOUND = "executable_not_found"
    COMMAND_FAILED = "command_failed"
    COMMAND_TIMEOUT = "command_timeout"
    COMMAND_CANCELLED = "command_cancelled"
    PROCESS_CRASHED = "process_crashed"
    OUTPUT_TRUNCATED = "output_truncated"
    SANDBOX_UNAVAILABLE = "sandbox_unavailable"
    NETWORK_DENIED = "network_denied"
    PERMISSION_DENIED = "permission_denied"
    POLICY_BLOCKED = "policy_blocked"

    # Mutation failures
    PATCH_CONFLICT = "patch_conflict"
    PARTIAL_PATCH = "partial_patch"
    STALE_FILE = "stale_file"
    OUT_OF_SCOPE_MUTATION = "out_of_scope_mutation"
    PROTECTED_PATH = "protected_path"
    UNEXPECTED_FILE_CHANGE = "unexpected_file_change"
    WORKSPACE_CORRUPTION = "workspace_corruption"
    ROLLBACK_FAILED = "rollback_failed"

    # Verification failures
    TARGETED_TEST_FAILED = "targeted_test_failed"
    REGRESSION_INTRODUCED = "regression_introduced"
    BUILD_FAILED = "build_failed"
    LINT_FAILED = "lint_failed"
    TYPE_CHECK_FAILED = "type_check_failed"
    NO_TESTS_COLLECTED = "no_tests_collected"
    VERIFICATION_TIMEOUT = "verification_timeout"
    VERIFIER_UNAVAILABLE = "verifier_unavailable"
    EVIDENCE_STALE = "evidence_stale"
    EVIDENCE_CORRUPTED = "evidence_corrupted"
    ACCEPTANCE_CRITERION_FAILED = "acceptance_criterion_failed"

    # Environment failures
    DEPENDENCY_MISSING = "dependency_missing"
    DEPENDENCY_CONFLICT = "dependency_conflict"
    RUNTIME_VERSION_MISMATCH = "runtime_version_mismatch"
    OPERATING_SYSTEM_UNSUPPORTED = "operating_system_unsupported"
    EXTERNAL_SERVICE_UNAVAILABLE = "external_service_unavailable"
    REPOSITORY_BASELINE_BROKEN = "repository_baseline_broken"
    DISK_OR_MEMORY_LIMIT = "disk_or_memory_limit"
    AUTHENTICATION_FAILURE = "authentication_failure"
    QUOTA_EXHAUSTED = "quota_exhausted"

    # Resource failures
    BUDGET_EXHAUSTED = "budget_exhausted"
    RETRY_LIMIT_REACHED = "retry_limit_reached"
    TIME_LIMIT_REACHED = "time_limit_reached"
    CONTEXT_LIMIT_REACHED = "context_limit_reached"
    MODEL_ESCALATION_LIMIT_REACHED = "model_escalation_limit_reached"

    # Core legacy fallbacks
    SYNTAX = "syntax"
    IMPORT = "import"
    TYPE = "type"
    TEST = "test"
    RUNTIME = "runtime"
    DEPENDENCY = "dependency"
    TIMEOUT = "timeout"
    ENVIRONMENT = "environment"
    SECURITY = "security"
    UNKNOWN = "unknown"


class FailureSeverity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class HypothesisStatus(str, Enum):
    PROPOSED = "proposed"
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNRESOLVED = "unresolved"


@dataclass
class EvidenceReference:
    evidence_id: str
    kind: str
    source: str
    summary: str
    artifact_path: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


@dataclass
class FailureHypothesis:
    hypothesis_id: str
    statement: str
    evidence_for: list[str] = field(default_factory=list)
    evidence_against: list[str] = field(default_factory=list)
    confidence: float = 0.5
    cheap_check: str = ""
    expected_outcome: str = ""
    status: HypothesisStatus = HypothesisStatus.PROPOSED


@dataclass
class FailureRecord:
    failure_id: str
    run_id: str
    category: FailureCategory
    kind: FailureKind
    source_component: str
    phase: str
    summary: str
    evidence: list[EvidenceReference] = field(default_factory=list)
    repository_state: str = ""
    plan_version: int = 1
    attempt_number: int = 1
    retryable: bool = True
    severity: FailureSeverity = FailureSeverity.MEDIUM
    user_action_required: bool = False
    likely_causes: list[FailureHypothesis] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    raw_output: str = ""
    file_paths: list[str] = field(default_factory=list)
    line_numbers: list[int] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    failing_tests: list[str] = field(default_factory=list)
    command: str = ""
    exit_code: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_id": self.failure_id,
            "run_id": self.run_id,
            "category": self.category.value if hasattr(self.category, "value") else str(self.category),
            "kind": self.kind.value if hasattr(self.kind, "value") else str(self.kind),
            "source_component": self.source_component,
            "phase": self.phase,
            "summary": self.summary,
            "evidence": [e.__dict__ for e in self.evidence],
            "repository_state": self.repository_state,
            "plan_version": self.plan_version,
            "attempt_number": self.attempt_number,
            "retryable": self.retryable,
            "severity": self.severity.value if hasattr(self.severity, "value") else str(self.severity),
            "user_action_required": self.user_action_required,
            "likely_causes": [h.__dict__ for h in self.likely_causes],
            "created_at": self.created_at,
            "raw_output": self.raw_output[:2000],
            "file_paths": self.file_paths,
            "line_numbers": self.line_numbers,
            "symbols": self.symbols,
            "failing_tests": self.failing_tests,
            "command": self.command,
            "exit_code": self.exit_code,
            "metadata": self.metadata,
        }


@dataclass
class FailureDiagnosis:
    diagnosis_id: str
    primary_failure: FailureRecord
    likely_root_causes: list[FailureHypothesis] = field(default_factory=list)
    rejected_causes: list[FailureHypothesis] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    recommended_strategy: str = ""
    rollback_required: bool = False
    replan_required: bool = False
    context_expansion_required: bool = False
    model_escalation_recommended: bool = False
    confidence: float = 0.5
    limitations: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "diagnosis_id": self.diagnosis_id,
            "primary_failure": self.primary_failure.to_dict(),
            "likely_root_causes": [h.__dict__ for h in self.likely_root_causes],
            "rejected_causes": [h.__dict__ for h in self.rejected_causes],
            "missing_evidence": self.missing_evidence,
            "recommended_strategy": self.recommended_strategy,
            "rollback_required": self.rollback_required,
            "replan_required": self.replan_required,
            "context_expansion_required": self.context_expansion_required,
            "model_escalation_recommended": self.model_escalation_recommended,
            "confidence": self.confidence,
            "limitations": self.limitations,
            "created_at": self.created_at,
        }
