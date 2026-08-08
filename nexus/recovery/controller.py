"""
Recovery Controller for Nexus CLI Sprint 7.
It orchestrates failure normalisation, diagnosis, strategy selection, loop detection,
and budget enforcement.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Tuple, Optional

from nexus.recovery.records import FailureRecord, FailureDiagnosis, FailureKind
from nexus.recovery.normalizer import FailureNormalizer
from nexus.recovery.diagnosis import DiagnosisEngine
from nexus.recovery.strategies import StrategySignatureEngine, RecoveryStrategy
from nexus.recovery.signatures import LoopDetector
from nexus.recovery.intelligent import RecoveryAction, RecoveryStateMachine

logger = logging.getLogger(__name__)


class RecoveryController:
    """Core orchestration class for the recovery subsystem.

    Parameters
    ----------
    max_repairs: int
        Global cap on number of repair attempts for a run.
    max_loop_iterations: int
        Maximum allowed repeated strategy signatures before aborting.
    max_time_seconds: int
        Hard time budget for the entire recovery process.
    """

    def __init__(self,
                 max_repairs: int = 5,
                 max_loop_iterations: int = 10,
                 max_time_seconds: int = 300,
                 run_id: str | None = None,
                 working_dir: str = ".",
                 budget: Any = None,
                 **kwargs: Any):
        self.run_id = run_id
        self.working_dir = working_dir
        self.max_repairs = max_repairs
        self.max_loop_iterations = max_loop_iterations
        self.max_time_seconds = max_time_seconds
        self.budget = budget
        self.repairs_done = 0
        self.history_failures: list[dict] = []
        self.signature_engine = StrategySignatureEngine()
        self.loop_detector = LoopDetector(max_history=self.max_loop_iterations)
        self.normalizer = FailureNormalizer()
        self.diagnosis_engine = DiagnosisEngine()
        self._start_time = None
        self.intelligent_state = RecoveryStateMachine(max_attempts=max_repairs + 2)
        self.last_intelligent_decision = None
        self.last_evidence_context: dict[str, Any] = {}

    def _enrich_context_from_runtime_evidence(
        self, raw_failure: Any, context: dict[str, Any]
    ) -> dict[str, Any]:
        """Derive recovery state from raw output instead of caller-supplied flags.

        This closes a reliability gap where EXPAND_CONTEXT/REVISE_PLAN only fired
        when an integration manually labelled a failure. The canonical extractor
        now derives stack paths, symbols, test nodes, migration signals, and
        concurrency symptoms for every recovery entry point.
        """
        from nexus.intelligence.repository.evidence import (
            FailureEvidenceExtractor,
        )
        from nexus.intelligence.task_profiles import TaskProfiler

        enriched = dict(context)
        known_paths = enriched.get("repository_paths") or enriched.get("context_files") or ()
        signals = FailureEvidenceExtractor.extract(
            {"failure": raw_failure, "context": enriched},
            repository_paths=known_paths,
        )
        if signals.paths:
            enriched.setdefault("failing_stack_files", list(signals.paths))
        if signals.tests:
            enriched.setdefault("failing_tests", list(signals.tests))
        if signals.symbols:
            enriched.setdefault("unresolved_symbols", list(signals.symbols))
        if signals.modules:
            enriched.setdefault("missing_dependencies", list(signals.modules))
        if signals.concurrency_terms or "timeout_or_deadlock" in signals.failure_kinds:
            enriched["concurrency_failure"] = True
        if signals.migration_terms:
            enriched["migration_failure"] = True
        context_files = {str(item) for item in enriched.get("context_files", ())}
        if signals.paths and not set(signals.paths).issubset(context_files):
            enriched["missing_context"] = True

        objective = str(enriched.get("objective") or raw_failure or "")
        profile = TaskProfiler.refine(TaskProfiler.classify(objective), signals)
        enriched["task_profile"] = profile.to_dict()
        evidence_payload = {
            "paths": list(signals.paths),
            "symbols": list(signals.symbols),
            "tests": list(signals.tests),
            "modules": list(signals.modules),
            "failure_kinds": list(signals.failure_kinds),
            "concurrency_terms": list(signals.concurrency_terms),
            "migration_terms": list(signals.migration_terms),
            "uncertainty_score": signals.uncertainty_score,
        }
        enriched["strategy_evidence"] = evidence_payload
        enriched.setdefault(
            "context_revision",
            hashlib.sha256(
                json.dumps(evidence_payload, sort_keys=True).encode("utf-8")
            ).hexdigest(),
        )
        self.last_evidence_context = enriched
        return enriched

    def _ensure_timing_started(self):
        from datetime import datetime, timezone
        if self._start_time is None:
            self._start_time = datetime.now(timezone.utc)

    def _time_exceeded(self) -> bool:
        from datetime import datetime, timezone
        if self._start_time is None:
            return False
        elapsed = (datetime.now(timezone.utc) - self._start_time).total_seconds()
        return elapsed > self.max_time_seconds

    def diagnose_and_recover(self,
                             record: FailureRecord,
                             context: dict[str, Any]) -> Tuple[bool, Optional[RecoveryStrategy]]:
        """Run the full diagnosis & recovery pipeline.

        Returns
        -------
        (recovered: bool, strategy: RecoveryStrategy | None)
            ``recovered`` indicates whether a repair was applied.
            ``strategy`` is the selected strategy (if any).
        """
        self._ensure_timing_started()

        # Enforce global budgets early
        if self.repairs_done >= self.max_repairs:
            logger.warning("Recovery budget exhausted (repairs=%s)", self.repairs_done)
            return False, None
        if self._time_exceeded():
            logger.warning("Recovery time budget exceeded")
            return False, None

        context = self._enrich_context_from_runtime_evidence(record, context)

        # Normalise raw output into a canonical FailureRecord (no-op if already normalised)
        norm_record = self.normalizer.normalize(record)

        # Generate a deterministic signature for loop detection
        signature = self.signature_engine.generate_signature(norm_record)
        if hasattr(self.loop_detector, "is_repeat") and self.loop_detector.is_repeat(signature):
            logger.warning("Loop detected for signature %s", signature)
            return False, None
        if hasattr(self.loop_detector, "record"):
            self.loop_detector.record(signature)

        # Diagnose
        diagnosis: FailureDiagnosis = self.diagnosis_engine.diagnose(norm_record, context)

        # Choose a strategy
        strategy = self.signature_engine.select_strategy(diagnosis)
        if strategy is None:
            logger.info("No viable recovery strategy found for failure %s", getattr(norm_record, "failure_id", "raw"))
            return False, None

        # Strategy metadata never counts as a repair by itself.  The strategy must
        # have an explicit runtime handler and return an affirmative result.
        try:
            applied = bool(strategy.apply(norm_record, context))
        except Exception as exc:
            logger.exception("Strategy %s raised an exception: %s", getattr(strategy, "name", str(strategy)), exc)
            applied = False

        if applied:
            self.repairs_done += 1
            logger.info("Applied recovery strategy %s", getattr(strategy, "name", str(strategy)))
        else:
            logger.info("Strategy %s could not be applied", getattr(strategy, "name", str(strategy)))

        return applied, strategy

    def classify(self, raw_failure: Any) -> Any:
        """Forward failure classification to FailureNormalizer."""
        rec = self.normalizer.normalize(raw_failure)
        return getattr(rec, "kind", rec)

    def handle_failure(
        self,
        raw_failure: Any,
        source_component: str = "kernel",
        phase: str = "execution",
        plan_version: int = 1,
        model_id: str | None = None,
        **kwargs: Any,
    ) -> Tuple[Optional[RecoveryStrategy], Optional[FailureDiagnosis], str]:
        self._ensure_timing_started()
        norm_record = self.normalizer.normalize(raw_failure)
        context = dict(kwargs)
        context.update({"source_component": source_component, "phase": phase, "plan_version": plan_version})
        context = self._enrich_context_from_runtime_evidence(raw_failure, context)
        if model_id:
            context["model_id"] = model_id
        
        # Track history for escalation and budget
        self.history_failures.append({"failure": norm_record, "model_id": model_id, "source": source_component})

        diagnosis = self.diagnosis_engine.diagnose(norm_record, context)
        intelligent = self.intelligent_state.decide(norm_record, context)
        self.last_intelligent_decision = intelligent
        
        # Check repeated model failures
        model_fails = [f for f in self.history_failures if f.get("model_id") == model_id and model_id]
        if len(model_fails) >= 2 or getattr(diagnosis.primary_failure, "kind", None) == FailureKind.INVALID_STRUCTURED_OUTPUT:
            diagnosis.model_escalation_recommended = True

        strategy = self.signature_engine.get(intelligent.action.value)
        if strategy is None:
            strategy = self.signature_engine.select_strategy(diagnosis)
        if diagnosis.model_escalation_recommended and intelligent.action not in {RecoveryAction.STOP_BLOCKED, RecoveryAction.STOP_FAILED, RecoveryAction.ROLLBACK}:
            strategy = self.signature_engine.get("SWITCH_MODEL") or strategy

        terminal = "CONTINUE" if strategy and not intelligent.terminal else "TERMINAL_FAILURE"
        strategy_type = getattr(strategy, "strategy_type", None) if strategy else None
        strategy_value = getattr(strategy_type, "value", strategy_type)
        if strategy_value == "STOP_BLOCKED":
            from nexus.recovery.terminal import TerminalState
            terminal = TerminalState.BLOCKED.value
        elif strategy_value == "STOP_FAILED":
            from nexus.recovery.terminal import TerminalState
            terminal = TerminalState.FAILED.value
        
        # Check budget limits
        if self.budget:
            max_retries = getattr(self.budget, "max_command_retries", None) or getattr(self.budget, "max_repairs", None)
            if max_retries is not None and len(self.history_failures) > max_retries:
                from nexus.recovery.terminal import TerminalState
                terminal = TerminalState.BUDGET_EXHAUSTED.value

        wdir = getattr(self, "working_dir", ".") or "."
        rid = getattr(self, "run_id", None) or "test-run-1"
        import os
        path = os.path.join(wdir, ".nexus", "runs", rid, "failures")
        os.makedirs(path, exist_ok=True)
        try:
            with open(os.path.join(path, f"failure-{len(self.history_failures):03d}.json"), "w") as f:
                json.dump({"failure": str(raw_failure), "phase": phase}, f)
        except Exception:
            pass

        return strategy, diagnosis, terminal
