"""
Canonical Recovery Strategy Definitions for Noryx CLI.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class RecoveryStrategyType(str, Enum):
    RETRY_TRANSIENT = "RETRY_TRANSIENT"
    RETRY_WITH_CORRECTED_ARGUMENTS = "RETRY_WITH_CORRECTED_ARGUMENTS"
    REQUEST_MISSING_PERMISSION = "REQUEST_MISSING_PERMISSION"
    REQUEST_CLARIFICATION = "REQUEST_CLARIFICATION"
    EXPAND_CONTEXT = "EXPAND_CONTEXT"
    REVISE_PLAN = "REVISE_PLAN"
    REVERT_LAST_MUTATION = "REVERT_LAST_MUTATION"
    ROLLBACK_TO_CHECKPOINT = "ROLLBACK_TO_CHECKPOINT"
    APPLY_SMALLER_PATCH = "APPLY_SMALLER_PATCH"
    CHANGE_TOOL = "CHANGE_TOOL"
    CHANGE_VALIDATION_COMMAND = "CHANGE_VALIDATION_COMMAND"
    INSTALL_OR_CONFIGURE_DEPENDENCY = "INSTALL_OR_CONFIGURE_DEPENDENCY"
    REPRODUCE_FAILURE_DIFFERENTLY = "REPRODUCE_FAILURE_DIFFERENTLY"
    SWITCH_MODEL = "SWITCH_MODEL"
    REDUCE_SCOPE = "REDUCE_SCOPE"
    SPLIT_TASK = "SPLIT_TASK"
    STOP_BLOCKED = "STOP_BLOCKED"
    STOP_FAILED = "STOP_FAILED"


@dataclass
class RecoveryStrategy:
    strategy_type: RecoveryStrategyType
    description: str
    prerequisites: list[str] = field(default_factory=list)
    expected_state_change: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    cost_impact: str = "low"
    risk: str = "low"
    rollback_required: bool = False
    verification_required: bool = True
    max_repetitions: int = 2
    metadata: dict[str, Any] = field(default_factory=dict)

    _HANDLER_ALIASES = {
        RecoveryStrategyType.RETRY_TRANSIENT: ("retry_transient", "retry"),
        RecoveryStrategyType.RETRY_WITH_CORRECTED_ARGUMENTS: (
            "retry_with_corrected_arguments",
            "retry",
        ),
        RecoveryStrategyType.REQUEST_MISSING_PERMISSION: (
            "request_missing_permission",
            "request_permission",
        ),
        RecoveryStrategyType.REQUEST_CLARIFICATION: (
            "request_clarification",
            "clarify",
        ),
        RecoveryStrategyType.EXPAND_CONTEXT: ("expand_context",),
        RecoveryStrategyType.REVISE_PLAN: ("revise_plan", "replan"),
        RecoveryStrategyType.REVERT_LAST_MUTATION: (
            "revert_last_mutation",
            "rollback_last_mutation",
        ),
        RecoveryStrategyType.ROLLBACK_TO_CHECKPOINT: (
            "rollback_to_checkpoint",
            "rollback",
        ),
        RecoveryStrategyType.APPLY_SMALLER_PATCH: ("apply_smaller_patch",),
        RecoveryStrategyType.CHANGE_TOOL: ("change_tool",),
        RecoveryStrategyType.CHANGE_VALIDATION_COMMAND: ("change_validation_command",),
        RecoveryStrategyType.INSTALL_OR_CONFIGURE_DEPENDENCY: (
            "install_or_configure_dependency",
            "configure_dependency",
        ),
        RecoveryStrategyType.REPRODUCE_FAILURE_DIFFERENTLY: ("reproduce_failure_differently",),
        RecoveryStrategyType.SWITCH_MODEL: ("switch_model", "escalate_model"),
        RecoveryStrategyType.REDUCE_SCOPE: ("reduce_scope",),
        RecoveryStrategyType.SPLIT_TASK: ("split_task",),
    }

    @staticmethod
    def _coerce_result(result: Any) -> bool:
        """Convert a handler result into a strict applied/not-applied decision."""
        if isinstance(result, bool):
            return result
        if result is None:
            return False
        if isinstance(result, Mapping):
            for key in ("applied", "success", "ok"):
                if key in result:
                    return bool(result[key])
            return False
        for attribute in ("applied", "success", "ok"):
            if hasattr(result, attribute):
                return bool(getattr(result, attribute))
        return False

    @staticmethod
    def _call_handler(
        handler: Callable[..., Any],
        *,
        strategy: "RecoveryStrategy",
        record: Any,
        context: dict[str, Any],
    ) -> Any:
        """Invoke common handler signatures without treating TypeError as success."""
        import inspect

        signature = inspect.signature(handler)
        parameters = signature.parameters
        accepts_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
        )
        keyword_arguments = {
            "strategy": strategy,
            "record": record,
            "context": context,
        }
        if accepts_kwargs:
            return handler(**keyword_arguments)
        supported = {name: value for name, value in keyword_arguments.items() if name in parameters}
        if supported:
            return handler(**supported)
        positional = [
            parameter
            for parameter in parameters.values()
            if parameter.kind
            in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        ]
        if len(positional) >= 2:
            return handler(record, context)
        if len(positional) == 1:
            return handler(record)
        return handler()

    def apply(self, record: Any, context: dict[str, Any] | None = None) -> bool:
        """Apply this strategy through an explicitly supplied runtime handler.

        Strategy definitions are policy metadata, not repair implementations.  A
        recovery attempt is therefore successful only when the runtime provides a
        concrete handler and that handler returns an affirmative, inspectable
        result.  This prevents false recovery evidence.
        """
        if self.strategy_type in {
            RecoveryStrategyType.STOP_BLOCKED,
            RecoveryStrategyType.STOP_FAILED,
        }:
            return False

        ctx = context or {}
        handler: Callable[..., Any] | None = None
        handlers = ctx.get("strategy_handlers") or ctx.get("recovery_handlers")
        if isinstance(handlers, Mapping):
            candidate_keys: tuple[Any, ...] = (
                self.strategy_type,
                self.strategy_type.value,
                self.strategy_type.value.lower(),
                *self._HANDLER_ALIASES.get(self.strategy_type, ()),
            )
            for key in candidate_keys:
                candidate = handlers.get(key)
                if callable(candidate):
                    handler = candidate
                    break

        if handler is None:
            for key in self._HANDLER_ALIASES.get(self.strategy_type, ()):
                candidate = ctx.get(key)
                if callable(candidate):
                    handler = candidate
                    break

        if handler is None:
            generic = ctx.get("recovery_executor") or ctx.get("apply_strategy")
            if callable(generic):
                handler = generic

        if handler is None:
            return False

        result = self._call_handler(
            handler,
            strategy=self,
            record=record,
            context=ctx,
        )
        return self._coerce_result(result)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_type": self.strategy_type.value,
            "description": self.description,
            "prerequisites": self.prerequisites,
            "expected_state_change": self.expected_state_change,
            "allowed_tools": self.allowed_tools,
            "cost_impact": self.cost_impact,
            "risk": self.risk,
            "rollback_required": self.rollback_required,
            "verification_required": self.verification_required,
            "max_repetitions": self.max_repetitions,
            "metadata": self.metadata,
        }


class StrategyRegistry:
    """Catalog of canonical recovery strategies."""

    _STRATEGIES: dict[RecoveryStrategyType, RecoveryStrategy] = {
        RecoveryStrategyType.RETRY_TRANSIENT: RecoveryStrategy(
            strategy_type=RecoveryStrategyType.RETRY_TRANSIENT,
            description="Retry transient execution error once with minor delay.",
            cost_impact="low",
            risk="low",
            max_repetitions=1,
        ),
        RecoveryStrategyType.RETRY_WITH_CORRECTED_ARGUMENTS: RecoveryStrategy(
            strategy_type=RecoveryStrategyType.RETRY_WITH_CORRECTED_ARGUMENTS,
            description="Re-execute tool with corrected parameters.",
            cost_impact="low",
            risk="low",
            max_repetitions=2,
        ),
        RecoveryStrategyType.REQUEST_MISSING_PERMISSION: RecoveryStrategy(
            strategy_type=RecoveryStrategyType.REQUEST_MISSING_PERMISSION,
            description="Request user or policy approval for missing permission.",
            cost_impact="zero",
            risk="low",
            verification_required=False,
            max_repetitions=1,
        ),
        RecoveryStrategyType.REQUEST_CLARIFICATION: RecoveryStrategy(
            strategy_type=RecoveryStrategyType.REQUEST_CLARIFICATION,
            description="Prompt user for clarifying details on ambiguous requirement.",
            cost_impact="zero",
            risk="low",
            verification_required=False,
            max_repetitions=1,
        ),
        RecoveryStrategyType.EXPAND_CONTEXT: RecoveryStrategy(
            strategy_type=RecoveryStrategyType.EXPAND_CONTEXT,
            description="Expand task repository context bundle with missing callers or definitions.",
            cost_impact="medium",
            risk="low",
            max_repetitions=2,
        ),
        RecoveryStrategyType.REVISE_PLAN: RecoveryStrategy(
            strategy_type=RecoveryStrategyType.REVISE_PLAN,
            description="Trigger versioned plan revision based on updated root cause analysis.",
            cost_impact="medium",
            risk="medium",
            max_repetitions=2,
        ),
        RecoveryStrategyType.REVERT_LAST_MUTATION: RecoveryStrategy(
            strategy_type=RecoveryStrategyType.REVERT_LAST_MUTATION,
            description="Revert the most recent invalid file mutation.",
            cost_impact="low",
            risk="medium",
            rollback_required=True,
            max_repetitions=2,
        ),
        RecoveryStrategyType.ROLLBACK_TO_CHECKPOINT: RecoveryStrategy(
            strategy_type=RecoveryStrategyType.ROLLBACK_TO_CHECKPOINT,
            description="Rollback entire repository workspace to last verified task checkpoint.",
            cost_impact="low",
            risk="high",
            rollback_required=True,
            max_repetitions=2,
        ),
        RecoveryStrategyType.APPLY_SMALLER_PATCH: RecoveryStrategy(
            strategy_type=RecoveryStrategyType.APPLY_SMALLER_PATCH,
            description="Apply a minimal, highly targeted patch to fix specific assertion.",
            cost_impact="low",
            risk="low",
            max_repetitions=2,
        ),
        RecoveryStrategyType.CHANGE_TOOL: RecoveryStrategy(
            strategy_type=RecoveryStrategyType.CHANGE_TOOL,
            description="Switch to an alternative equivalent tool implementation.",
            cost_impact="low",
            risk="low",
            max_repetitions=2,
        ),
        RecoveryStrategyType.CHANGE_VALIDATION_COMMAND: RecoveryStrategy(
            strategy_type=RecoveryStrategyType.CHANGE_VALIDATION_COMMAND,
            description="Use a targeted single-test command instead of full test suite.",
            cost_impact="low",
            risk="low",
            max_repetitions=2,
        ),
        RecoveryStrategyType.INSTALL_OR_CONFIGURE_DEPENDENCY: RecoveryStrategy(
            strategy_type=RecoveryStrategyType.INSTALL_OR_CONFIGURE_DEPENDENCY,
            description="Install or configure missing environment dependency.",
            cost_impact="medium",
            risk="medium",
            max_repetitions=1,
        ),
        RecoveryStrategyType.REPRODUCE_FAILURE_DIFFERENTLY: RecoveryStrategy(
            strategy_type=RecoveryStrategyType.REPRODUCE_FAILURE_DIFFERENTLY,
            description="Run reproduction command with extra verbosity or single worker.",
            cost_impact="low",
            risk="low",
            max_repetitions=2,
        ),
        RecoveryStrategyType.SWITCH_MODEL: RecoveryStrategy(
            strategy_type=RecoveryStrategyType.SWITCH_MODEL,
            description="Recommend model escalation for higher reasoning capability.",
            cost_impact="high",
            risk="low",
            max_repetitions=1,
        ),
        RecoveryStrategyType.REDUCE_SCOPE: RecoveryStrategy(
            strategy_type=RecoveryStrategyType.REDUCE_SCOPE,
            description="Reduce execution scope to exclude inherited broken tests.",
            cost_impact="low",
            risk="medium",
            max_repetitions=1,
        ),
        RecoveryStrategyType.SPLIT_TASK: RecoveryStrategy(
            strategy_type=RecoveryStrategyType.SPLIT_TASK,
            description="Decompose failing step into smaller sub-tasks.",
            cost_impact="medium",
            risk="low",
            max_repetitions=1,
        ),
        RecoveryStrategyType.STOP_BLOCKED: RecoveryStrategy(
            strategy_type=RecoveryStrategyType.STOP_BLOCKED,
            description="Stop execution honestly with BLOCKED terminal state.",
            cost_impact="zero",
            risk="zero",
            verification_required=False,
            max_repetitions=0,
        ),
        RecoveryStrategyType.STOP_FAILED: RecoveryStrategy(
            strategy_type=RecoveryStrategyType.STOP_FAILED,
            description="Stop execution honestly with FAILED terminal state.",
            cost_impact="zero",
            risk="zero",
            verification_required=False,
            max_repetitions=0,
        ),
    }

    @classmethod
    def get(cls, strategy_type: RecoveryStrategyType | str) -> RecoveryStrategy:
        st = (
            RecoveryStrategyType(strategy_type) if isinstance(strategy_type, str) else strategy_type
        )
        return cls._STRATEGIES.get(
            st,
            RecoveryStrategy(
                strategy_type=RecoveryStrategyType.STOP_FAILED,
                description="Default stop failed.",
            ),
        )

    @classmethod
    def select_strategy(cls, diagnosis: Any) -> RecoveryStrategy | None:
        prim = getattr(diagnosis, "primary_failure", None)
        if prim:
            kind = getattr(prim, "kind", None)
            if hasattr(kind, "value"):
                kind_str = str(kind.value).lower()
            else:
                kind_str = str(kind or "").lower()
            if kind_str in ("executable_not_found", "policy_blocked", "permission_denied"):
                return cls.get(RecoveryStrategyType.STOP_BLOCKED)
            if kind_str in ("dependency_missing", "dependency", "environment"):
                return cls.get(RecoveryStrategyType.INSTALL_OR_CONFIGURE_DEPENDENCY)
        rec = getattr(diagnosis, "recommended_strategy", None)
        if rec:
            return cls.get(rec)
        hyps = getattr(diagnosis, "hypotheses", [])
        if hyps:
            for h in hyps:
                strat = getattr(h, "recommended_strategy", None)
                if strat:
                    return cls.get(strat)
        return cls.get(RecoveryStrategyType.REVISE_PLAN)

    @classmethod
    def generate_signature(cls, norm_record: Any) -> str:
        fid = getattr(norm_record, "failure_id", "unknown")
        cat = getattr(norm_record, "category", "unknown")
        kind = getattr(norm_record, "kind", "unknown")
        return f"sig-{cat}-{kind}-{fid}"


# Alias for backward-compat import in recovery.controller
StrategySignatureEngine = StrategyRegistry
