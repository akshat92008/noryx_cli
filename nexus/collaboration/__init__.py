"""
nexus/collaboration/__init__.py

Public API for the Nexus multi-agent collaboration subsystem.
"""

from nexus.collaboration.assignments import AssignmentGraph
from nexus.collaboration.capabilities import AgentCapabilityRegistry
from nexus.collaboration.conflicts import ScopeReservationRegistry
from nexus.collaboration.coordination import CoordinationBlackboard, CoordinationBus
from nexus.collaboration.delegation import DelegationPlanner, TaskCharacteristics
from nexus.collaboration.lifecycle import WorkerLifecycleManager
from nexus.collaboration.integration import IntegrationCoordinator
from nexus.collaboration.lead_orchestrator import LeadOrchestrator
from nexus.collaboration.models import (
    AgentAssignment,
    AgentRole,
    AssignmentResult,
    AssignmentReview,
    AssignmentScope,
    AssignmentStatus,
    CollaborationBudget,
    CollaborationDecision,
    CollaborationMode,
    CollaborationPolicyProfile,
    CollaborationState,
    DelegationAssessment,
    IntegrationResult,
    IntegrationStatus,
    MutationPolicy,

    ReviewDecision,
    RiskLevel,
    RoutingConstraints,
    WorkerBudget,
    WorkerResult,
    WorkerResultStatus,
    WorkerReview,
    WorkerState,
    WorkspaceStrategy,
)
from nexus.collaboration.observability import CollaborationEventEmitter
from nexus.collaboration.policies import CollaborationPolicyEngine, default_budget
from nexus.collaboration.review import ResultReviewService
from nexus.collaboration.worker_runtime import WorkerRuntime

__all__ = [
    # Orchestrators & Coordinators
    "LeadOrchestrator",
    "WorkerRuntime",
    "IntegrationCoordinator",
    "ResultReviewService",
    # Models & Contracts
    "AgentRole",
    "AgentAssignment",
    "AssignmentResult",
    "AssignmentReview",
    "AssignmentStatus",
    "AssignmentScope",
    "CollaborationMode",
    "CollaborationDecision",
    "CollaborationBudget",
    "CollaborationPolicyProfile",
    "CollaborationState",
    "WorkerState",
    "WorkerResult",
    "WorkerResultStatus",
    "WorkerReview",
    "ReviewDecision",
    "IntegrationResult",
    "IntegrationStatus",
    "DelegationAssessment",
    "MutationPolicy",
    "WorkerBudget",
    "RoutingConstraints",
    "RiskLevel",
    "WorkspaceStrategy",
    # Services & Infrastructure
    "WorkerLifecycleManager",
    "AgentCapabilityRegistry",
    "DelegationPlanner",
    "TaskCharacteristics",
    "AssignmentGraph",
    "ScopeReservationRegistry",
    "CoordinationBus",
    "CoordinationBlackboard",
    "CollaborationPolicyEngine",
    "CollaborationEventEmitter",
    "default_budget",
]
