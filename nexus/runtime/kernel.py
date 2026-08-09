"""Noryx runtime engines: interactive agentic loop and dependency-aware DAG executor."""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Generator

from nexus.memory import compact_messages
from nexus.planner import ExecutionPlan, PlanStep, TaskStatus
from nexus.providers.base import Provider
from nexus.recovery import RecoveryController
from nexus.recovery.normalizer import FailureNormalizer
from nexus.run_state import RunLedger
from nexus.runtime.events import (
    BaseEvent,
    ErrorEvent,
    FailureEvent,
    ModelRequestCompleted,
    ModelRequestStarted,
    ModelStreamChunk,
    RunAwaitingApproval,
    RunCompleted,
    RunFailed,
    RunStarted,
    ToolCallCompleted,
    ToolCallStarted,
    TurnCompleted,
    TurnStarted,
    RunAwaitingConfirmation,
)
from nexus.runtime.state_machine import RunState, StateMachine

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Shared outcome types (used by both engines and exported via nexus.execution)
# ---------------------------------------------------------------------------


class FailureKind(str, Enum):
    """Deterministic failure classes used to focus repair prompts."""

    SYNTAX = "syntax"
    IMPORT = "import"
    TYPE = "type"
    TEST = "test"
    RUNTIME = "runtime"
    DEPENDENCY = "dependency"
    TIMEOUT = "timeout"
    ENVIRONMENT = "environment"
    SECURITY = "security"
    UNKNOWN = "unknown"


# Per-difficulty wall-clock budgets (seconds). Operators can override via
# NORYX_WALL_CLOCK_BUDGET_<DIFFICULTY> env vars.
_WALL_CLOCK_BUDGETS: dict[str, int] = {
    "trivial": int(__import__("nexus.env", fromlist=["noryx_env"]).noryx_env("NORYX_WALL_CLOCK_TRIVIAL", "90")),
    "simple": int(__import__("nexus.env", fromlist=["noryx_env"]).noryx_env("NORYX_WALL_CLOCK_SIMPLE", "300")),
    "moderate": int(__import__("nexus.env", fromlist=["noryx_env"]).noryx_env("NORYX_WALL_CLOCK_MODERATE", "900")),
    "complex": int(__import__("nexus.env", fromlist=["noryx_env"]).noryx_env("NORYX_WALL_CLOCK_COMPLEX", "1800")),
    "massive": int(__import__("nexus.env", fromlist=["noryx_env"]).noryx_env("NORYX_WALL_CLOCK_MASSIVE", "3600")),
}


def get_wall_clock_budget(difficulty: str) -> int:
    """Return the wall-clock time budget in seconds for the given difficulty label."""
    return _WALL_CLOCK_BUDGETS.get(difficulty.lower(), _WALL_CLOCK_BUDGETS["moderate"])


class StreamInterruptedError(OSError):
    """Raised when a provider stream is interrupted mid-delivery (transient)."""
    pass


@dataclass
class TaskOutcome:
    """Outcome returned by a task executor or repair callback."""

    success: bool
    summary: str
    evidence_ids: list[str] = field(default_factory=list)
    output: str = ""
    changed_files: list[str] = field(default_factory=list)
    verification_commands: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReviewOutcome:
    """Independent review result for a completed task or plan."""

    approved: bool
    summary: str
    findings: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class ExecutionResult:
    """Final state of a DAG execution."""

    status: TaskStatus
    completed: list[int]
    failed: list[int]
    blocked: list[int]
    repairs: int
    review: ReviewOutcome | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == TaskStatus.COMPLETED and not self.failed and not self.blocked


StepExecutor = Callable[[PlanStep], TaskOutcome]
StepVerifier = Callable[[PlanStep, TaskOutcome], TaskOutcome]
StepRepairer = Callable[[PlanStep, TaskOutcome, FailureKind, int], TaskOutcome]
PlanReviewer = Callable[[ExecutionPlan], ReviewOutcome]


# ---------------------------------------------------------------------------
# ExecutionKernel — interactive agentic loop
# ---------------------------------------------------------------------------


