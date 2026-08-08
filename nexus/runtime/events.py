"""
Typed event system for the Nexus execution engine.

Provides structured events emitted during an agent's run lifecycle.
"""

from dataclasses import dataclass, field
from enum import Enum


class EventType(str, Enum):
    """Types of runtime events."""

    RUN_STARTED = "run_started"
    RUN_COMPLETED = "run_completed"
    RUN_FAILED = "run_failed"
    TURN_STARTED = "turn_started"
    TURN_COMPLETED = "turn_completed"
    MODEL_REQUEST_STARTED = "model_request_started"
    MODEL_REQUEST_COMPLETED = "model_request_completed"
    MODEL_STREAM_CHUNK = "model_stream_chunk"
    TOOL_CALL_STARTED = "tool_call_started"
    TOOL_CALL_COMPLETED = "tool_call_completed"
    WAITING_FOR_USER = "waiting_for_user"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class BaseEvent:
    """Base class for all runtime events."""

    type: EventType
    timestamp: float = field(default_factory=lambda: __import__("time").time())


@dataclass
class RunStarted(BaseEvent):
    type: EventType = EventType.RUN_STARTED
    conversation_id: str = ""
    model: str = ""


@dataclass
class RunCompleted(BaseEvent):
    type: EventType = EventType.RUN_COMPLETED
    content: str = ""


@dataclass
class RunFailed(BaseEvent):
    type: EventType = EventType.RUN_FAILED
    error: str = ""


@dataclass
class TurnStarted(BaseEvent):
    type: EventType = EventType.TURN_STARTED
    turn_number: int = 1


@dataclass
class TurnCompleted(BaseEvent):
    type: EventType = EventType.TURN_COMPLETED
    turn_number: int = 1
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)


@dataclass
class ModelRequestStarted(BaseEvent):
    type: EventType = EventType.MODEL_REQUEST_STARTED
    model: str = ""
    messages: list[dict] = field(default_factory=list)


@dataclass
class ModelRequestCompleted(BaseEvent):
    type: EventType = EventType.MODEL_REQUEST_COMPLETED
    model: str = ""
    usage: dict = field(default_factory=dict)


@dataclass
class ModelStreamChunk(BaseEvent):
    type: EventType = EventType.MODEL_STREAM_CHUNK
    text: str = ""


@dataclass
class ToolCallStarted(BaseEvent):
    type: EventType = EventType.TOOL_CALL_STARTED
    tool_name: str = ""
    arguments: dict = field(default_factory=dict)


@dataclass
class ToolCallCompleted(BaseEvent):
    type: EventType = EventType.TOOL_CALL_COMPLETED
    tool_name: str = ""
    arguments: dict = field(default_factory=dict)
    result: str = ""
    success: bool = True
    error: str | None = None


@dataclass
class WaitingForUser(BaseEvent):
    type: EventType = EventType.WAITING_FOR_USER
    prompt: str = ""


@dataclass
class WarningEvent(BaseEvent):
    type: EventType = EventType.WARNING
    message: str = ""


@dataclass
class ErrorEvent(BaseEvent):
    type: EventType = EventType.ERROR
    message: str = ""
@dataclass
class FailureEvent(BaseEvent):
    type: EventType = EventType.ERROR
    kind: str = ""
    message: str = ""
    # Additional fields can be added for detailed failure evidence
    # e.g., error_category: FailureKind, raw_output: str, timestamp, etc.

