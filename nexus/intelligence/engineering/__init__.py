"""Noryx Engineering Brain: repository-aware control plane for coding agents."""

from .brain import EngineeringBrain, EngineeringContract
from .constraints import CompiledConstraint, ConstraintCompiler, ConstraintKind
from .failure_learning import FailureLearningIntegrityError, FailureLearningStore, FailureLesson
from .long_horizon import (
    LongHorizonConflictError,
    LongHorizonController,
    LongHorizonIntegrityError,
    LongHorizonPhase,
    LongHorizonState,
)
from .memory import (
    EngineeringMemoryStore,
    EngineeringTaskMemory,
    MemoryConflictError,
    MemoryIntegrityError,
)
from .scope import (
    ScopeContract,
    ScopeDecision,
    ScopeEvidenceType,
    ScopeExpansionEvidence,
    SurgicalScopeGuard,
)
from .semantic import SemanticVerificationResult, SemanticVerifier

__all__ = [
    "CompiledConstraint",
    "ConstraintCompiler",
    "ConstraintKind",
    "EngineeringBrain",
    "EngineeringContract",
    "EngineeringMemoryStore",
    "EngineeringTaskMemory",
    "FailureLearningIntegrityError",
    "FailureLearningStore",
    "FailureLesson",
    "LongHorizonConflictError",
    "LongHorizonController",
    "LongHorizonIntegrityError",
    "LongHorizonPhase",
    "LongHorizonState",
    "MemoryConflictError",
    "MemoryIntegrityError",
    "ScopeContract",
    "ScopeEvidenceType",
    "ScopeExpansionEvidence",
    "ScopeDecision",
    "SemanticVerificationResult",
    "SemanticVerifier",
    "SurgicalScopeGuard",
]