class ExecutionKernel:
    """The canonical interactive execution engine for the Noryx agent.

    Manages the agentic loop: sends messages to a provider, dispatches tool
    calls, accumulates results, and emits structured events throughout.

    Args:
        provider: Any object that satisfies the Provider protocol.
        max_turns: Maximum agentic turns before giving up.
        model_id: The effective model string to send to the provider API.
                  If omitted, falls back to ``provider.model_id`` or
                  ``provider.id``.
        run_id: Identifier for this run, used in RunStarted events for
                provenance correlation with the run ledger.
    """

    def __init__(
        self,
        provider: Provider | None = None,
        max_turns: int = 50,
        model_id: str | None = None,
        run_id: str | None = None,
        # Accept (but ignore) DAG-only kwargs so callers don't crash on
        # partial migrations where both constructors were sometimes mixed.
        plan: ExecutionPlan | None = None,
        ledger: RunLedger | None = None,
        max_total_repairs: int | None = None,
        wall_clock_deadline: float | None = None,
    ):
        self.provider = provider
        self.max_turns = max_turns
        if provider:
            self.model_id = (
                model_id
                or getattr(provider, "model_id", None)
                or getattr(provider, "id", "unknown")
            )
        else:
            self.model_id = model_id or "unknown"
        self.run_id = run_id
        self.state_machine = StateMachine()
        self.recovery = RecoveryController()
        self.tool_executor: Callable[[str, dict], tuple[bool, str]] | None = None
        self.before_tool_hook: Callable[[str, dict], None] | None = None
        self.after_tool_hook: Callable[[str, dict, bool, str], None] | None = None
        # Optional wall-clock deadline (monotonic seconds). When set, the loop
        # aborts with RunFailed if time.monotonic() exceeds this value.
        self.wall_clock_deadline: float | None = wall_clock_deadline
        # Consecutive empty-response retries before giving up.
        self._empty_response_retries: int = 0
        self._max_empty_retries: int = 2

        # Event handler registry
        self._event_handlers: list[Callable[[BaseEvent], None]] = []

        # DAG fields — kept as None for interactive instances; present so
        # that mixed code paths don't raise AttributeError.
        self.plan = plan
        self.ledger = ledger
        configured = int(plan.retry_policy.get("total_repairs", 5)) if plan else 5
        self.max_total_repairs = max(
            0, configured if max_total_repairs is None else max_total_repairs
        )
        self.repairs = 0

    # ------------------------------------------------------------------
    # Event registry
    # ------------------------------------------------------------------

    def add_event_handler(self, handler: Callable[[BaseEvent], None]) -> None:
        """Register a callable that receives every emitted event."""
        self._event_handlers.append(handler)

    def _emit(self, event: BaseEvent) -> None:
        """Dispatch an event to all registered handlers (errors are swallowed)."""
        for handler in self._event_handlers:
            try:
                handler(event)
            except (OSError, ValueError) as exc:  # pragma: no cover
                logger.warning("Event handler raised: %s", exc)

    def _create_and_emit(self, event: BaseEvent) -> BaseEvent:
        """Emit an event and return it (for use in ``yield`` expressions)."""
        self._emit(event)
        return event

    # ------------------------------------------------------------------
    # Interactive agentic loop
    # ------------------------------------------------------------------

    def run(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Generator[BaseEvent, None, None]:
        """Alias for :meth:`run_interactive` — preferred name used by tests."""
        yield from self.run_interactive(
            messages,
            tools=tools,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def run_interactive(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> Generator[BaseEvent, None, None]:
        """Execute the agentic loop.

        Yields events as they happen, and also calls registered event handlers.
        """
        if not self.state_machine.transition_to(RunState.EXECUTING):
            yield self._create_and_emit(ErrorEvent(message="Cannot start run from current state"))
            return

        yield self._create_and_emit(RunStarted(conversation_id=self.run_id, model=self.model_id))

        iteration = 0
        current_messages = list(messages)

        while iteration < self.max_turns:
            # Phase 7 Fix: Auto-compact long execution context inside the loop
            if len(current_messages) > 100:
                current_messages = compact_messages(current_messages)
                
            # ── Fix #4: Wall-clock deadline enforcement ──────────────────────
            if self.wall_clock_deadline is not None and time.monotonic() > self.wall_clock_deadline:
                logger.warning(
                    "ExecutionKernel: wall-clock deadline exceeded after %d turn(s).",
                    iteration,
                )
                self.state_machine.transition_to(RunState.FAILED)
                yield self._create_and_emit(
                    RunFailed(error="Wall-clock deadline exceeded for this task complexity tier.")
                )
                return

            iteration += 1
            yield self._create_and_emit(TurnStarted(turn_number=iteration))

            # Emit Model Request
            yield self._create_and_emit(
                ModelRequestStarted(model=self.model_id, messages=current_messages)
            )

            request_started = datetime.now(timezone.utc)
            try:
                stream = self.provider.chat(
                    model_id=self.model_id,
                    messages=current_messages,
                    tools=tools,
                    stream=True,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            except Exception as e:
                # Normalize the raw exception into a FailureRecord
                raw_msg = str(e)
                failure_record = FailureNormalizer.normalize(
                    raw_msg,
                    source_component="provider",
                    phase="provider_call",
                    run_id=self.run_id or "run-unknown",
                    command="provider.chat",
                    exit_code=None,
                    metadata={},
                )
                # Attempt recovery (may apply a repair strategy)
                recovered, strategy = self.recovery.diagnose_and_recover(failure_record, context={})

                if self.ledger and self.ledger.turn_dir:
                    completed = datetime.now(timezone.utc)
                    self.ledger.append_model_call(
                        role="executor",
                        model=self.model_id,
                        provider=str(getattr(self.provider, "id", "")),
                        status="failed",
                        started_at=request_started.isoformat(),
                        completed_at=completed.isoformat(),
                        duration_ms=int((completed - request_started).total_seconds() * 1000),
                        error_category=self.recovery.classify(raw_msg).value,
                        detail=raw_msg[:1000],
                    )
                # Emit failure event (including classification)
                yield self._create_and_emit(
                    FailureEvent(kind=self.recovery.classify(raw_msg), message=raw_msg)
                )
                yield self._create_and_emit(ErrorEvent(message=f"Provider error: {e}"))
                self.state_machine.transition_to(RunState.FAILED)
                yield self._create_and_emit(RunFailed(error=str(e)))
                return

            # Process Stream
            try:
                full_content, tool_calls, usage, request_id = yield from self._process_stream(
                    stream
                )
            except (OSError, ValueError) as exc:
                if self.ledger and self.ledger.turn_dir:
                    completed = datetime.now(timezone.utc)
                    self.ledger.append_model_call(
                        role="executor",
                        model=self.model_id,
                        provider=str(getattr(self.provider, "id", "")),
                        status="failed",
                        started_at=request_started.isoformat(),
                        completed_at=completed.isoformat(),
                        duration_ms=int((completed - request_started).total_seconds() * 1000),
                        error_category=self.recovery.classify(str(exc)).value,
                        detail=str(exc)[:1000],
                    )
                yield self._create_and_emit(
                    FailureEvent(kind=self.recovery.classify(str(exc)), message=str(exc))
                )
                yield self._create_and_emit(ErrorEvent(message=f"Provider stream error: {exc}"))
                self.state_machine.transition_to(RunState.FAILED)
                yield self._create_and_emit(RunFailed(error=str(exc)))
                return
            completed = datetime.now(timezone.utc)
            if self.ledger and self.ledger.turn_dir:
                self.ledger.append_model_call(
                    role="executor",
                    model=self.model_id,
                    provider=str(getattr(self.provider, "id", "")),
                    status="verified",
                    usage=usage,
                    request_id=request_id,
                    started_at=request_started.isoformat(),
                    completed_at=completed.isoformat(),
                    duration_ms=int((completed - request_started).total_seconds() * 1000),
                    has_tool_calls=bool(tool_calls),
                )
            yield self._create_and_emit(ModelRequestCompleted(model=self.model_id, usage=usage))

            # Record assistant message
            assistant_msg: dict = {"role": "assistant"}
            if full_content:
                assistant_msg["content"] = full_content
            if tool_calls:
                # Format for OpenAI schema — arguments MUST be a JSON string
                assistant_msg["tool_calls"] = [
                    {
                        "id": tc.get("id", f"call_{i}"),
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": (
                                tc["arguments"]
                                if isinstance(tc["arguments"], str)
                                else json.dumps(tc["arguments"])
                            ),
                        },
                    }
                    for i, tc in enumerate(tool_calls)
                ]
            current_messages.append(assistant_msg)

            yield self._create_and_emit(
                TurnCompleted(
                    turn_number=iteration,
                    content=full_content,
                    tool_calls=tool_calls,
                    assistant_msg=assistant_msg,
                )
            )

            # ── Fix #3: Empty response detection ─────────────────────────────
            if not tool_calls and not full_content.strip():
                self._empty_response_retries += 1
                logger.warning(
                    "ExecutionKernel: provider returned empty response (retry %d/%d).",
                    self._empty_response_retries,
                    self._max_empty_retries,
                )
                yield self._create_and_emit(
                    FailureEvent(
                        kind="PROVIDER_EMPTY_RESPONSE",
                        message=(
                            f"Provider returned an empty response with no content and no tool calls "
                            f"(attempt {self._empty_response_retries}/{self._max_empty_retries})."
                        ),
                    )
                )
                if self._empty_response_retries >= self._max_empty_retries:
                    self.state_machine.transition_to(RunState.FAILED)
                    yield self._create_and_emit(
                        RunFailed(
                            error="Provider repeatedly returned empty responses. Aborting."
                        )
                    )
                    return
                # Pop the empty assistant message we just appended and retry the turn
                if current_messages and current_messages[-1].get("role") == "assistant":
                    current_messages.pop()
                iteration -= 1  # Don't count empty-retry as a productive turn
                continue

            if not tool_calls:
                # No more tools to call — clean completion
                self._empty_response_retries = 0
                break

            # Execute Tools
            for tc in tool_calls:
                tool_name = tc.get("name")
                args = tc.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except json.JSONDecodeError:
                        pass  # Pass raw string if it's not valid JSON

                safe_args = args if isinstance(args, dict) else {"raw": args}

                yield self._create_and_emit(
                    ToolCallStarted(tool_name=tool_name, arguments=safe_args)
                )

                # Hook isolation: hook exceptions must not crash the execution loop
                if self.before_tool_hook:
                    try:
                        self.before_tool_hook(tool_name, safe_args)
                    except (OSError, ValueError) as hook_exc:
                        logger.warning("before_tool_hook raised: %s", hook_exc)

                if self.tool_executor:
                    try:
                        success, result_text = self.tool_executor(tool_name, safe_args)
                    except Exception as e:
                        success, result_text = False, f"Tool executor error: {e}"
                else:
                    success, result_text = False, "No tool executor registered"

                # ── Fix #2: AWAITING_APPROVAL detection ──────────────────────
                # When a mutation tool is pending human review the executor
                # embeds a "__AWAITING_APPROVAL__:<id>" sentinel in result_text.
                # We halt the run cleanly and emit RunAwaitingApproval so the
                # CLI can surface the approval UI instead of treating this as a
                # tool failure that triggers retry/replan loops.
                if isinstance(result_text, str) and result_text.startswith("__AWAITING_APPROVAL__:"):
                    first_line = result_text.split("\n", 1)[0]
                    confirmation_id = first_line.removeprefix("__AWAITING_APPROVAL__:").strip()
                    display_text = result_text[len(first_line):].strip()
                    yield self._create_and_emit(
                        ToolCallCompleted(
                            tool_name=tool_name,
                            arguments=safe_args,
                            result=display_text,
                            success=False,
                            error=None,
                        )
                    )
                    self.state_machine.transition_to(RunState.COMPLETED)
                    tool_call_id = tc.get("id", "")
                    yield self._create_and_emit(
                        RunAwaitingApproval(
                            confirmation_id=confirmation_id,
                            diff_preview=display_text,
                            tool_name=tool_name or "",
                            tool_call_id=tool_call_id,
                        )
                    )
                    return

                # ── Fix: PENDING_CONFIRMATION / HUMAN_ACTION_REQUIRED detection ─
                # When a command/package/scope/capability gate requires human
                # review, the executor embeds a
                # "__HUMAN_ACTION_REQUIRED__:<type>:<id>" sentinel in result_text.
                # We halt the run cleanly and emit RunAwaitingConfirmation so the
                # CLI returns the prompt to the user without feeding this back to
                # the LLM (which would trigger infinite retries).
                elif isinstance(result_text, str) and result_text.startswith("__HUMAN_ACTION_REQUIRED__:"):
                    first_line = result_text.split("\n", 1)[0]
                    rest = first_line.removeprefix("__HUMAN_ACTION_REQUIRED__:")
                    # Format: <action_type>:<confirmation_id>
                    parts = rest.split(":", 1)
                    action_type = parts[0] if parts else "COMMAND_CONFIRMATION"
                    confirmation_id = parts[1] if len(parts) > 1 else ""
                    display_text = result_text[len(first_line):].strip()
                    tool_call_id = tc.get("id", "")
                    yield self._create_and_emit(
                        ToolCallCompleted(
                            tool_name=tool_name,
                            arguments=safe_args,
                            result=display_text,
                            success=False,
                            error=None,
                            tool_call_id=tool_call_id,
                        )
                    )
                    self.state_machine.transition_to(RunState.COMPLETED)
                    yield self._create_and_emit(
                        RunAwaitingConfirmation(
                            confirmation_id=confirmation_id,
                            action_type=action_type,
                            display_message=display_text,
                            tool_name=tool_name or "",
                            tool_call_id=tool_call_id,
                        )
                    )
                    return

                if self.after_tool_hook:
                    try:
                        self.after_tool_hook(tool_name, safe_args, success, result_text)
                    except (OSError, ValueError) as hook_exc:
                        logger.warning("after_tool_hook raised: %s", hook_exc)

                yield self._create_and_emit(
                    ToolCallCompleted(
                        tool_name=tool_name,
                        arguments=safe_args,
                        result=result_text,
                        success=success,
                        error=None if success else result_text,
                        tool_call_id=tc.get("id", ""),
                    )
                )

                # ── Fix #1: Append tool result to conversation ───────────────
                # The model MUST see role=tool messages for every tool_call it
                # made in the previous assistant turn.  Without this the next
                # model request violates the OpenAI tool-calling protocol and
                # the model cannot know whether its action succeeded.
                tool_call_id = tc.get("id", f"call_{list(tool_calls).index(tc)}")
                current_messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": result_text if result_text else "(no output)",
                })

        else:
            # Max turns exhausted with tool calls still pending — this is a
            # partial/failed outcome, not a clean completion.
            logger.warning(
                "ExecutionKernel: max_turns (%d) exhausted with pending tool calls.",
                self.max_turns,
            )
            self.state_machine.transition_to(RunState.FAILED)
            yield self._create_and_emit(RunFailed(error=f"Max turns ({self.max_turns}) exhausted"))
            return

        self.state_machine.transition_to(RunState.COMPLETED)

        # Extract the final content from the last assistant message
        final_content = ""
        for msg in reversed(current_messages):
            if msg.get("role") == "assistant" and msg.get("content"):
                final_content = msg["content"]
                break

        yield self._create_and_emit(RunCompleted(content=final_content))

    def _process_stream(self, stream) -> tuple[str, list[dict], dict[str, int], str]:
        """Process the generator from the provider.

        Fix #12: Mid-stream transient failures (connection reset, read timeout)
        are re-raised as StreamInterruptedError so the kernel can retry the
        entire provider request without counting it as a successful turn.
        """
        full_content = ""
        tool_calls_accum: dict[int, dict] = {}
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        request_id = ""

        try:
            for chunk in stream:
                request_id = request_id or str(getattr(chunk, "id", "") or "")
                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    for key in usage:
                        value = (
                            chunk_usage.get(key, 0)
                            if isinstance(chunk_usage, dict)
                            else getattr(chunk_usage, key, 0)
                        )
                        usage[key] = max(usage[key], int(value or 0))
                if not hasattr(chunk, "choices") or not chunk.choices:
                    continue
                delta = chunk.choices[0].delta

                if hasattr(delta, "content") and delta.content:
                    full_content += delta.content
                    yield self._create_and_emit(ModelStreamChunk(text=delta.content))

                if hasattr(delta, "tool_calls") and delta.tool_calls:
                    for tc in delta.tool_calls:
                        idx = tc.index
                        if idx not in tool_calls_accum:
                            tool_calls_accum[idx] = {
                                "id": tc.id or "",
                                "name": "",
                                "arguments": "",
                            }
                        if tc.id:
                            tool_calls_accum[idx]["id"] = tc.id
                        if hasattr(tc, "function") and tc.function:
                            if tc.function.name:
                                tool_calls_accum[idx]["name"] = tc.function.name
                            if tc.function.arguments:
                                tool_calls_accum[idx]["arguments"] += tc.function.arguments
        except (OSError, ValueError):
            # Re-raise as-is; these are caught by the kernel's outer try/except.
            raise
        except Exception as exc:
            # Transient mid-stream failure (e.g. connection reset, read timeout).
            # Wrap it so the kernel can distinguish it from a pre-stream error.
            _transient_signals = (
                "connection", "reset", "eof", "read timeout", "chunked",
                "incomplete", "remotedisconnected", "brokenassertion",
            )
            exc_lower = str(exc).lower()
            if any(s in exc_lower for s in _transient_signals):
                raise StreamInterruptedError(
                    f"Provider stream interrupted mid-delivery: {exc}"
                ) from exc
            raise

        tool_calls = []
        for idx in sorted(tool_calls_accum.keys()):
            tc = tool_calls_accum[idx]
            if tc["name"]:
                if not tc.get("id"):
                    tc["id"] = f"call_{idx}_{int(time.time() * 1000)}"
                tool_calls.append(tc)

        if not usage["total_tokens"]:
            usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        return full_content, tool_calls, usage, request_id


