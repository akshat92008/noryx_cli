"""Evidence-delta recovery governance.

Retries are only useful when the execution state changes.  This state machine
makes repeated failures consume an escalation ladder and eventually stop,
instead of allowing a model to loop on cosmetically different attempts.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Mapping


class RecoveryAction(str, Enum):
    RETRY_SMALLER_PATCH = "APPLY_SMALLER_PATCH"
    EXPAND_CONTEXT = "EXPAND_CONTEXT"
    REVISE_PLAN = "REVISE_PLAN"
    SWITCH_MODEL = "SWITCH_MODEL"
    ROLLBACK = "ROLLBACK_TO_CHECKPOINT"
    STOP_BLOCKED = "STOP_BLOCKED"
    STOP_FAILED = "STOP_FAILED"


@dataclass(frozen=True)
class RecoveryDecision:
    action: RecoveryAction
    reason: str
    failure_fingerprint: str
    evidence_fingerprint: str
    repeat_count: int
    evidence_changed: bool
    terminal: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["action"] = self.action.value
        return payload


class RecoveryStateMachine:
    """Select bounded recovery actions from failure and evidence deltas."""

    def __init__(self, *, max_attempts: int = 6, max_stagnant_repeats: int = 4) -> None:
        self.max_attempts = max(1, int(max_attempts))
        self.max_stagnant_repeats = max(1, int(max_stagnant_repeats))
        self._attempts = 0
        self._last_evidence_by_failure: dict[str, str] = {}
        self._stagnant_counts: dict[str, int] = {}
        self.history: list[RecoveryDecision] = []

    @staticmethod
    def _value(value: Any) -> Any:
        if hasattr(value, "value"):
            return value.value
        return value

    @classmethod
    def failure_fingerprint(cls, record: Any) -> str:
        if hasattr(record, "to_dict"):
            raw = record.to_dict()
        elif isinstance(record, Mapping):
            raw = dict(record)
        else:
            raw = {"summary": str(record)}
        stable = {
            "category": cls._value(raw.get("category")),
            "kind": cls._value(raw.get("kind")),
            "source_component": raw.get("source_component") or raw.get("tool"),
            "phase": raw.get("phase"),
            "summary": cls._normalize_text(raw.get("summary") or raw.get("error") or raw.get("raw_output")),
            "file_paths": sorted(map(str, raw.get("file_paths") or raw.get("paths") or [])),
            "failing_tests": sorted(map(str, raw.get("failing_tests") or [])),
            "command": cls._normalize_text(raw.get("command")),
            "exit_code": raw.get("exit_code"),
        }
        return hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()

    @classmethod
    def evidence_fingerprint(cls, record: Any, context: Mapping[str, Any]) -> str:
        repository_state = getattr(record, "repository_state", "")
        plan_version = context.get("plan_version", getattr(record, "plan_version", 1))
        material = {
            "repository_state": repository_state or context.get("repository_state", ""),
            "plan_version": plan_version,
            "context_revision": context.get("context_revision", ""),
            "context_files": sorted(map(str, context.get("context_files", []) or [])),
            "verification_evidence": context.get("verification_evidence", ""),
            "model_id": context.get("model_id", ""),
            "strategy_evidence": context.get("strategy_evidence", ""),
        }
        return hashlib.sha256(json.dumps(material, sort_keys=True, default=str).encode()).hexdigest()

    @staticmethod
    def _normalize_text(value: Any) -> str:
        text = re.sub(r"\b(?:failure|attempt|run)[-_ ]?[0-9a-f]{4,}\b", "<id>", str(value or ""), flags=re.I)
        text = re.sub(r"\b\d+(?:\.\d+)?\s*(?:ms|seconds?|s)\b", "<time>", text, flags=re.I)
        return " ".join(text.lower().split())[:2000]

    def decide(self, record: Any, context: Mapping[str, Any]) -> RecoveryDecision:
        self._attempts += 1
        failure = self.failure_fingerprint(record)
        evidence = self.evidence_fingerprint(record, context)
        previous = self._last_evidence_by_failure.get(failure)
        changed = previous is None or previous != evidence
        self._last_evidence_by_failure[failure] = evidence
        stagnant = 0 if changed else self._stagnant_counts.get(failure, 0) + 1
        self._stagnant_counts[failure] = stagnant

        blocked = bool(context.get("policy_blocked") or context.get("permission_required"))
        missing_context = bool(
            context.get("missing_context")
            or context.get("unresolved_symbols")
            or context.get("failing_stack_files")
            or context.get("missing_dependencies")
        )
        hypothesis_invalid = bool(
            context.get("hypothesis_contradicted")
            or context.get("root_cause_invalidated")
            or context.get("plan_assumption_failed")
        )
        systemic_failure = bool(
            context.get("concurrency_failure")
            or context.get("migration_failure")
            or context.get("repository_wide_contract_failure")
        )
        partial_patch = bool(context.get("partial_patch") or context.get("patch_too_large"))
        capability_mismatch = bool(context.get("model_capability_mismatch"))

        if blocked:
            action, reason, terminal = RecoveryAction.STOP_BLOCKED, "Policy or permission boundary blocks recovery.", True
        elif self._attempts > self.max_attempts:
            action, reason, terminal = RecoveryAction.STOP_FAILED, "Global recovery budget exhausted.", True
        elif stagnant > self.max_stagnant_repeats:
            action, reason, terminal = RecoveryAction.STOP_FAILED, "Stagnation budget exhausted after rollback; stop the loop.", True
        elif changed and hypothesis_invalid:
            action, reason, terminal = RecoveryAction.REVISE_PLAN, "New evidence contradicts the active root-cause hypothesis.", False
        elif changed and (missing_context or systemic_failure):
            action = RecoveryAction.EXPAND_CONTEXT if missing_context else RecoveryAction.REVISE_PLAN
            reason = (
                "Runtime evidence identifies missing repository context."
                if missing_context
                else "Systemic migration/concurrency evidence requires a structurally different plan."
            )
            terminal = False
        elif changed and capability_mismatch:
            action, reason, terminal = RecoveryAction.SWITCH_MODEL, "The failure is classified as a model capability mismatch.", False
        elif changed and partial_patch:
            action, reason, terminal = RecoveryAction.RETRY_SMALLER_PATCH, "New evidence supports a smaller, isolated corrective patch.", False
        elif changed:
            action, reason, terminal = RecoveryAction.RETRY_SMALLER_PATCH, "New evidence permits one bounded corrective attempt.", False
        elif stagnant == 1:
            action, reason, terminal = RecoveryAction.EXPAND_CONTEXT, "Failure repeated without evidence delta; expand from stack, symbols, callers and tests.", False
        elif stagnant == 2:
            action, reason, terminal = RecoveryAction.REVISE_PLAN, "Expanded evidence did not change the failure; invalidate the current causal plan.", False
        elif stagnant == 3:
            action, reason, terminal = RecoveryAction.SWITCH_MODEL, "The revised plan produced no evidence delta; escalate model capability.", False
        elif stagnant == 4:
            action, reason, terminal = RecoveryAction.ROLLBACK, "Repeated failure remains unchanged; rollback before further work.", False
        else:
            action, reason, terminal = RecoveryAction.STOP_FAILED, "Recovery stopped because no new evidence or state transition occurred.", True

        decision = RecoveryDecision(action, reason, failure, evidence, stagnant, changed, terminal)
        self.history.append(decision)
        return decision

    def reset(self) -> None:
        self._attempts = 0
        self._last_evidence_by_failure.clear()
        self._stagnant_counts.clear()
        self.history.clear()
