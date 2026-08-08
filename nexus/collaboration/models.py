"""
nexus/collaboration/models.py

Core models and typed contracts for controlled multi-agent collaboration.
Every public type used across the collaboration sub-package is defined here
so that the rest of the package can import from a single, stable location
without creating circular dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, Tuple


from nexus.routing.models import ModelTier

# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class CollaborationMode(Enum):
    SINGLE_AGENT = "SINGLE_AGENT"
    REVIEW_PAIR = "REVIEW_PAIR"
    SPECIALIST_TEAM = "SPECIALIST_TEAM"
    PARALLEL_ANALYSIS = "PARALLEL_ANALYSIS"
    PARALLEL_IMPLEMENTATION = "PARALLEL_IMPLEMENTATION"
    STAGED_COLLABORATION = "STAGED_COLLABORATION"


class CollaborationState(Enum):
    ANALYZING = "analyzing"
    DECOMPOSING = "decomposing"
    VALIDATING_ASSIGNMENTS = "validating_assignments"
    PREPARING_WORKERS = "preparing_workers"
    RUNNING_WORKERS = "running_workers"
    COLLECTING_RESULTS = "collecting_results"
    REVIEWING_RESULTS = "reviewing_results"
    RESOLVING_CONFLICTS = "resolving_conflicts"
    INTEGRATING = "integrating"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class WorkerState(Enum):
    CREATED = "created"
    PREPARING = "preparing"
    READY = "ready"
    RUNNING = "running"
    WAITING = "waiting"
    SUBMITTED = "submitted"
    REVIEWED = "reviewed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    FAILED = "failed"
    CANCELLED = "cancelled"
    CLEANED_UP = "cleaned_up"


class AgentRole(Enum):
    INVESTIGATOR = "INVESTIGATOR"
    PLANNER = "PLANNER"
    IMPLEMENTER = "IMPLEMENTER"
    TEST_ENGINEER = "TEST_ENGINEER"
    REVIEWER = "REVIEWER"
    SECURITY_REVIEWER = "SECURITY_REVIEWER"
    INTEGRATION_ENGINEER = "INTEGRATION_ENGINEER"
    CENTRAL_VERIFIER = "CENTRAL_VERIFIER"

    # Backward compatibility aliases
    LEAD_ENGINEER = "PLANNER"
    REPOSITORY_ANALYST = "INVESTIGATOR"
    IMPLEMENTATION_ENGINEER = "IMPLEMENTER"
    DEBUGGER = "INVESTIGATOR"
    ARCHITECTURE_REVIEWER = "REVIEWER"
    DEPENDENCY_SPECIALIST = "INVESTIGATOR"
    DOCUMENTATION_ENGINEER = "IMPLEMENTER"
    INDEPENDENT_VERIFIER = "CENTRAL_VERIFIER"


class RiskLevel(Enum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class WorkspaceStrategy(Enum):
    READ_ONLY_SHARED_SNAPSHOT = "READ_ONLY_SHARED_SNAPSHOT"
    ISOLATED_WORKTREE = "ISOLATED_WORKTREE"
    ISOLATED_TEMPORARY_COPY = "ISOLATED_TEMPORARY_COPY"


class ReservationMode(Enum):
    EXCLUSIVE = "exclusive"
    SHARED_READ = "shared_read"


class AssignmentStatus(Enum):
    COMPLETED = "COMPLETED"
    PARTIALLY_COMPLETED = "PARTIALLY_COMPLETED"
    LOCALLY_VALIDATED = "LOCALLY_VALIDATED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    INVALID = "INVALID"


# Alias for backward compatibility
WorkerResultStatus = AssignmentStatus


class ReviewDecision(Enum):
    APPROVE_FOR_INTEGRATION = "APPROVE_FOR_INTEGRATION"
    REVISE = "REVISE"
    REJECT = "REJECT"
    BLOCKED = "BLOCKED"


class IntegrationStatus(Enum):
    INTEGRATED = "INTEGRATED"
    PARTIALLY_INTEGRATED = "PARTIALLY_INTEGRATED"
    CONFLICTED = "CONFLICTED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


class CoordinationMessageType(Enum):
    FINDING = "FINDING"
    EVIDENCE_REQUEST = "EVIDENCE_REQUEST"
    DEPENDENCY_UPDATE = "DEPENDENCY_UPDATE"
    SCOPE_EXPANSION_REQUEST = "SCOPE_EXPANSION_REQUEST"
    REVIEW_ISSUE = "REVIEW_ISSUE"
    INTEGRATION_CONFLICT = "INTEGRATION_CONFLICT"
    ASSIGNMENT_BLOCKED = "ASSIGNMENT_BLOCKED"
    ASSIGNMENT_COMPLETED = "ASSIGNMENT_COMPLETED"

    # Backward compatibility aliases
    CONTEXT_REQUEST = "EVIDENCE_REQUEST"
    CLARIFICATION_REQUEST = "EVIDENCE_REQUEST"
    BLOCKER_REPORT = "ASSIGNMENT_BLOCKED"
    RESULT_SUBMISSION = "ASSIGNMENT_COMPLETED"
    CANCELLATION = "ASSIGNMENT_BLOCKED"
    REVISION_REQUEST = "REVIEW_ISSUE"


class ReviewFindingCategory(Enum):
    MISSING_EVIDENCE = "missing_evidence"
    UNPLANNED_FILE = "unplanned_file"
    POLICY_VIOLATION = "policy_violation"
    SECURITY_RISK = "security_risk"
    UNSUPPORTED_CLAIM = "unsupported_claim"
    SCOPE_VIOLATION = "scope_violation"
    VERIFICATION_FAILURE = "verification_failure"
    ASSIGNMENT_NONCOMPLIANCE = "assignment_noncompliance"


class CollaborationPolicyProfile(Enum):
    DISABLED = "DISABLED"
    INVESTIGATION_ONLY = "INVESTIGATION_ONLY"
    REVIEW_ONLY = "REVIEW_ONLY"
    CONTROLLED_PARALLEL = "CONTROLLED_PARALLEL"
    CUSTOM = "CUSTOM"


class ArchitectureReviewDecision(Enum):
    APPROVE = "APPROVE"
    APPROVE_WITH_WARNINGS = "APPROVE_WITH_WARNINGS"
    REVISE = "REVISE"
    BLOCK = "BLOCK"


class SecurityFindingSeverity(Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class WorkerRecoveryAction(Enum):
    RETRY_WORKER = "RETRY_WORKER"
    REASSIGN_WITH_DIFFERENT_MODEL = "REASSIGN_WITH_DIFFERENT_MODEL"
    REDUCE_ASSIGNMENT_SCOPE = "REDUCE_ASSIGNMENT_SCOPE"
    REQUEST_MORE_CONTEXT = "REQUEST_MORE_CONTEXT"
    CONVERT_TO_SEQUENTIAL_WORK = "CONVERT_TO_SEQUENTIAL_WORK"
    REPLAN_ASSIGNMENT = "REPLAN_ASSIGNMENT"
    MARK_OPTIONAL_FAILURE = "MARK_OPTIONAL_FAILURE"
    ABORT_COLLABORATION = "ABORT_COLLABORATION"


# ---------------------------------------------------------------------------
# Dataclasses & Contracts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentCapabilityProfile:
    role: AgentRole
    supported_task_types: Tuple[str, ...]
    supported_languages: Tuple[str, ...]
    allowed_tool_capabilities: Tuple[str, ...]
    mutation_allowed: bool
    maximum_risk_level: RiskLevel
    preferred_model_tiers: Tuple[ModelTier, ...]
    context_budget: int
    can_request_approval: bool
    can_create_workers: bool


@dataclass(frozen=True)
class MutationPolicy:
    allowed: bool
    require_transaction: bool = True
    max_files_per_turn: int = 10
    prohibited_patterns: Tuple[str, ...] = ()


@dataclass(frozen=True)
class WorkerBudget:
    max_model_calls: int
    max_tool_calls: int
    max_tokens: int
    max_cost_usd: Optional[Decimal]
    max_wall_clock_seconds: int
    max_retries: int = 2


@dataclass(frozen=True)
class RoutingConstraints:
    local_only: bool = False
    max_tier: Optional[Any] = None
    preferred_model_ids: Tuple[str, ...] = ()


@dataclass(frozen=True)
class AssignmentScope:
    description: str
    packages: Tuple[str, ...]
    is_bounded: bool = True


@dataclass(frozen=True)
class AgentAssignment:
    assignment_id: str
    role: AgentRole
    objective: str
    parent_plan_step_ids: Tuple[str, ...] = ()
    repository_snapshot_id: str = "main"
    context_bundle_id: str = "bundle-0"
    allowed_read_paths: Tuple[Path, ...] = ()
    allowed_mutation_paths: Tuple[Path, ...] = ()
    protected_paths: Tuple[Path, ...] = ()
    dependencies: Tuple[str, ...] = ()
    acceptance_criteria: Tuple[str, ...] = ()
    expected_deliverables: Tuple[str, ...] = ()
    allowed_tools: Tuple[str, ...] = ()
    model_requirements: Dict[str, Any] = field(default_factory=dict)
    model_id: Optional[str] = None
    budget: WorkerBudget = field(default_factory=lambda: WorkerBudget(10, 20, 50000, Decimal("1.00"), 300))
    timeout_seconds: int = 300
    retry_limit: int = 2
    is_optional: bool = False

    # Backwards compatibility attributes
    parent_run_id: str = ""
    scope: AssignmentScope = field(default_factory=lambda: AssignmentScope("default", ()))
    allowed_paths: Tuple[Path, ...] = ()
    prohibited_paths: Tuple[Path, ...] = ()
    relevant_symbols: Tuple[str, ...] = ()
    requirements: Tuple[str, ...] = ()
    expected_outputs: Tuple[str, ...] = ()
    verification_requirements: Tuple[str, ...] = ()
    mutation_policy: MutationPolicy = field(default_factory=lambda: MutationPolicy(False))
    model_constraints: RoutingConstraints = field(default_factory=lambda: RoutingConstraints())
    deadline_seconds: int = 300

    def __post_init__(self) -> None:
        # Sync compatibility fields if left default
        if self.allowed_mutation_paths and not self.allowed_paths:
            object.__setattr__(self, "allowed_paths", self.allowed_mutation_paths)
        if self.protected_paths and not self.prohibited_paths:
            object.__setattr__(self, "prohibited_paths", self.protected_paths)
        if self.acceptance_criteria and not self.requirements:
            object.__setattr__(self, "requirements", self.acceptance_criteria)
        if self.acceptance_criteria and not self.verification_requirements:
            object.__setattr__(self, "verification_requirements", self.acceptance_criteria)
        if self.expected_deliverables and not self.expected_outputs:
            object.__setattr__(self, "expected_outputs", self.expected_deliverables)


@dataclass(frozen=True)
class DelegationAssessment:
    collaboration_recommended: bool
    expected_benefit: float
    coordination_cost: float
    parallelizable_work: Tuple[str, ...]
    sequential_dependencies: Tuple[str, ...]
    risks: Tuple[str, ...]
    maximum_workers: int
    evidence_ids: Tuple[str, ...]


@dataclass(frozen=True)
class CollaborationDecision:
    use_collaboration: bool
    recommended_mode: CollaborationMode
    reasons: Tuple[str, ...]
    expected_benefit: str
    expected_overhead: str
    expected_cost: Decimal
    risk: RiskLevel
    confidence: float


@dataclass(frozen=True)
class ContextResource:
    resource_id: str
    kind: str
    path: Optional[str]
    content_hash: Optional[str]


@dataclass(frozen=True)
class WorkerContextPacket:
    assignment_id: str
    objective: str
    role: AgentRole
    constraints: Tuple[str, ...]
    allowed_resources: Tuple[ContextResource, ...]
    dependency_summary: str
    relevant_evidence: Tuple[str, ...]
    expected_output_schema: str
    token_count: int
    repository_revision: str


@dataclass(frozen=True)
class WorkerWorkspace:
    workspace_id: str
    assignment_id: str
    strategy: WorkspaceStrategy
    root_path: Path
    is_writable: bool
    created_at: datetime
    cleaned_up: bool = False


@dataclass(frozen=True)
class MutationScopeReservation:
    reservation_id: str
    assignment_id: str
    paths: Tuple[Path, ...]
    symbol_ids: Tuple[str, ...]
    mode: ReservationMode
    expires_at: datetime


@dataclass(frozen=True)
class CoordinationMessage:
    message_id: str
    sender_id: str
    recipient_id: str
    message_type: CoordinationMessageType
    assignment_id: str
    content: Mapping[str, Any]
    evidence_ids: Tuple[str, ...]
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))


@dataclass(frozen=True)
class WorkerFinding:
    finding_id: str
    description: str
    severity: RiskLevel
    evidence_ids: Tuple[str, ...]
    affected_paths: Tuple[str, ...]


@dataclass(frozen=True)
class ProposedChange:
    change_id: str
    path: str
    description: str
    diff_reference: Optional[str]
    transaction_ref: Optional[str]


@dataclass(frozen=True)
class ResourceUsage:
    model_calls: int
    tool_calls: int
    tokens_used: int
    cost_usd: Optional[Decimal]
    wall_clock_seconds: float


@dataclass(frozen=True)
class AssignmentResult:
    assignment_id: str
    status: AssignmentStatus
    repository_snapshot_before: str = "main"
    workspace_tree_after: str = "tree"
    patch_artifact: Optional[str] = None
    evidence: Tuple[str, ...] = ()
    tests: Tuple[str, ...] = ()
    findings: Tuple[WorkerFinding, ...] = ()
    limitations: Tuple[str, ...] = ()
    cost: ResourceUsage = field(default_factory=lambda: ResourceUsage(0, 0, 0, Decimal("0"), 0.0))
    failure: Optional[str] = None

    # Backward compatibility properties
    worker_id: str = "worker-0"
    summary: str = ""
    proposed_changes: Tuple[ProposedChange, ...] = ()
    transaction_reference: Optional[str] = None
    verification_results: Tuple[str, ...] = ()
    unresolved_questions: Tuple[str, ...] = ()
    risks: Tuple[str, ...] = ()
    evidence_ids: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.evidence and not self.evidence_ids:
            object.__setattr__(self, "evidence_ids", self.evidence)
        if self.tests and not self.verification_results:
            object.__setattr__(self, "verification_results", self.tests)


# Alias for backward compatibility
WorkerResult = AssignmentResult


@dataclass(frozen=True)
class ReviewIssue:
    issue_id: str
    description: str
    severity: RiskLevel


@dataclass(frozen=True)
class ReviewFinding:
    finding_id: str
    category: ReviewFindingCategory
    description: str
    severity: RiskLevel


@dataclass(frozen=True)
class AssignmentReview:
    assignment_id: str
    decision: ReviewDecision = ReviewDecision.APPROVE_FOR_INTEGRATION
    review_id: str = "rev-0"
    blocking_issues: Tuple[ReviewIssue, ...] = ()
    warnings: Tuple[ReviewIssue, ...] = ()
    missing_tests: Tuple[str, ...] = ()
    scope_violations: Tuple[str, ...] = ()
    security_findings: Tuple[str, ...] = ()
    evidence: Tuple[str, ...] = ()

    # Backward compatibility fields
    accepted: bool = False
    findings: Tuple[ReviewFinding, ...] = ()
    missing_evidence: Tuple[str, ...] = ()
    required_revisions: Tuple[str, ...] = ()
    integration_eligible: bool = False

    def __post_init__(self) -> None:
        if self.decision == ReviewDecision.APPROVE_FOR_INTEGRATION or self.accepted:
            object.__setattr__(self, "accepted", True)
            object.__setattr__(self, "integration_eligible", True)


# Alias for backward compatibility
WorkerReview = AssignmentReview


@dataclass(frozen=True)
class SecurityFinding:
    finding_id: str
    title: str
    severity: SecurityFindingSeverity
    evidence: Tuple[str, ...]
    exploit_preconditions: Tuple[str, ...]
    affected_files: Tuple[str, ...]
    recommended_remediation: str
    confidence: float


@dataclass(frozen=True)
class IntegrationResult:
    integration_id: str
    status: IntegrationStatus
    baseline_tree: str
    integrated_tree: Optional[str]
    applied_assignments: Tuple[str, ...]
    rejected_assignments: Tuple[str, ...]
    conflicts: Tuple[str, ...]
    evidence: Tuple[str, ...]
    rollback_checkpoint: str

    # Backward compatibility fields
    transaction_id: str = ""
    integrated_assignments: Tuple[str, ...] = ()
    verification_results: Tuple[str, ...] = ()
    evidence_ids: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.applied_assignments and not self.integrated_assignments:
            object.__setattr__(self, "integrated_assignments", self.applied_assignments)
        if self.evidence and not self.evidence_ids:
            object.__setattr__(self, "evidence_ids", self.evidence)


@dataclass(frozen=True)
class CollaborationBudget:
    maximum_workers: int
    maximum_parallel_workers: int
    maximum_total_model_calls: int
    maximum_total_tool_calls: int
    maximum_total_tokens: int
    maximum_total_cost_usd: Optional[Decimal]
    maximum_wall_clock_seconds: int
    maximum_worker_retries: int
    maximum_reassignments: int


@dataclass(frozen=True)
class CollaborationRun:
    collaboration_id: str
    run_id: str
    task_contract_id: str
    plan_id: str
    plan_version: int
    repository_snapshot_id: str
    mode: CollaborationMode
    assignments: Tuple[AgentAssignment, ...]
    dependency_graph: Any  # AssignmentGraph
    integration_policy: str
    verification_policy: str
    concurrency_limit: int
    budget: CollaborationBudget
    status: CollaborationState


@dataclass
class CollaborationRunState:
    run_id: str
    collaboration_id: str
    state: CollaborationState
    policy: CollaborationPolicyProfile
    budget: CollaborationBudget
    assignments: dict = field(default_factory=dict)
    worker_states: dict = field(default_factory=dict)
    worker_results: dict = field(default_factory=dict)
    worker_reviews: dict = field(default_factory=dict)
    reservations: dict = field(default_factory=dict)
    integration_result: Optional[IntegrationResult] = None
    cancelled: bool = False
    mode: CollaborationMode = CollaborationMode.SINGLE_AGENT
    created_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))

    def total_spent(self) -> ResourceUsage:
        calls = sum(r.cost.model_calls for r in self.worker_results.values())
        tools = sum(r.cost.tool_calls for r in self.worker_results.values())
        tokens = sum(r.cost.tokens_used for r in self.worker_results.values())
        costs = [r.cost.cost_usd for r in self.worker_results.values() if r.cost.cost_usd is not None]
        total_cost = sum(costs, Decimal("0")) if costs else None
        wall = sum(r.cost.wall_clock_seconds for r in self.worker_results.values())
        return ResourceUsage(
            model_calls=calls,
            tool_calls=tools,
            tokens_used=tokens,
            cost_usd=total_cost,
            wall_clock_seconds=wall,
        )
