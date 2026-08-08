"""Execution session orchestrator.

Provides a unified facade for interactive and DAG execution paths.
"""

from __future__ import annotations

from typing import Callable, Generator

from nexus.planner import ExecutionPlan
from nexus.providers.base import Provider
from nexus.run_state import RunLedger
from nexus.runtime.events import BaseEvent
from nexus.runtime.kernel import (
    ExecutionKernel,
    ExecutionResult,
    PlanReviewer,
    StepExecutor,
    StepRepairer,
    StepVerifier,
    TaskDagKernel,
)


class ExecutionSession:
    """Canonical orchestration session for Nexus execution paths."""

    def __init__(
        self,
        provider: Provider | None = None,
        max_turns: int = 50,
        model_id: str | None = None,
        run_id: str | None = None,
        plan: ExecutionPlan | None = None,
        ledger: RunLedger | None = None,
        max_total_repairs: int | None = None,
    ):
        self.provider = provider
        self.max_turns = max_turns
        self.model_id = model_id
        self.run_id = run_id
        self.plan = plan
        self.ledger = ledger
        self.max_total_repairs = max_total_repairs

        self._interactive_kernel: ExecutionKernel | None = None
        self._dag_kernel: TaskDagKernel | None = None

    @property
    def interactive(self) -> ExecutionKernel:
        if self._interactive_kernel is None:
            self._interactive_kernel = ExecutionKernel(
                provider=self.provider,
                max_turns=self.max_turns,
                model_id=self.model_id,
                run_id=self.run_id,
                ledger=self.ledger,
            )
        return self._interactive_kernel

    @property
    def dag(self) -> TaskDagKernel:
        if self._dag_kernel is None:
            if not self.plan or not self.ledger:
                raise ValueError("TaskDagKernel requires a plan and a ledger.")
            self._dag_kernel = TaskDagKernel(
                plan=self.plan,
                ledger=self.ledger,
                max_total_repairs=self.max_total_repairs,
            )
        return self._dag_kernel

    def run_interactive(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        tool_executor: Callable[[str, dict], tuple[bool, str]] | None = None,
        event_handler: Callable[[BaseEvent], None] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Generator[BaseEvent, None, None]:
        kernel = self.interactive
        if tool_executor:
            kernel.tool_executor = tool_executor
        if event_handler:
            kernel.add_event_handler(event_handler)

        yield from kernel.run_interactive(
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def run_dag(
        self,
        execute: StepExecutor,
        *,
        verify: StepVerifier | None = None,
        repair: StepRepairer | None = None,
        reviewer: PlanReviewer | None = None,
    ) -> ExecutionResult:
        return self.dag.run_dag(
            execute=execute,
            verify=verify,
            repair=repair,
            reviewer=reviewer,
        )
