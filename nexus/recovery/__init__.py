"""
Recovery & Failure Subsystem for Nexus CLI.
Exports canonical RecoveryController, RollbackManager, Taxonomy, and Budget primitives.
"""

from nexus.recovery.baseline import BaselineAnalyzer, FailureRelation
from nexus.recovery.budget import RecoveryBudget
from nexus.recovery.controller import RecoveryController
from nexus.recovery.diagnosers import (
    BuildLintTypeDiagnoser,
    EnvironmentDiagnoser,
    PatchQualityDiagnoser,
    TestFailureDiagnoser,
)
from nexus.recovery.diagnosis import DiagnosisEngine
from nexus.recovery.events import (
    DiagnosisCompletedEvent,
    FailureDetectedEvent,
    FailureNormalizedEvent,
    LoopDetectedEvent,
    RecoveryStoppedEvent,
    RecoveryStrategySelectedEvent,
    RollbackCompletedEvent,
)
from nexus.recovery.extractor import SignalExtractor
from nexus.recovery.intervention import UserInterventionManager, UserInterventionRequest
from nexus.recovery.normalizer import FailureNormalizer
from nexus.recovery.records import (
    EvidenceReference,
    FailureCategory,
    FailureDiagnosis,
    FailureHypothesis,
    FailureKind,
    FailureRecord,
    FailureSeverity,
    HypothesisStatus,
)

# Preserve legacy RollbackManager class & import
from nexus.recovery.rollback import RollbackDecisionEngine, RollbackManager
from nexus.recovery.resume import SessionResumptionEngine
from nexus.recovery.signatures import AttemptSignature, LoopDetector
from nexus.recovery.strategies import (
    RecoveryStrategy,
    RecoveryStrategyType,
    StrategyRegistry,
)
from nexus.recovery.terminal import TerminalState, TerminalStateGovernance

__all__ = [
    "AttemptSignature",
    "BaselineAnalyzer",
    "BuildLintTypeDiagnoser",
    "DiagnosisCompletedEvent",
    "DiagnosisEngine",
    "EnvironmentDiagnoser",
    "EvidenceReference",
    "FailureCategory",
    "FailureDetectedEvent",
    "FailureDiagnosis",
    "FailureHypothesis",
    "FailureKind",
    "FailureNormalizedEvent",
    "FailureNormalizer",
    "FailureRecord",
    "FailureRelation",
    "FailureSeverity",
    "HypothesisStatus",
    "LoopDetectedEvent",
    "LoopDetector",
    "PatchQualityDiagnoser",
    "RecoveryBudget",
    "RecoveryController",
    "RecoveryStoppedEvent",
    "RecoveryStrategy",
    "RecoveryStrategySelectedEvent",
    "RecoveryStrategyType",
    "RollbackCompletedEvent",
    "RollbackDecisionEngine",
    "RollbackManager",
    "SessionResumptionEngine",
    "SignalExtractor",
    "StrategyRegistry",
    "TerminalState",
    "TerminalStateGovernance",
    "TestFailureDiagnoser",
    "UserInterventionManager",
    "UserInterventionRequest",
]