# ---------------------------------------------------------------------------
# TaskDagKernel — dependency-aware, resumable DAG executor
# ---------------------------------------------------------------------------


class TaskDagKernel:
    """Run a typed task DAG with checkpointed state and bounded repairs.

    This is the DAG execution engine.  It is a separate class from
    :class:`ExecutionKernel` (the interactive engine) to avoid the Python
    single-class / two-``__init__`` pitfall that previously broke the build.
    """

    def __init__(
        self,
        plan: ExecutionPlan,
        ledger: RunLedger,
        *,
        max_total_repairs: int | None = None,
    ):
        self.plan = plan
        self.ledger = ledger
        configured = int(plan.retry_policy.get("total_repairs", 5))
        self.max_total_repairs = max(
            0, configured if max_total_repairs is None else max_total_repairs
        )
        self.repairs = 0

    # Preserve the old public name for existing callers (e.g. agent.py two-node path)
    def run(
        self,
        execute: StepExecutor,
        *,
        verify: StepVerifier | None = None,
        repair: StepRepairer | None = None,
        reviewer: PlanReviewer | None = None,
    ) -> ExecutionResult:
        """Alias for :meth:`run_dag` — preferred name for new code."""
        return self.run_dag(execute, verify=verify, repair=repair, reviewer=reviewer)

    def run_dag(
        self,
        execute: StepExecutor,
        *,
        verify: StepVerifier | None = None,
        repair: StepRepairer | None = None,
        reviewer: PlanReviewer | None = None,
    ) -> ExecutionResult:
        """Execute all dependency-ready tasks until the DAG completes or blocks."""
        self._validate_graph()
        self.ledger.record_tasks(self.plan.steps)
        self.ledger.append_event(
            "dag_started",
            status="verified",
            detail=f"Starting dependency-aware execution of {len(self.plan.steps)} task(s).",
            metadata={"plan_id": self.plan.id},
        )

        while True:
            ready = self._ready_steps()
            if not ready:
                break
            for step in ready:
                self._run_step(step, execute, verify, repair)
                self.ledger.record_tasks(self.plan.steps)
                self.ledger.record_plan(self.plan)

        self._block_orphans()
        completed = [step.id for step in self.plan.steps if step.status == TaskStatus.COMPLETED]
        failed = [step.id for step in self.plan.steps if step.status == TaskStatus.FAILED]
        blocked = [step.id for step in self.plan.steps if step.status == TaskStatus.BLOCKED]

        review_result = None
        if (
            not failed
            and not blocked
            and all(
                step.status in (TaskStatus.COMPLETED, TaskStatus.SKIPPED)
                for step in self.plan.steps
            )
        ):
            if reviewer is not None:
                review_result = reviewer(self.plan)
                self.ledger.append_event(
                    "independent_review",
                    status="verified" if review_result.approved else "failed",
                    detail=review_result.summary,
                    metadata={
                        "findings": review_result.findings,
                        "evidence_ids": review_result.evidence_ids,
                    },
                )
                if not review_result.approved:
                    self.plan.status = TaskStatus.FAILED
                else:
                    self.plan.status = TaskStatus.COMPLETED
            else:
                self.plan.status = TaskStatus.COMPLETED
        elif failed:
            self.plan.status = TaskStatus.FAILED
        else:
            self.plan.status = TaskStatus.BLOCKED

        self.ledger.record_tasks(self.plan.steps)
        self.ledger.record_plan(self.plan)
        self.ledger.append_event(
            "dag_finished",
            status="verified" if self.plan.status == TaskStatus.COMPLETED else "failed",
            detail=f"DAG finished with status {self.plan.status.value}.",
            metadata={
                "completed": completed,
                "failed": failed,
                "blocked": blocked,
                "repairs": self.repairs,
            },
        )
        return ExecutionResult(
            status=self.plan.status,
            completed=completed,
            failed=failed,
            blocked=blocked,
            repairs=self.repairs,
            review=review_result,
        )

    def _run_step(
        self,
        step: PlanStep,
        execute: StepExecutor,
        verify: StepVerifier | None,
        repair: StepRepairer | None,
    ) -> None:
        step.status = TaskStatus.IN_PROGRESS
        step.started_at = datetime.now(timezone.utc).isoformat()
        step.attempts += 1
        self.ledger.append_event(
            "task_started",
            status="verified",
            detail=step.title,
            metadata={"task_id": step.id, "attempt": step.attempts},
        )
        outcome = execute(step)
        if outcome.success and verify is not None:
            outcome = verify(step, outcome)

        repair_number = 0
        while (
            not outcome.success
            and repair is not None
            and repair_number < step.retry_limit
            and self.repairs < self.max_total_repairs
        ):
            repair_number += 1
            self.repairs += 1
            failure_kind = classify_failure(outcome.output or outcome.summary)
            self.ledger.append_event(
                "repair_started",
                status="verified",
                detail=f"Focused {failure_kind.value} repair for task {step.id}.",
                metadata={
                    "task_id": step.id,
                    "repair": repair_number,
                    "failure_kind": failure_kind.value,
                    "failure": (outcome.output or outcome.summary)[:4000],
                },
            )
            outcome = repair(step, outcome, failure_kind, repair_number)
            step.attempts += 1
            if outcome.success and verify is not None:
                outcome = verify(step, outcome)

        step.result = outcome.summary
        step.error = "" if outcome.success else (outcome.output or outcome.summary)
        step.status = TaskStatus.COMPLETED if outcome.success else TaskStatus.FAILED
        step.completed_at = datetime.now(timezone.utc).isoformat()
        self.ledger.append_event(
            "task_finished",
            status="verified" if outcome.success else "failed",
            detail=outcome.summary,
            metadata={
                "task_id": step.id,
                "attempts": step.attempts,
                "evidence_ids": outcome.evidence_ids,
                "changed_files": outcome.changed_files,
                "verification_commands": outcome.verification_commands,
            },
        )
        if outcome.success:
            self.ledger.checkpoint(
                f"task-{step.id}-verified",
                plan=self.plan,
                metadata={
                    "task_id": step.id,
                    "evidence_ids": outcome.evidence_ids,
                    "changed_files": outcome.changed_files,
                },
            )

    def _ready_steps(self) -> list[PlanStep]:
        status_by_id = {step.id: step.status for step in self.plan.steps}
        return [
            step
            for step in self.plan.steps
            if step.status == TaskStatus.PENDING
            and all(
                status_by_id[dependency] in (TaskStatus.COMPLETED, TaskStatus.SKIPPED)
                for dependency in step.depends_on
            )
        ]

    def _block_orphans(self) -> None:
        status_by_id = {step.id: step.status for step in self.plan.steps}
        for step in self.plan.steps:
            if step.status != TaskStatus.PENDING:
                continue
            failed_dependencies = [
                dependency
                for dependency in step.depends_on
                if status_by_id.get(dependency) in (TaskStatus.FAILED, TaskStatus.BLOCKED)
            ]
            if failed_dependencies:
                step.status = TaskStatus.BLOCKED
                step.error = f"Blocked by failed dependencies: {failed_dependencies}"
                step.completed_at = datetime.now(timezone.utc).isoformat()

    def _validate_graph(self) -> None:
        ids = [step.id for step in self.plan.steps]
        if len(ids) != len(set(ids)):
            raise ValueError("Task DAG contains duplicate task ids")
        known = set(ids)
        for step in self.plan.steps:
            missing = set(step.depends_on) - known
            if missing:
                raise ValueError(f"Task {step.id} depends on unknown task ids: {sorted(missing)}")
            if step.id in step.depends_on:
                raise ValueError(f"Task {step.id} depends on itself")

        visiting: set[int] = set()
        visited: set[int] = set()
        graph = {step.id: step.depends_on for step in self.plan.steps}

        def visit(task_id: int) -> None:
            if task_id in visiting:
                raise ValueError("Task DAG contains a dependency cycle")
            if task_id in visited:
                return
            visiting.add(task_id)
            for dependency in graph[task_id]:
                visit(dependency)
            visiting.remove(task_id)
            visited.add(task_id)

        for task_id in ids:
            visit(task_id)


