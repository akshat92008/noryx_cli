"""Canonical execution package.

The DAG execution engine and the audited process controller live under one
importable package so ``nexus.execution.controller`` is never shadowed by a
same-named module.
"""

from nexus.execution.controller import ExecutionController, ExecutionResult
from nexus.runtime.kernel import (
    ExecutionResult as KernelExecutionResult,
)
from nexus.runtime.kernel import (
    FailureKind,
    PlanReviewer,
    ReviewOutcome,
    StepExecutor,
    StepRepairer,
    StepVerifier,
    TaskOutcome,
    classify_failure,
)
from nexus.runtime.kernel import TaskDagKernel as ExecutionEngine

__all__ = [
    "ExecutionController",
    "ExecutionEngine",
    "ExecutionResult",
    "KernelExecutionResult",
    "FailureKind",
    "ReviewOutcome",
    "StepExecutor",
    "StepRepairer",
    "StepVerifier",
    "PlanReviewer",
    "TaskOutcome",
    "classify_failure",
]
