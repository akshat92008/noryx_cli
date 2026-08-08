"""
nexus/collaboration/lead_orchestrator.py

LeadOrchestrator: the single controlling entity for a collaboration run.

Responsible for:
  - Collaboration eligibility decision (DelegationPlanner)
  - Task contract & plan binding
  - Task decomposition & graph validation
  - Model assignment & pre-call budget reservation (Sprint 9 integration)
  - Isolated worker workspace lifecycle management
  - Coordination bus & blackboard routing
  - Structured worker output collection & independent review
  - Transactional patch integration & conflict detection
  - Independent central verification on exact integrated tree hash
  - Canonical finalisation (via RunFinalizer — NEVER by workers)
  - Recovery & single-agent fallback (Sprint 7 RecoveryController integration)
  - Cancellation & observability events

Non-delegatable responsibilities (enforced in code):
  - Workers cannot finalize the parent run or issue VERIFIED
  - Workers cannot bypass the scope reservation gateway
  - Workers cannot create additional workers
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Sequence

from nexus.collaboration.assignments import AssignmentGraph, AssignmentValidationError
from nexus.collaboration.capabilities import AgentCapabilityRegistry
from nexus.collaboration.conflicts import ReservationMode, ScopeReservationRegistry
from nexus.collaboration.context_partitioning import ContextPartitioner
from nexus.collaboration.coordination import CoordinationBlackboard, CoordinationBus
from nexus.collaboration.delegation import DelegationPlanner, TaskCharacteristics
from nexus.collaboration.integration import IntegrationCoordinator
from nexus.collaboration.lifecycle import WorkerLifecycleManager
from nexus.collaboration.models import (
    AgentAssignment,
    AgentRole,
    CollaborationMode,
    CollaborationPolicyProfile,
    CollaborationRunState,
    CollaborationState,
    IntegrationStatus,
    ReviewDecision,
    WorkerState,
    WorkspaceStrategy,
)
from nexus.collaboration.observability import (
    ASSIGNMENT_CREATED,
    ASSIGNMENT_SCHEDULED,
    CENTRAL_VERIFICATION_COMPLETED,
    CENTRAL_VERIFICATION_STARTED,
    COLLABORATION_COMPLETED,
    COLLABORATION_DECISION_CREATED,
    COLLABORATION_FAILED,
    COLLABORATION_FALLBACK_SELECTED,
    COLLABORATION_STARTED,
    INTEGRATION_COMPLETED,
    INTEGRATION_CONFLICT_DETECTED,
    INTEGRATION_FAILED,
    INTEGRATION_STARTED,
    WORKER_ACCEPTED,
    WORKER_CANCELLED,
    WORKER_REJECTED,
    WORKER_STARTED,
    CollaborationEventEmitter,
)
from nexus.collaboration.persistence import CollaborationPersistence
from nexus.collaboration.policies import CollaborationPolicyEngine
from nexus.collaboration.review import ResultReviewService
from nexus.collaboration.worker_runtime import WorkerRuntime

logger = logging.getLogger(__name__)


class LeadOrchestrator:
    """
    Lead orchestrator for multi-agent collaboration runs.
    Single-agent fallback is automatic when policy is DISABLED, task is trivial/coupled,
    or budget is insufficient.
    """

    def __init__(
        self,
        run_id: str,
        policy: CollaborationPolicyProfile,
        lead_workspace_root: Path,
        current_revision: str = "main",
        persistence_dir: Optional[Path] = None,
        capability_registry: Optional[AgentCapabilityRegistry] = None,
        verification_service: Optional[object] = None,
        provider_coordinator: Optional[object] = None,
        tool_execution_service: Optional[object] = None,
        recovery_service: Optional[object] = None,
        local_only: bool = False,
        task_contract_id: str = "task-contract-0",
        plan_id: str = "plan-0",
        plan_version: int = 1,
    ) -> None:
        self._run_id = run_id
        self._current_revision = current_revision
        self._lead_root = lead_workspace_root.resolve()

        self._policy_engine = CollaborationPolicyEngine(profile=policy, local_only=local_only)
        self._capabilities = capability_registry or AgentCapabilityRegistry()
        self._scope_registry = ScopeReservationRegistry()
        self._bus = CoordinationBus()
        self._blackboard = CoordinationBlackboard(task_contract_id, plan_id, plan_version)
        self._lifecycle = WorkerLifecycleManager(self._lead_root, current_revision=current_revision)
        self._partitioner = ContextPartitioner(current_revision)
        # Verification is intentionally not stubbed.  Missing verification must
        # block mutating collaboration rather than manufacture a structural pass.
        self._integrator = IntegrationCoordinator(
            current_revision=current_revision,
            verification_service=verification_service,
            lead_workspace_root=self._lead_root,
        )
        self._reviewer = ResultReviewService(known_repository_revision=current_revision)
        self._emitter = CollaborationEventEmitter()
        self._emitter.register_logger()

        collaboration_id = str(uuid.uuid4())
        self._state = CollaborationRunState(
            run_id=run_id,
            collaboration_id=collaboration_id,
            state=CollaborationState.ANALYZING,
            policy=policy,
            budget=self._policy_engine.budget,
        )

        pdir = persistence_dir or (self._lead_root / ".noryx" / "runs" / run_id / "collaboration")
        self._persistence = CollaborationPersistence(pdir)
        self._worker_runtime = WorkerRuntime(
            capability_registry=self._capabilities,
            scope_registry=self._scope_registry,
            provider_coordinator=provider_coordinator,
            tool_execution_service=tool_execution_service,
            verification_service=verification_service,
            recovery_service=recovery_service,
        )

        self._cancellation_events: Dict[str, asyncio.Event] = {}

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def run_collaboration(
        self,
        assignments: Sequence[AgentAssignment],
        parent_resources: Optional[list] = None,
        task_characteristics: Optional[TaskCharacteristics] = None,
    ) -> CollaborationRunState:
        """
        Execute a full collaboration run.
        Returns the final CollaborationRunState.
        Workers NEVER finalize the parent run or issue overall VERIFIED.
        """
        self._emitter.emit(
            COLLABORATION_STARTED,
            parent_run_id=self._run_id,
            collaboration_id=self._state.collaboration_id,
            state=CollaborationState.ANALYZING.value,
        )

        # 1. Eligibility Decision Stage
        planner = DelegationPlanner(policy=self._state.policy, budget=self._state.budget)
        if task_characteristics is None:
            task_characteristics = TaskCharacteristics(
                task_id=self._run_id,
                description="Engineering task",
                estimated_files_affected=len(assignments) * 2,
                packages_involved=["nexus"],
                languages_involved=["python"],
                independent_workstreams=[a.assignment_id for a in assignments],
                sequential_dependencies=[],
                estimated_context_tokens=15000,
                requires_security_review=any(
                    a.role == AgentRole.SECURITY_REVIEWER for a in assignments
                ),
                requires_architecture_review=False,
                dependency_coupling_score=0.2,
                time_budget_seconds=300,
                financial_budget_usd=1.0,
                local_only=self._policy_engine.local_only,
                worker_isolation_available=True,
            )

        decision = planner.decide(task_characteristics)
        self._state.mode = decision.recommended_mode
        self._blackboard.record_transition(
            CollaborationState.ANALYZING, f"Eligibility: {decision.recommended_mode.value}"
        )

        self._emitter.emit(
            COLLABORATION_DECISION_CREATED,
            parent_run_id=self._run_id,
            collaboration_id=self._state.collaboration_id,
            use_collaboration=decision.use_collaboration,
            mode=decision.recommended_mode.value,
        )

        # Single-Agent Fallback check
        if (
            not decision.use_collaboration
            or decision.recommended_mode == CollaborationMode.SINGLE_AGENT
        ):
            logger.info("LeadOrchestrator: task selected SINGLE_AGENT mode.")
            self._state.mode = CollaborationMode.SINGLE_AGENT
            self._emitter.emit(
                COLLABORATION_FALLBACK_SELECTED,
                parent_run_id=self._run_id,
                collaboration_id=self._state.collaboration_id,
                reason="Single-agent fallback selected by eligibility engine.",
            )

        # 2. Build and Validate Assignment Graph
        self._state.state = CollaborationState.DECOMPOSING
        self._blackboard.record_transition(CollaborationState.DECOMPOSING)
        graph = AssignmentGraph()

        for assignment in assignments:
            try:
                graph.add_assignment(assignment)
                self._state.assignments[assignment.assignment_id] = assignment
                self._blackboard.register_assignment(assignment)
                self._emitter.emit(
                    ASSIGNMENT_CREATED,
                    parent_run_id=self._run_id,
                    collaboration_id=self._state.collaboration_id,
                    assignment_id=assignment.assignment_id,
                    role=assignment.role.value,
                )
            except AssignmentValidationError as exc:
                logger.error("LeadOrchestrator: rejected assignment: %s", exc)
                self._state.state = CollaborationState.FAILED
                self._blackboard.record_transition(
                    CollaborationState.FAILED, f"Invalid assignment: {exc}"
                )
                return self._state

        # 3. Reserve Scope for Mutating Assignments
        self._state.state = CollaborationState.VALIDATING_ASSIGNMENTS
        self._blackboard.record_transition(CollaborationState.VALIDATING_ASSIGNMENTS)

        for assignment in assignments:
            mutation_paths = assignment.allowed_mutation_paths or assignment.allowed_paths
            is_mutating = assignment.mutation_policy.allowed or assignment.role in (
                AgentRole.IMPLEMENTER,
                AgentRole.TEST_ENGINEER,
                AgentRole.INTEGRATION_ENGINEER,
            )
            if is_mutating and mutation_paths:
                try:
                    reservation = self._scope_registry.reserve(
                        assignment_id=assignment.assignment_id,
                        paths=tuple(Path(p) for p in mutation_paths),
                        symbol_ids=assignment.relevant_symbols,
                        mode=ReservationMode.EXCLUSIVE,
                    )
                    self._state.reservations[reservation.reservation_id] = reservation
                except Exception as exc:
                    logger.error(
                        "LeadOrchestrator: scope reservation failed for '%s': %s",
                        assignment.assignment_id,
                        exc,
                    )
                    self._state.state = CollaborationState.FAILED
                    self._blackboard.record_transition(
                        CollaborationState.FAILED, f"Scope reservation conflict: {exc}"
                    )
                    return self._state

        # 4. Prepare Workers & Execute
        self._state.state = CollaborationState.PREPARING_WORKERS
        self._persistence.save(self._state)

        parallel_groups = graph.find_parallel_groups()
        self._state.state = CollaborationState.RUNNING_WORKERS
        self._blackboard.record_transition(CollaborationState.RUNNING_WORKERS)

        for group in parallel_groups:
            if self._state.cancelled:
                break
            await self._run_group(group, graph, parent_resources or [])

        if self._state.cancelled:
            self._state.state = CollaborationState.CANCELLED
            self._blackboard.record_transition(CollaborationState.CANCELLED)
            self._cleanup_all()
            self._emitter.emit(
                COLLABORATION_FAILED,
                parent_run_id=self._run_id,
                collaboration_id=self._state.collaboration_id,
                state=CollaborationState.CANCELLED.value,
            )
            return self._state

        # 5. Independent Review of Worker Results
        self._state.state = CollaborationState.REVIEWING_RESULTS
        self._blackboard.record_transition(CollaborationState.REVIEWING_RESULTS)
        accepted_results = []

        for aid, result in self._state.worker_results.items():
            assignment = self._state.assignments.get(aid)
            if assignment is None:
                continue
            review = self._reviewer.review(
                result, assignment, self._current_revision, reviewer_id=f"reviewer-{aid[:4]}"
            )
            self._state.worker_reviews[aid] = review
            if review.accepted and review.decision == ReviewDecision.APPROVE_FOR_INTEGRATION:
                accepted_results.append(result)
                self._emitter.emit(
                    WORKER_ACCEPTED,
                    parent_run_id=self._run_id,
                    collaboration_id=self._state.collaboration_id,
                    assignment_id=aid,
                )
            else:
                self._emitter.emit(
                    WORKER_REJECTED,
                    parent_run_id=self._run_id,
                    collaboration_id=self._state.collaboration_id,
                    assignment_id=aid,
                )

        # 6. Real Patch Integration & Conflict Detection
        self._state.state = CollaborationState.INTEGRATING
        self._blackboard.record_transition(CollaborationState.INTEGRATING)
        self._emitter.emit(
            INTEGRATION_STARTED,
            parent_run_id=self._run_id,
            collaboration_id=self._state.collaboration_id,
        )

        integration_result = self._integrator.integrate(
            accepted_results=accepted_results,
            reviews=self._state.worker_reviews,
        )
        self._state.integration_result = integration_result
        self._blackboard.integration_result = integration_result

        if integration_result.conflicts:
            self._emitter.emit(
                INTEGRATION_CONFLICT_DETECTED,
                parent_run_id=self._run_id,
                collaboration_id=self._state.collaboration_id,
                conflicts=list(integration_result.conflicts),
            )

        if (
            integration_result.status in (IntegrationStatus.FAILED, IntegrationStatus.CONFLICTED)
            and not integration_result.integrated_assignments
        ):
            self._state.state = CollaborationState.FAILED
            self._blackboard.record_transition(
                CollaborationState.FAILED, "Integration failed or blocked by conflicts"
            )
            self._emitter.emit(
                INTEGRATION_FAILED,
                parent_run_id=self._run_id,
                collaboration_id=self._state.collaboration_id,
            )
            self._cleanup_all()
            return self._state

        self._emitter.emit(
            INTEGRATION_COMPLETED,
            parent_run_id=self._run_id,
            collaboration_id=self._state.collaboration_id,
            integrated_tree=integration_result.integrated_tree or "",
        )

        # 7. Independent Central Verification on Integrated Tree Hash
        self._state.state = CollaborationState.VERIFYING
        self._blackboard.record_transition(CollaborationState.VERIFYING)
        self._emitter.emit(
            CENTRAL_VERIFICATION_STARTED,
            parent_run_id=self._run_id,
            collaboration_id=self._state.collaboration_id,
        )

        verification_passed = any(
            v.startswith("central_verification:PASS")
            for v in integration_result.verification_results
        )
        self._blackboard.verification_passed = verification_passed

        self._emitter.emit(
            CENTRAL_VERIFICATION_COMPLETED,
            parent_run_id=self._run_id,
            collaboration_id=self._state.collaboration_id,
            state="PASS" if verification_passed else "FAIL",
        )

        if not verification_passed:
            self._state.state = CollaborationState.FAILED
            self._blackboard.record_transition(
                CollaborationState.FAILED, "Central verification failed"
            )
            self._cleanup_all()
            return self._state

        # 8. Mark Run Completed
        self._state.state = CollaborationState.COMPLETED
        self._blackboard.record_transition(CollaborationState.COMPLETED)
        self._persistence.save(self._state)
        self._cleanup_all()

        self._emitter.emit(
            COLLABORATION_COMPLETED,
            parent_run_id=self._run_id,
            collaboration_id=self._state.collaboration_id,
            state=CollaborationState.COMPLETED.value,
        )
        return self._state

    # ------------------------------------------------------------------
    # Cancellation & Recovery
    # ------------------------------------------------------------------

    def cancel_worker(self, worker_id: str) -> None:
        event = self._cancellation_events.get(worker_id)
        if event:
            event.set()
            self._emitter.emit(
                WORKER_CANCELLED,
                parent_run_id=self._run_id,
                collaboration_id=self._state.collaboration_id,
                worker_id=worker_id,
            )

    def cancel_all(self) -> None:
        self._state.cancelled = True
        for event in self._cancellation_events.values():
            event.set()

    # ------------------------------------------------------------------
    # Internal execution
    # ------------------------------------------------------------------

    async def _run_group(
        self,
        group: List[AgentAssignment],
        graph: AssignmentGraph,
        parent_resources: list,
    ) -> None:
        tasks = []
        for assignment in group:
            cancel_event = asyncio.Event()
            self._cancellation_events[assignment.assignment_id] = cancel_event

            self._emitter.emit(
                ASSIGNMENT_SCHEDULED,
                parent_run_id=self._run_id,
                collaboration_id=self._state.collaboration_id,
                assignment_id=assignment.assignment_id,
            )

            is_mutating = assignment.mutation_policy.allowed or assignment.role in (
                AgentRole.IMPLEMENTER,
                AgentRole.TEST_ENGINEER,
                AgentRole.INTEGRATION_ENGINEER,
            )

            strategy = (
                WorkspaceStrategy.ISOLATED_TEMPORARY_COPY
                if is_mutating
                else WorkspaceStrategy.READ_ONLY_SHARED_SNAPSHOT
            )

            record = self._lifecycle.create_worker(assignment)
            workspace = self._lifecycle.prepare_workspace(record.worker_id, strategy)

            context = self._partitioner.build_packet(
                assignment=assignment,
                parent_objective=f"parent_run:{self._run_id}",
                available_resources=parent_resources,
            )

            self._state.worker_states[record.worker_id] = WorkerState.RUNNING
            self._blackboard.update_assignment_state(assignment.assignment_id, WorkerState.RUNNING)
            self._emitter.emit(
                WORKER_STARTED,
                parent_run_id=self._run_id,
                collaboration_id=self._state.collaboration_id,
                worker_id=record.worker_id,
                assignment_id=assignment.assignment_id,
            )

            task = asyncio.create_task(
                self._worker_runtime.execute(assignment, context, workspace, cancel_event)
            )
            tasks.append((record.worker_id, assignment.assignment_id, task))

        results = await asyncio.gather(*[t for _, _, t in tasks], return_exceptions=True)

        for (worker_id, aid, _), result in zip(tasks, results, strict=True):
            graph.update_state(aid, WorkerState.SUBMITTED)
            if isinstance(result, Exception):
                logger.error("Worker %s raised: %s", worker_id, result)
                self._state.worker_states[worker_id] = WorkerState.FAILED
                self._blackboard.update_assignment_state(aid, WorkerState.FAILED)
            else:
                self._state.worker_results[aid] = result
                self._state.worker_states[worker_id] = WorkerState.SUBMITTED
                self._blackboard.add_result(result)
                self._blackboard.update_assignment_state(aid, WorkerState.SUBMITTED)

    def _cleanup_all(self) -> None:
        results = self._lifecycle.cleanup_all()
        for worker_id, success in results.items():
            if not success:
                logger.error("LeadOrchestrator: cleanup failed for worker %s.", worker_id)
        for aid in self._state.assignments:
            self._scope_registry.release_for_assignment(aid)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def state(self) -> CollaborationRunState:
        return self._state

    @property
    def collaboration_id(self) -> str:
        return self._state.collaboration_id

    @property
    def blackboard(self) -> CoordinationBlackboard:
        return self._blackboard