# ---------------------------------------------------------------------------
# Failure classifier (shared utility)
# ---------------------------------------------------------------------------

_FAILURE_PATTERNS: tuple[tuple[FailureKind, tuple[str, ...]], ...] = (
    (FailureKind.TIMEOUT, ("timed out", "timeout", "deadline exceeded")),
    (FailureKind.SYNTAX, ("syntaxerror", "parse error", "unexpected token", "syntax error")),
    (
        FailureKind.IMPORT,
        ("modulenotfound", "importerror", "cannot find module", "unresolved import"),
    ),
    (FailureKind.TYPE, ("typeerror", "type error", "mypy", "ts2322", "incompatible type")),
    (FailureKind.TEST, ("assertionerror", "failed test", "tests failed", "assert ")),
    (FailureKind.DEPENDENCY, ("dependency conflict", "no matching distribution", "eresolve")),
    (FailureKind.SECURITY, ("vulnerability", "secret detected", "security policy")),
    (FailureKind.ENVIRONMENT, ("command not found", "not installed", "connection refused")),
    (FailureKind.RUNTIME, ("traceback", "runtimeerror", "exception", "panic:")),
)


def classify_failure(output: Any) -> FailureKind:
    """Classify raw failure evidence without asking a model."""
    if not isinstance(output, str):
        output = str(output or "")
    normalized = re.sub(r"\s+", " ", output.lower())
    for kind, patterns in _FAILURE_PATTERNS:
        if any(pattern in normalized for pattern in patterns):
            return kind
    return FailureKind.UNKNOWN
