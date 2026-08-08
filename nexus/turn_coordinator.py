"""
TurnCoordinator — extracted service for single-model-turn orchestration.

This module formalises the boundary between the Agent's outer agentic loop
and one synchronous model turn.  It owns:

  * message-list assembly
  * model call dispatch and streaming
  * tool-call parsing and validation
  * result collection and return

Architecture::

    TurnCoordinator
    ├── prepare_messages()     build the message list for this turn
    ├── call_model()           dispatch to the provider client
    ├── parse_tool_calls()     extract and validate tool-call JSON
    ├── collect_results()      run each tool via ToolExecutionController
    └── summarise_turn()       return (content, events, tool_results)

The coordinator does NOT own:
  * the outer retry/repair loop  (Agent._run_managed)
  * evidence persistence         (Agent._record_*)
  * run-state checkpointing      (RunLedger)

This keeps the turn boundary clean and makes the coordinator independently
testable with a mock client.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nexus.agent import Agent

logger = logging.getLogger(__name__)


@dataclass
class TurnResult:
    """Output of one complete model turn."""

    content: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    duration_ms: int = 0
    success: bool = True
    error: str = ""

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class TurnRequest:
    """Input specification for one model turn."""

    messages: list[dict[str, Any]]
    system_prompt: str = ""
    tool_definitions: list[dict[str, Any]] = field(default_factory=list)
    max_tokens: int = 4096
    stream: bool = True
    emit_ui: bool = True
    turn_index: int = 0


class TurnCoordinator:
    """
    Service that orchestrates one synchronous model turn.

    This is a *delegation target* for ``Agent._run_model_turn``.
    It surfaces turn coordination as an independently callable unit
    so that the turn can be tested, mocked, or swapped without touching
    the agent loop.

    Usage::

        coordinator = TurnCoordinator(agent)
        result = coordinator.run_turn(request)
    """

    def __init__(self, agent: "Agent") -> None:
        self._agent = agent

    # ──────────────────────────────────────────────────────────────────────────
    # Public interface
    # ──────────────────────────────────────────────────────────────────────────

    def run_turn(self, request: TurnRequest) -> TurnResult:
        """
        Execute one complete model turn and return a structured TurnResult.

        Delegates to the agent's existing implementation for now; this
        wrapper establishes the decomposition boundary so callers can be
        migrated one by one.
        """
        start = time.monotonic()
        try:
            # The agent's inner turn runner returns (content, events).
            content, events = self._agent._run_single_turn(
                request.messages,
                emit_ui=request.emit_ui,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
            tool_calls = [e for e in events if e.get("type") == "tool_call"]
            return TurnResult(
                content=content,
                tool_calls=tool_calls,
                tool_results=[e for e in events if e.get("type") == "tool_result"],
                events=events,
                duration_ms=duration_ms,
                success=True,
            )
        except (LookupError, OSError, RuntimeError, TypeError, ValueError) as exc:# pragma: no cover
            duration_ms = int((time.monotonic() - start) * 1000)
            logger.exception("TurnCoordinator.run_turn failed: %s", exc)
            return TurnResult(
                content="",
                events=[],
                duration_ms=duration_ms,
                success=False,
                error=str(exc),
            )

    # ──────────────────────────────────────────────────────────────────────────
    # Introspection / factory helpers
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def parse_tool_call_arguments(raw: str | None) -> dict[str, Any]:
        """
        Parse tool-call argument JSON safely.

        Returns an empty dict if *raw* is None, empty, or invalid JSON.
        This is a pure function — no side effects.
        """
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (json.JSONDecodeError, ValueError):
            logger.debug("Failed to parse tool-call arguments: %r", raw[:200])
            return {}

    @staticmethod
    def validate_tool_call(call: dict[str, Any]) -> tuple[bool, str]:
        """
        Validate a tool-call dict has required shape.

        Returns ``(is_valid, error_reason)``.  Pure function.
        """
        if not isinstance(call, dict):
            return False, "tool call is not a dict"
        name = call.get("name") or call.get("function", {}).get("name", "")
        if not name or not isinstance(name, str):
            return False, "tool call has no name"
        call_id = call.get("id", "")
        if not call_id or not isinstance(call_id, str):
            return False, "tool call has no id"
        return True, ""

    def describe(self) -> dict[str, Any]:
        """Return a machine-readable description of the coordinator."""
        return {
            "service": "TurnCoordinator",
            "agent_model": getattr(self._agent, "model_key", "unknown"),
            "working_dir": getattr(self._agent, "working_dir", ""),
        }


# ─── Convenience factory ─────────────────────────────────────────────────────


def make_coordinator(agent: "Agent") -> TurnCoordinator:
    """Create and attach a ``TurnCoordinator`` to *agent*."""
    coordinator = TurnCoordinator(agent)
    agent._turn_coordinator = coordinator  # type: ignore[attr-defined]
    return coordinator
