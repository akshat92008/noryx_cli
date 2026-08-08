"""Canonical controller-driven task session.

This API is intentionally fail-closed: a session cannot become VERIFIED unless
an injected execution controller produces a result and an independent
verification controller explicitly approves that result.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict

from nexus.config.core import get_config
from nexus.events import EventBus, EventType
from nexus.run_state import RunStatus

logger = logging.getLogger(__name__)


class SessionConfigurationError(RuntimeError):
    """Raised when a concrete runtime controller is missing."""


class AgentSession:
    """Coordinate one task through context, plan, execute, verify, and finalize."""

    def __init__(
        self,
        task: str,
        planner: Any = None,
        context_selector: Any = None,
        execution_controller: Any = None,
        mutation_controller: Any = None,
        verification_controller: Any = None,
        recovery_controller: Any = None,
        evidence_collector: Any = None,
        finalizer: Any = None,
    ):
        self.session_id = str(uuid.uuid4())
        self.task = task
        self.status = RunStatus.RUNNING
        self.config = get_config()
        self.planner = planner
        self.context_selector = context_selector
        self.execution_controller = execution_controller
        self.mutation_controller = mutation_controller
        self.verification_controller = verification_controller
        self.recovery_controller = recovery_controller
        self.evidence_collector = evidence_collector
        self.finalizer = finalizer

    @staticmethod
    def _invoke(target: Any, names: tuple[str, ...], *args: Any) -> Any:
        for name in names:
            method = getattr(target, name, None)
            if callable(method):
                return method(*args)
        raise SessionConfigurationError(
            f"{type(target).__name__} does not implement any of: {', '.join(names)}"
        )

    @staticmethod
    def _verification_passed(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, dict):
            if "passed" in value:
                return bool(value["passed"])
            return str(value.get("status", "")).upper() in {"PASS", "PASSED", "VERIFIED"}
        return str(getattr(value, "status", "")).upper() in {"PASS", "PASSED", "VERIFIED"}

    def start(self) -> Dict[str, Any]:
        self.status = RunStatus.RUNNING
        EventBus.publish(EventType.TASK_STARTED, self.session_id, "AgentSession", {"task": self.task})
        try:
            if self.execution_controller is None:
                raise SessionConfigurationError("execution_controller is required")
            if self.verification_controller is None:
                raise SessionConfigurationError("verification_controller is required")

            context = (
                self._invoke(self.context_selector, ("gather_context", "select"), self.task)
                if self.context_selector is not None
                else {}
            )
            plan = (
                self._invoke(self.planner, ("create_plan", "plan"), self.task, context)
                if self.planner is not None
                else {"objective": self.task, "steps": []}
            )
            EventBus.publish(EventType.PLAN_CREATED, self.session_id, "AgentSession", {"plan": plan})

            result = self._invoke(
                self.execution_controller,
                ("execute_plan", "execute", "run"),
                plan,
                context,
            )
            verification = self._invoke(
                self.verification_controller,
                ("verify_result", "verify", "run"),
                result,
                plan,
                context,
            )
            if not self._verification_passed(verification):
                self.status = RunStatus.FAILED
                payload = {
                    "status": "FAILED",
                    "result": result,
                    "verification": verification,
                    "error": "independent verification did not pass",
                }
                EventBus.publish(EventType.TASK_FAILED, self.session_id, "AgentSession", payload)
                return payload

            payload = {"status": "VERIFIED", "result": result, "verification": verification}
            self.status = RunStatus.VERIFIED
            EventBus.publish(EventType.TASK_COMPLETED, self.session_id, "AgentSession", payload)
            if self.finalizer is not None:
                return self._invoke(self.finalizer, ("finalize", "finish"), self.session_id, payload)
            return payload
        except SessionConfigurationError as exc:
            self.status = RunStatus.BLOCKED
            payload = {"status": "BLOCKED", "error": str(exc)}
            EventBus.publish(EventType.TASK_FAILED, self.session_id, "AgentSession", payload)
            return payload
        except Exception as exc:  # noqa: BLE001 - normalized into durable failure output.
            self.status = RunStatus.FAILED
            logger.exception("Session failed")
            EventBus.publish(EventType.TASK_FAILED, self.session_id, "AgentSession", {"error": str(exc)})
            if self.recovery_controller is not None:
                recovery = getattr(self.recovery_controller, "attempt_recovery", None)
                if callable(recovery):
                    return recovery(self.session_id, exc)
            return {"status": "FAILED", "error": str(exc)}
