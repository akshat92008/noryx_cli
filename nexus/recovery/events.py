"""
Structured Recovery Lifecycle Events for Nexus CLI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class RecoveryBaseEvent:
    event_type: str
    run_id: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FailureDetectedEvent(RecoveryBaseEvent):
    failure_id: str = ""
    summary: str = ""

    def __post_init__(self):
        self.event_type = "FailureDetected"


@dataclass
class FailureNormalizedEvent(RecoveryBaseEvent):
    failure_id: str = ""
    category: str = ""
    kind: str = ""

    def __post_init__(self):
        self.event_type = "FailureNormalized"


@dataclass
class DiagnosisCompletedEvent(RecoveryBaseEvent):
    diagnosis_id: str = ""
    recommended_strategy: str = ""
    confidence: float = 0.0

    def __post_init__(self):
        self.event_type = "DiagnosisCompleted"


@dataclass
class RecoveryStrategySelectedEvent(RecoveryBaseEvent):
    strategy_type: str = ""
    attempt_number: int = 1

    def __post_init__(self):
        self.event_type = "RecoveryStrategySelected"


@dataclass
class RollbackCompletedEvent(RecoveryBaseEvent):
    success: bool = True
    detail: str = ""

    def __post_init__(self):
        self.event_type = "RollbackCompleted"


@dataclass
class LoopDetectedEvent(RecoveryBaseEvent):
    strategy_type: str = ""
    reason: str = ""

    def __post_init__(self):
        self.event_type = "LoopDetected"


@dataclass
class RecoveryStoppedEvent(RecoveryBaseEvent):
    terminal_state: str = ""
    reason: str = ""

    def __post_init__(self):
        self.event_type = "RecoveryStopped"
