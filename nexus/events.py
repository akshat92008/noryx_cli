from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, List
import uuid

class EventType(Enum):
    TASK_STARTED = "TaskStarted"
    PLAN_CREATED = "PlanCreated"
    CONTEXT_SELECTED = "ContextSelected"
    TOOL_CALLED = "ToolCalled"
    COMMAND_EXECUTED = "CommandExecuted"
    FILE_MODIFIED = "FileModified"
    VERIFICATION_STARTED = "VerificationStarted"
    VERIFICATION_COMPLETED = "VerificationCompleted"
    RECOVERY_STARTED = "RecoveryStarted"
    TASK_COMPLETED = "TaskCompleted"
    TASK_FAILED = "TaskFailed"
    PROVIDER_ATTEMPT = "ProviderAttempt"
    AGENT_STATE_CHANGED = "AgentStateChanged"
    TASK_INTERPRETATION_STARTED = "TaskInterpretationStarted"
    TASK_CONTRACT_CREATED = "TaskContractCreated"
    AMBIGUITY_DETECTED = "AmbiguityDetected"
    CLARIFICATION_REQUESTED = "ClarificationRequested"
    PLANNING_STARTED = "PlanningStarted"
    PLAN_VALIDATION_FAILED = "PlanValidationFailed"
    PLAN_CRITIQUE_STARTED = "PlanCritiqueStarted"
    PLAN_CRITIQUE_COMPLETED = "PlanCritiqueCompleted"
    PLAN_APPROVED = "PlanApproved"
    PLAN_REJECTED = "PlanRejected"
    PLAN_REVISED = "PlanRevised"
    EXECUTION_CONTRACT_CREATED = "ExecutionContractCreated"

@dataclass
class NexusEvent:
    event_type: EventType
    run_id: str
    component: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))

class EventBus:
    """Centralized event bus for Nexus runtime."""
    _subscribers: Dict[EventType, List[Callable[[NexusEvent], None]]] = {
        event_type: [] for event_type in EventType
    }

    @classmethod
    def subscribe(cls, event_type: EventType, callback: Callable[[NexusEvent], None]):
        cls._subscribers[event_type].append(callback)

    @classmethod
    def unsubscribe(cls, event_type: EventType, callback: Callable[[NexusEvent], None]):
        if callback in cls._subscribers[event_type]:
            cls._subscribers[event_type].remove(callback)

    @classmethod
    def emit(cls, event: NexusEvent):
        for callback in cls._subscribers[event.event_type]:
            try:
                callback(event)
            except Exception as e:
                # Event handlers should not crash the main loop
                import logging
                logging.getLogger(__name__).error(f"Error in event handler for {event.event_type}: {e}")

    @classmethod
    def publish(cls, event_type: EventType, run_id: str, component: str, metadata: Dict[str, Any] = None):
        cls.emit(NexusEvent(event_type=event_type, run_id=run_id, component=component, metadata=metadata or {}))
