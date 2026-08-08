"""
nexus/collaboration/observability.py

Structured event emission for all collaboration lifecycle events.
Events are lightweight, auditable, and redact sensitive content by default.
Source code is not included in events.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event type constants (All 20 Sprint 10 Lifecycle Events)
# ---------------------------------------------------------------------------

COLLABORATION_DECISION_CREATED = "CollaborationDecisionCreated"
COLLABORATION_STARTED = "CollaborationStarted"
ASSIGNMENT_CREATED = "AssignmentCreated"
ASSIGNMENT_SCHEDULED = "AssignmentScheduled"
ASSIGNMENT_STARTED = "AssignmentStarted"
ASSIGNMENT_PROGRESSED = "AssignmentProgressed"
ASSIGNMENT_COMPLETED = "AssignmentCompleted"
ASSIGNMENT_FAILED = "AssignmentFailed"
ASSIGNMENT_BLOCKED = "AssignmentBlocked"
ASSIGNMENT_REVIEW_STARTED = "AssignmentReviewStarted"
ASSIGNMENT_REVIEW_COMPLETED = "AssignmentReviewCompleted"
SCOPE_EXPANSION_REQUESTED = "ScopeExpansionRequested"
INTEGRATION_STARTED = "IntegrationStarted"
PATCH_APPLIED = "PatchApplied"
INTEGRATION_CONFLICT_DETECTED = "IntegrationConflictDetected"
INTEGRATION_COMPLETED = "IntegrationCompleted"
CENTRAL_VERIFICATION_STARTED = "CentralVerificationStarted"
CENTRAL_VERIFICATION_COMPLETED = "CentralVerificationCompleted"
COLLABORATION_FALLBACK_SELECTED = "CollaborationFallbackSelected"
COLLABORATION_STOPPED = "CollaborationStopped"

# Backward compatibility aliases
DELEGATION_ASSESSED = "CollaborationDecisionCreated"
WORKER_PREPARED = "AssignmentScheduled"
WORKER_STARTED = "AssignmentStarted"
WORKER_BLOCKED = "AssignmentBlocked"
WORKER_RESULT_SUBMITTED = "AssignmentCompleted"
WORKER_RESULT_REVIEWED = "AssignmentReviewCompleted"
WORKER_ACCEPTED = "AssignmentReviewCompleted"
WORKER_REJECTED = "AssignmentReviewCompleted"
MUTATION_SCOPE_RESERVED = "ScopeExpansionRequested"
MUTATION_SCOPE_RELEASED = "ScopeExpansionRequested"
PARALLEL_GROUP_STARTED = "AssignmentScheduled"
PARALLEL_GROUP_COMPLETED = "AssignmentCompleted"
SEMANTIC_CONFLICT_DETECTED = "IntegrationConflictDetected"
ASSIGNMENT_INTEGRATED = "PatchApplied"
INTEGRATION_FAILED = "IntegrationConflictDetected"
WORKER_CANCELLED = "CollaborationStopped"
COLLABORATION_COMPLETED = "CentralVerificationCompleted"
COLLABORATION_FAILED = "CollaborationStopped"


@dataclass(frozen=True)
class CollaborationEvent:
    event_id: str
    event_type: str
    parent_run_id: str
    collaboration_id: str
    worker_id: Optional[str]
    assignment_id: Optional[str]
    state: Optional[str]
    model_tier: Optional[str]
    budget_usage: Dict[str, Any]
    timing_ms: float
    evidence_ids: Tuple[str, ...]
    error_summary: Optional[str]
    timestamp: datetime


class CollaborationEventEmitter:
    """
    Emits structured, privacy-safe collaboration events.
    Handlers are registered at runtime (e.g., for logging, telemetry, UI).
    """

    def __init__(self) -> None:
        self._handlers: List[Callable[[CollaborationEvent], None]] = []
        self._log_handler_registered = False

    def register_handler(self, handler: Callable[[CollaborationEvent], None]) -> None:
        self._handlers.append(handler)

    def register_logger(self) -> None:
        if not self._log_handler_registered:
            self._handlers.append(_log_event)
            self._log_handler_registered = True

    def emit(
        self,
        event_type: str,
        parent_run_id: str,
        collaboration_id: str,
        worker_id: Optional[str] = None,
        assignment_id: Optional[str] = None,
        state: Optional[str] = None,
        model_tier: Optional[str] = None,
        budget_usage: Optional[Dict[str, Any]] = None,
        timing_ms: float = 0.0,
        evidence_ids: Tuple[str, ...] = (),
        error: Optional[Exception] = None,
        **kwargs: Any,
    ) -> CollaborationEvent:
        error_summary: Optional[str] = None
        if error is not None:
            raw = f"{type(error).__name__}: {error}"
            error_summary = raw[:120]

        event = CollaborationEvent(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            parent_run_id=parent_run_id,
            collaboration_id=collaboration_id,
            worker_id=worker_id,
            assignment_id=assignment_id,
            state=state,
            model_tier=model_tier,
            budget_usage=budget_usage or {},
            timing_ms=timing_ms,
            evidence_ids=evidence_ids,
            error_summary=error_summary,
            timestamp=datetime.now(tz=timezone.utc),
        )

        for handler in self._handlers:
            try:
                handler(event)
            except Exception:
                logger.exception("CollaborationEventEmitter: handler %s failed", handler)

        return event


def _log_event(event: CollaborationEvent) -> None:
    logger.info(
        "[%s] event=%s run=%s collab=%s worker=%s assign=%s",
        event.timestamp.isoformat(),
        event.event_type,
        event.parent_run_id,
        event.collaboration_id,
        event.worker_id or "-",
        event.assignment_id or "-",
    )
