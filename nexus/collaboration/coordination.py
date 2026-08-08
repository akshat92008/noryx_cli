"""
nexus/collaboration/coordination.py

CoordinationBus: orchestrator-mediated message routing between workers.
CoordinationBlackboard: thread-safe, atomic coordination store and blackboard.

Rules:
  - All messages are validated against the schema.
  - Workers cannot message each other directly.
  - Messages must not contain credentials.
  - Communication budgets are enforced.
  - Repeated equivalent requests are detected.
  - All messages are auditable.
  - Blackboard maintains canonical audit state and allows atomic updates.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from nexus.collaboration.models import (
    AgentAssignment,
    AssignmentResult,
    AssignmentStatus,
    CollaborationBudget,
    CollaborationState,
    CoordinationMessage,
    CoordinationMessageType,
    IntegrationResult,
    WorkerState,
)

logger = logging.getLogger(__name__)

# Tokens that must not appear in coordination messages
_CREDENTIAL_PATTERNS = (
    "api_key", "secret", "password", "token", "bearer",
    "private_key", "client_secret", "auth",
)

_MAX_CONTENT_SIZE = 32_768  # characters


def _content_contains_credentials(content: Mapping[str, Any]) -> bool:
    raw = json.dumps(content, default=str).lower()
    return any(pat in raw for pat in _CREDENTIAL_PATTERNS)


def _content_fingerprint(content: Mapping[str, Any]) -> str:
    raw = json.dumps(content, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:20]


def _message_valid(msg: CoordinationMessage) -> Tuple[bool, str]:
    if not msg.assignment_id:
        return False, "message missing assignment_id."
    if not msg.sender_id:
        return False, "message missing sender_id."
    if not msg.recipient_id:
        return False, "message missing recipient_id."
    if _content_contains_credentials(msg.content):
        return False, "message content appears to contain credentials. Rejected."
    raw_size = len(json.dumps(dict(msg.content), default=str))
    if raw_size > _MAX_CONTENT_SIZE:
        return False, f"message content exceeds maximum size ({raw_size} > {_MAX_CONTENT_SIZE})."
    return True, ""


class DirectPeerMessageBlocked(RuntimeError):
    """Raised when a worker attempts direct peer messaging."""


class CoordinationBudgetExceeded(RuntimeError):
    """Raised when per-worker or global communication budget is exceeded."""


class DuplicateMessageDetected(RuntimeError):
    """Raised when an equivalent message has already been sent recently."""


class CoordinationBus:
    """
    Single-channel message router.
    Workers must address the orchestrator (ORCHESTRATOR_ID constant).
    The orchestrator may forward content to specific workers.
    """

    ORCHESTRATOR_ID = "orchestrator"

    def __init__(
        self,
        max_messages_per_worker: int = 50,
        max_global_messages: int = 500,
        on_message: Optional[Callable[[CoordinationMessage], None]] = None,
    ) -> None:
        self._max_per_worker = max_messages_per_worker
        self._max_global = max_global_messages
        self._on_message = on_message

        self._inbox: Dict[str, List[CoordinationMessage]] = defaultdict(list)
        self._audit_log: List[CoordinationMessage] = []
        self._worker_counts: Dict[str, int] = defaultdict(int)
        self._seen_fingerprints: Dict[str, set[str]] = defaultdict(set)

    def send(
        self,
        sender_id: str,
        recipient_id: str,
        message_type: CoordinationMessageType,
        assignment_id: str,
        content: Mapping[str, Any],
        evidence_ids: Tuple[str, ...] = (),
    ) -> CoordinationMessage:
        if (
            sender_id != self.ORCHESTRATOR_ID
            and recipient_id != self.ORCHESTRATOR_ID
        ):
            raise DirectPeerMessageBlocked(
                f"Worker '{sender_id}' attempted direct message to '{recipient_id}'. "
                "All messages must route through the orchestrator."
            )

        msg = CoordinationMessage(
            message_id=str(uuid.uuid4()),
            sender_id=sender_id,
            recipient_id=recipient_id,
            message_type=message_type,
            assignment_id=assignment_id,
            content=content,
            evidence_ids=evidence_ids,
            timestamp=datetime.now(tz=timezone.utc),
        )

        valid, reason = _message_valid(msg)
        if not valid:
            raise ValueError(f"Coordination message rejected: {reason}")

        if len(self._audit_log) >= self._max_global:
            raise CoordinationBudgetExceeded(
                f"Global coordination message limit ({self._max_global}) exceeded."
            )

        if self._worker_counts[sender_id] >= self._max_per_worker:
            raise CoordinationBudgetExceeded(
                f"Worker '{sender_id}' exceeded per-worker message limit ({self._max_per_worker})."
            )

        fp = _content_fingerprint(content)
        if fp in self._seen_fingerprints[sender_id]:
            raise DuplicateMessageDetected(
                f"Worker '{sender_id}' sent a duplicate message (fingerprint={fp})."
            )
        self._seen_fingerprints[sender_id].add(fp)

        self._inbox[recipient_id].append(msg)
        self._audit_log.append(msg)
        self._worker_counts[sender_id] += 1

        logger.debug(
            "CoordinationBus: %s → %s [%s] assignment=%s",
            sender_id, recipient_id, message_type.value, assignment_id,
        )

        if self._on_message:
            self._on_message(msg)

        return msg

    def drain(self, recipient_id: str) -> List[CoordinationMessage]:
        messages = list(self._inbox[recipient_id])
        self._inbox[recipient_id].clear()
        return messages

    def peek(self, recipient_id: str) -> List[CoordinationMessage]:
        return list(self._inbox[recipient_id])

    def full_audit_log(self) -> List[CoordinationMessage]:
        return list(self._audit_log)

    def messages_for_assignment(self, assignment_id: str) -> List[CoordinationMessage]:
        return [m for m in self._audit_log if m.assignment_id == assignment_id]

    def worker_message_count(self, worker_id: str) -> int:
        return self._worker_counts[worker_id]


class CoordinationBlackboard:
    """
    Thread-safe coordination store / blackboard.
    Tracks canonical state, task contract, plan version, evidence, costs, and audit log.
    """

    def __init__(self, task_contract_id: str, plan_id: str, plan_version: int = 1) -> None:
        self._lock = threading.Lock()
        self.task_contract_id = task_contract_id
        self.plan_id = plan_id
        self.plan_version = plan_version
        self.repository_snapshot_id = "main"

        self.state = CollaborationState.ANALYZING
        self.assignments: Dict[str, AgentAssignment] = {}
        self.assignment_states: Dict[str, WorkerState] = {}
        self.worker_results: Dict[str, AssignmentResult] = {}
        self.findings: List[Dict[str, Any]] = []
        self.evidence: List[str] = []
        self.scope_expansion_requests: List[Dict[str, Any]] = []
        self.conflicts: List[str] = []
        self.integration_result: Optional[IntegrationResult] = None
        self.verification_passed: bool = False
        self.total_cost_usd: Decimal = Decimal("0.00")
        self.audit_trail: List[Dict[str, Any]] = []

    def record_transition(self, new_state: CollaborationState, note: str = "") -> None:
        with self._lock:
            old = self.state
            self.state = new_state
            entry = {
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "event": "state_transition",
                "from": old.value,
                "to": new_state.value,
                "note": note,
            }
            self.audit_trail.append(entry)

    def register_assignment(self, assignment: AgentAssignment) -> None:
        with self._lock:
            self.assignments[assignment.assignment_id] = assignment
            self.assignment_states[assignment.assignment_id] = WorkerState.CREATED
            self.audit_trail.append({
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "event": "assignment_registered",
                "assignment_id": assignment.assignment_id,
            })

    def update_assignment_state(self, assignment_id: str, state: WorkerState) -> None:
        with self._lock:
            self.assignment_states[assignment_id] = state
            self.audit_trail.append({
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "event": "worker_state_updated",
                "assignment_id": assignment_id,
                "state": state.value,
            })

    def add_result(self, result: AssignmentResult) -> None:
        with self._lock:
            self.worker_results[result.assignment_id] = result
            if result.cost and result.cost.cost_usd:
                self.total_cost_usd += result.cost.cost_usd
            if result.evidence_ids:
                self.evidence.extend(result.evidence_ids)

    def request_scope_expansion(self, assignment_id: str, requested_path: str, reason: str) -> None:
        with self._lock:
            req = {
                "request_id": str(uuid.uuid4()),
                "assignment_id": assignment_id,
                "requested_path": requested_path,
                "reason": reason,
                "timestamp": datetime.now(tz=timezone.utc).isoformat(),
                "approved": False,
            }
            self.scope_expansion_requests.append(req)
            self.audit_trail.append({
                "timestamp": req["timestamp"],
                "event": "scope_expansion_requested",
                "assignment_id": assignment_id,
                "requested_path": requested_path,
            })

    def get_summary(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "task_contract_id": self.task_contract_id,
                "plan_id": self.plan_id,
                "plan_version": self.plan_version,
                "state": self.state.value,
                "assignments_count": len(self.assignments),
                "completed_results": len(self.worker_results),
                "evidence_count": len(self.evidence),
                "total_cost_usd": str(self.total_cost_usd),
                "verification_passed": self.verification_passed,
            }
