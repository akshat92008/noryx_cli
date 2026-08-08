"""
Canonical Execution Pipeline for Nexus CLI.

Defines the single authoritative execution flow consumed by all modes:
    UserPrompt → RepoUnderstanding → Planning → ContextSelection →
    ModelRouting → Execution → Verification → RepairLoop → IndependentReview → Evidence → Completion

Every mode (interactive, non-interactive, Nova, two-node, CI) calls this same
pipeline, eliminating duplicated business logic.

Usage::

    from nexus.pipeline import ExecutionPipeline, PipelineStage, PipelineResult
    pipeline = ExecutionPipeline(agent)
    result = pipeline.run(user_input)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nexus.planner import TaskStatus, TaskType, get_task_type
from nexus.recovery.controller import RecoveryController
from nexus.recovery.records import FailureRecord
from nexus.run_state import RunStatus

if TYPE_CHECKING:
    from nexus.agent import Agent

logger = logging.getLogger(__name__)


# ── Pipeline Stages ───────────────────────────────────────────────────────────


class PipelineStage(str, Enum):
    """Ordered stages of the canonical execution pipeline."""

    REPO_UNDERSTANDING = "repo_understanding"
    PLANNING = "planning"
    CONTEXT_SELECTION = "context_selection"
    MODEL_ROUTING = "model_routing"
    EXECUTION = "execution"
    VERIFICATION = "verification"
    REPAIR = "repair"
    REVIEW = "review"
    EVIDENCE = "evidence"
    COMPLETION = "completion"


@dataclass
class StageResult:
    """Outcome of a single pipeline stage."""

    stage: PipelineStage
    success: bool
    duration_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    applicable: bool = True

    @property
    def status(self) -> str:
        if not self.applicable:
            return "not_applicable"
        return "passed" if self.success else "failed"


@dataclass
class PipelineResult:
    """Complete result of a pipeline execution."""

    user_input: str
    response: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)
    stage_results: list[StageResult] = field(default_factory=list)
    success: bool = False
    total_duration_ms: int = 0
    model_turns: int = 0
    recovered_from_error: bool = False
    status: str = RunStatus.RUNNING.value
    outcome: str = ""

    def failed_stages(self) -> list[StageResult]:
        """Return stages that did not succeed."""
        return [s for s in self.stage_results if not s.success]

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_input": self.user_input[:200],
            "success": self.success,
            "total_duration_ms": self.total_duration_ms,
            "model_turns": self.model_turns,
            "recovered_from_error": self.recovered_from_error,
            "status": self.status,
            "outcome": self.outcome,
            "stages": [
                {
                    "stage": s.stage.value,
                    "success": s.success,
                    "status": s.status,
                    "applicable": s.applicable,
                    "duration_ms": s.duration_ms,
                    "error": s.error,
                }
                for s in self.stage_results
            ],
        }


# ── Pipeline ──────────────────────────────────────────────────────────────────


class ExecutionPipeline:
    """
    Canonical execution pipeline — the single authoritative path through which
    all agent modes execute user requests.

    Each stage is discrete and independently observable. Failures in optional
    stages (repo understanding, verification) are logged but do not terminate
    execution. Failures in required stages (execution) are propagated.
    """

    def __init__(self, agent: "Agent") -> None:
        self._agent = agent
        self._logger = logging.getLogger(__name__)

    # ── Public entry point ────────────────────────────────────────────────────

    def run(
        self,
        user_input: str,
        *,
        interactive: bool = False,
        emit_ui: bool = False,
    ) -> PipelineResult:
        """
        Execute the full pipeline for a user request.

        Args:
            user_input: The raw user prompt.
            interactive: If True, stream output and support confirmations.
            emit_ui: If True, use the UI layer for progress indicators.

        Returns:
            PipelineResult with response, events and stage metrics.
        """
        pipeline_start = time.monotonic()
        result = PipelineResult(user_input=user_input)
        stage_results = result.stage_results

        # ── Stage 1: Repo Understanding ───────────────────────────────────────
        stage_results.append(self._stage_repo_understanding())

        # ── Stage 2: Planning ─────────────────────────────────────────────────
        analysis, plan, planning_result = self._stage_planning(user_input)
        stage_results.append(planning_result)
        if analysis.get("engineering_hard_block"):
            self._agent._begin_managed_run(user_input, analysis, plan)
            response = "BLOCKED: repository intelligence could not establish a safe mutation scope."
            report = self._agent._run_finalizer.finish(
                response, [], status_override=RunStatus.BLOCKED
            )
            result.response = response
            result.status = report.get("status", RunStatus.BLOCKED.value)
            result.outcome = report.get("outcome", "BLOCKED")
            result.total_duration_ms = int((time.monotonic() - pipeline_start) * 1000)
            return result

        # ── Stage 3: Context Selection ────────────────────────────────────────
        stage_results.append(self._stage_context_selection(user_input))

        # ── Stage 4: Model Routing ────────────────────────────────────────────
        routing_mode = self._stage_model_routing(analysis)
        stage_results.append(
            StageResult(
                stage=PipelineStage.MODEL_ROUTING,
                success=True,
                metadata={"routing_mode": routing_mode},
            )
        )

        self._agent._begin_managed_run(user_input, analysis, plan)
        horizon = getattr(getattr(self._agent, "engineering_brain", None), "long_horizon", None)
        if horizon is not None:
            try:
                from nexus.intelligence.engineering import LongHorizonPhase
                if horizon.state.phase == LongHorizonPhase.PLAN:
                    horizon.transition(
                        LongHorizonPhase.IMPLEMENT,
                        summary="Approved engineering plan entered bounded implementation.",
                    )
            except Exception as exc:
                self._logger.error("Long-horizon state transition failed closed: %s", exc)
                response = (
                    "BLOCKED: long-horizon engineering state could not enter the "
                    "implementation phase safely."
                )
                report = self._agent._run_finalizer.finish(
                    response, [], status_override=RunStatus.BLOCKED
                )
                result.response = response
                result.status = report.get("status", RunStatus.BLOCKED.value)
                result.outcome = report.get("outcome", "BLOCKED")
                result.total_duration_ms = int((time.monotonic() - pipeline_start) * 1000)
                return result

        # ── Stage 5: Execution ────────────────────────────────────────────────
        exec_result = self._stage_execution(
            user_input, analysis, plan, routing_mode, interactive=interactive, emit_ui=emit_ui
        )
        stage_results.append(exec_result["stage_result"])
        if not exec_result["stage_result"].success:
            result.response = exec_result.get("response", "❌ Execution failed.")
            result.events = exec_result.get("events", [])
            result.model_turns = exec_result.get("model_turns", 0)
            result.total_duration_ms = int((time.monotonic() - pipeline_start) * 1000)
            report = self._agent._run_finalizer.finish(
                result.response,
                result.events,
                status_override=RunStatus.FAILED,
            )
            result.status = report.get("status", RunStatus.FAILED.value)
            result.outcome = report.get("outcome", "FAILED")
            return result

        response = exec_result["response"]
        events = exec_result["events"]
        result.model_turns = exec_result.get("model_turns", 0)

        # ── Stage 6: Verification ─────────────────────────────────────────────
        ver_result = self._stage_verification()
        stage_results.append(ver_result)

        # ── Stage 7: Repair (if verification failed) ──────────────────────────
        if not ver_result.success:
            repair_result = self._stage_repair(user_input, ver_result)
            stage_results.append(repair_result)
            if repair_result.success:
                result.recovered_from_error = True
                ver_result = self._stage_verification()
                stage_results.append(ver_result)

        # ── Stage 8: Independent review ──────────────────────────────────────
        review_result = self._stage_review(routing_mode, ver_result.success)
        stage_results.append(review_result)
        if ver_result.success and not review_result.success and routing_mode == "hosted":
            repair_result = self._stage_repair(user_input, review_result)
            stage_results.append(repair_result)
            if repair_result.success:
                result.recovered_from_error = True
                ver_result = self._stage_verification()
                stage_results.append(ver_result)
                review_result = self._stage_review(routing_mode, ver_result.success)
                stage_results.append(review_result)

        # ── Stage 9: Evidence ─────────────────────────────────────────────────
        semantic_result = self._stage_semantic_verification(review_result)
        evidence_stage = self._stage_evidence()
        evidence_stage.metadata["semantic_verification"] = semantic_result.metadata
        stage_results.append(evidence_stage)

        semantic_status = str(semantic_result.metadata.get("status", ""))
        status_override = (
            RunStatus.FAILED
            if semantic_status == RunStatus.FAILED.value
            else None
        )
        report = self._agent._run_finalizer.finish(
            response, events, status_override=status_override
        )
        if (
            semantic_status == RunStatus.PARTIALLY_VERIFIED.value
            and report.get("status") == RunStatus.VERIFIED.value
        ):
            report = self._agent._run_finalizer.finish(
                response, events, status_override=RunStatus.PARTIALLY_VERIFIED
            )

        # ── Stage 10: Completion ──────────────────────────────────────────────
        result.response = response
        result.events = events
        result.status = report.get("status", RunStatus.UNVERIFIED.value)
        result.outcome = report.get("outcome", result.status)
        result.success = result.status == RunStatus.VERIFIED.value
        result.total_duration_ms = int((time.monotonic() - pipeline_start) * 1000)
        stage_results.append(
            StageResult(
                stage=PipelineStage.COMPLETION,
                success=result.success,
                metadata={"status": result.status, "outcome": result.outcome},
                error="" if result.success else f"Run finished as {result.outcome}",
            )
        )

        return result

    # ── Stage implementations ─────────────────────────────────────────────────

    def _stage_repo_understanding(self) -> StageResult:
        """Refresh the repository graph index if stale."""
        t = time.monotonic()
        try:
            if not (Path(self._agent.working_dir) / ".git").exists():
                return StageResult(
                    stage=PipelineStage.REPO_UNDERSTANDING,
                    success=True,
                    duration_ms=int((time.monotonic() - t) * 1000),
                    metadata={"refreshed": False, "reason": "Not a git repository"},
                )
            
            updated = self._agent.repo_graph.build(force=False)
            return StageResult(
                stage=PipelineStage.REPO_UNDERSTANDING,
                success=True,
                duration_ms=int((time.monotonic() - t) * 1000),
                metadata={
                    "refreshed": updated.scanned_files > 0
                    if hasattr(updated, "scanned_files")
                    else True
                },
            )
        except (OSError, TypeError, ValueError) as exc:
            # Non-fatal: missing graph degrades context quality, not correctness
            self._logger.debug("Repo understanding stage skipped: %s", exc)
            return StageResult(
                stage=PipelineStage.REPO_UNDERSTANDING,
                success=False,
                duration_ms=int((time.monotonic() - t) * 1000),
                metadata={"refreshed": False, "warning": str(exc)},
            )

    def _stage_planning(self, user_input: str) -> tuple[dict[str, Any], Any, StageResult]:
        """Classify intent and generate or retrieve the execution plan."""
        t = time.monotonic()
        strict_mode = (
            user_input.lstrip().startswith("[NEXUS VERIFIED REPAIR]")
            or bool(getattr(getattr(self._agent, "mode_policy", None), "require_review", False))
        )
        try:
            resume_analysis = getattr(self._agent, "_resume_analysis_override", None)
            resume_plan = getattr(self._agent, "_resume_plan_override", None)
            if resume_analysis is not None and resume_plan is not None:
                self._agent._resume_analysis_override = None  # noqa: SLF001
                self._agent._resume_plan_override = None  # noqa: SLF001
                self._agent.planner.current_plan = resume_plan
                return (
                    resume_analysis,
                    resume_plan,
                    StageResult(
                        stage=PipelineStage.PLANNING,
                        success=True,
                        duration_ms=int((time.monotonic() - t) * 1000),
                        metadata={
                            "intent": str(resume_analysis.get("intent", "")),
                            "plan_type": str(resume_analysis.get("plan_type", "")),
                            "resumed": True,
                            "completed_steps": sum(
                                step.status == TaskStatus.COMPLETED for step in resume_plan.steps
                            ),
                        },
                    ),
                )
            analysis = self._agent.planner.analyze(user_input)
            plan = None
            brain_contract = None
            intent = analysis.get("intent")
            requires_engineering_contract = (
                intent is not None and get_task_type(intent) != TaskType.READ_ONLY
            )
            if (
                requires_engineering_contract
                and getattr(self._agent, "engineering_brain", None)
            ):
                brain_contract = self._agent.engineering_brain.prepare(
                    user_input,
                    task_id=self._agent.conversation_id,
                    strict=strict_mode,
                )
                self._agent._engineering_contract = brain_contract
                analysis["engineering_contract"] = brain_contract.to_dict()
                self._agent.engineering_brain.record_decision(
                    "Use repository-intelligence-selected files as the initial mutation boundary",
                    rationale=(
                        f"Selected {len(brain_contract.decisive_files)} decisive files and "
                        f"{len(brain_contract.related_tests)} related tests at "
                        f"confidence {brain_contract.context_confidence:.2f}."
                    ),
                )
                horizon = self._agent.engineering_brain.long_horizon
                if horizon is not None and horizon.state.phase.value == "RESEARCH":
                    from nexus.intelligence.engineering import LongHorizonPhase
                    horizon.transition(
                        LongHorizonPhase.PLAN,
                        summary="Repository context and initial engineering contract established.",
                    )
                if brain_contract.plan_critic.get("blocking_issues") and strict_mode:
                    analysis["engineering_hard_block"] = True
                    return (
                        analysis,
                        None,
                        StageResult(
                            stage=PipelineStage.PLANNING,
                            success=False,
                            duration_ms=int((time.monotonic() - t) * 1000),
                            metadata={
                                "hard_block": True,
                                "issues": brain_contract.plan_critic.get("blocking_issues", []),
                            },
                            error="; ".join(brain_contract.plan_critic.get("blocking_issues", [])),
                        ),
                    )
            if analysis.get("plan_type") == "planned":
                repo_summary = (
                    self._agent.repo_graph.summary()
                    if getattr(self._agent, "repo_graph", None)
                    else None
                )
                plan = self._agent.planner.create_plan(
                    user_input, analysis, repo_summary=repo_summary
                )
                verification = self._agent._applicable_verification(  # noqa: SLF001
                    analysis["intent"], analysis.get("skills_needed", [])
                )
                plan.verification_steps = verification
                plan.acceptance_criteria = self._agent.planner._generate_acceptance_criteria(
                    user_input,
                    analysis["intent"],
                    verification,
                )
                if brain_contract is not None:
                    approved_files = list(dict.fromkeys([
                        *brain_contract.decisive_files,
                        *brain_contract.related_tests,
                    ]))
                    plan.permitted_files = list(dict.fromkeys([*plan.permitted_files, *approved_files]))
                    plan.acceptance_criteria = list(dict.fromkeys([
                        *plan.acceptance_criteria,
                        "Every changed file is justified by repository impact evidence",
                        "No prohibited or unrelated area is modified",
                        "External verification and independent semantic review support completion",
                    ]))
                for step in plan.steps:
                    step.acceptance_criteria = list(plan.acceptance_criteria)
                    if brain_contract is not None:
                        step.permitted_files = list(plan.permitted_files)
                    if step.checks:
                        step.checks = list(verification)
            return (
                analysis,
                plan,
                StageResult(
                    stage=PipelineStage.PLANNING,
                    success=True,
                    duration_ms=int((time.monotonic() - t) * 1000),
                    metadata={
                        "intent": str(analysis.get("intent", "")),
                        "plan_type": str(analysis.get("plan_type", "")),
                    },
                ),
            )
        except Exception as exc:
            if strict_mode:
                self._logger.error("Planning control-plane failure (blocked): %s", exc)
                return (
                    {
                        "intent": "unknown",
                        "plan_type": "direct",
                        "skills_needed": [],
                        "engineering_hard_block": True,
                        "engineering_control_error": str(exc),
                    },
                    None,
                    StageResult(
                        stage=PipelineStage.PLANNING,
                        success=False,
                        duration_ms=int((time.monotonic() - t) * 1000),
                        metadata={"hard_block": True, "control_plane_error": type(exc).__name__},
                        error=str(exc),
                    ),
                )
            self._logger.warning("Planning stage error (degraded read/review path): %s", exc)
            return (
                {"intent": "unknown", "plan_type": "direct", "skills_needed": []},
                None,
                StageResult(
                    stage=PipelineStage.PLANNING,
                    success=False,
                    duration_ms=int((time.monotonic() - t) * 1000),
                    metadata={"degraded": True},
                    error=str(exc),
                ),
            )

    def _stage_context_selection(self, user_input: str) -> StageResult:
        """Select minimal but sufficient context from the repository graph."""
        t = time.monotonic()
        try:
            if not self._agent._context_gathered:  # noqa: SLF001
                self._agent._gather_context()  # noqa: SLF001
            relevant = []
            contract = getattr(self._agent, "_engineering_contract", None)
            if contract is not None:
                relevant = [
                    {"path": path, "reasons": ["selected by Engineering Brain"]}
                    for path in contract.decisive_files
                ]
            elif getattr(self._agent, "repo_graph", None):
                relevant = self._agent.repo_graph.relevant_files(user_input, limit=16)
            return StageResult(
                stage=PipelineStage.CONTEXT_SELECTION,
                success=True,
                duration_ms=int((time.monotonic() - t) * 1000),
                metadata={
                    "selected_files": [item["path"] for item in relevant],
                    "selection_reasons": {
                        item["path"]: item.get("reasons", []) for item in relevant
                    },
                },
            )
        except (TypeError, ValueError) as exc:
            self._logger.debug("Context selection error (non-fatal): %s", exc)
            return StageResult(
                stage=PipelineStage.CONTEXT_SELECTION,
                success=False,
                duration_ms=int((time.monotonic() - t) * 1000),
                metadata={"warning": str(exc)},
            )

    def _stage_model_routing(self, analysis: dict[str, Any]) -> str:
        """Determine which execution path to use for this request."""
        agent = self._agent
        if agent._is_nova_model():  # noqa: SLF001
            return "nova"
        if agent._should_use_two_node(analysis):  # noqa: SLF001
            return "two_node"
        return "hosted"

    def _stage_execution(
        self,
        user_input: str,
        analysis: dict[str, Any],
        plan: Any,
        routing_mode: str,
        *,
        interactive: bool,
        emit_ui: bool,
    ) -> dict[str, Any]:
        """Run the model + tool loop."""
        t = time.monotonic()
        agent = self._agent
        try:
            if routing_mode == "nova":
                response, events = self._run_nova_turn(user_input, emit_ui=emit_ui)  # noqa: SLF001
            elif routing_mode == "two_node":
                response, events = self._run_two_node_turn(user_input, analysis, emit_ui=emit_ui)  # noqa: SLF001
            else:
                response, events = self._run_hosted_execution(  # noqa: SLF001
                    user_input,
                    analysis,
                    plan,
                    interactive=interactive,
                    emit_ui=emit_ui,
                )
        except Exception as exc:
            self._logger.exception("Execution failed")
            brain = getattr(self._agent, "engineering_brain", None)
            if brain is not None:
                try:
                    brain.record_failure(category="execution", phase="execution", summary=str(exc))
                except (OSError, TypeError, ValueError) as learning_exc:
                    self._logger.debug("Failure lesson persistence failed: %s", learning_exc)
            # Route through RecoveryController before giving up
            failure_record = FailureRecord(raw={"error": str(exc), "phase": "execution"})
            recovery_ctrl = getattr(self._agent, "recovery_controller", None) or RecoveryController(
                run_id=getattr(self._agent, "conversation_id", None),
                working_dir=getattr(self._agent, "working_dir", "."),
                budget=getattr(self._agent, "budget", None),
            )
            strategy, diagnosis, terminal = recovery_ctrl.handle_failure(
                failure_record,
                source_component="pipeline",
                phase="execution",
                model_id=getattr(self._agent, "model", None),
            )
            self._logger.debug(
                "Pipeline execution recovery: strategy=%s terminal=%s",
                getattr(strategy, "name", strategy),
                terminal,
            )
            return {
                "response": "❌ Execution failed.",
                "events": [],
                "stage_result": StageResult(
                    stage=PipelineStage.EXECUTION,
                    success=False,
                    duration_ms=int((time.monotonic() - t) * 1000),
                    error=str(exc),
                    metadata={"recovery_terminal": terminal},
                ),
            }

        execution_failed = bool(
            (response or "").lstrip().upper().startswith(("ERROR:", "BLOCKED:"))
            or (plan is not None and plan.has_failures)
        )

        return {
            "stage_result": StageResult(
                stage=PipelineStage.EXECUTION,
                success=not execution_failed,
                duration_ms=int((time.monotonic() - t) * 1000),
                error="Execution stopped before the requested work was complete."
                if execution_failed
                else "",
            ),
            "response": response,
            "events": events,
            "model_turns": len(
                [e for e in events if isinstance(e, dict) and e.get("type") == "model_turn"]
            ),
        }

    def _run_hosted_execution(
        self,
        user_input: str,
        analysis: dict[str, Any],
        plan: Any,
        *,
        interactive: bool,
        emit_ui: bool,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Execute every ready plan step under one global model-turn budget."""

        agent = self._agent
        if plan is None or not getattr(plan, "steps", None):
            return agent._run_hosted_turn(  # noqa: SLF001
                user_input,
                analysis,
                plan,
                interactive=interactive,
                emit_ui=emit_ui,
            )

        all_events: list[dict[str, Any]] = []
        responses: list[str] = []
        turns_used = 0
        retry_contexts: dict[int, str] = {}
        failure_fingerprints: dict[int, set[str]] = {}
        while not plan.is_complete:
            current = next(
                (step for step in plan.steps if step.status == TaskStatus.IN_PROGRESS),
                None,
            )
            if current is None:
                current = plan.next_step
                if current is None:
                    break
                agent.planner.advance_step(current.id, TaskStatus.IN_PROGRESS)

            remaining = max(0, agent.max_turns - turns_used)
            if remaining == 0:
                agent.planner.advance_step(
                    current.id,
                    TaskStatus.FAILED,
                    f"Global model-turn budget ({agent.max_turns}) exhausted.",
                )
                break

            step_budget = min(remaining, max(2, int(current.max_tool_calls) + 1))
            retry_context = retry_contexts.get(current.id, "")
            repeated_failure_warning = ""
            if retry_context:
                repeated_failure_warning = (
                    "\nThis is a focused retry. Do not repeat the previous approach. "
                    "Read the affected files and failure output before editing.\n"
                    f"Previous failure evidence:\n{retry_context[:6000]}\n"
                )
            step_prompt = (
                f"Original objective:\n{user_input}\n\n"
                f"Autonomous plan step {current.id + 1}/{len(plan.steps)}: "
                f"{current.title}\n{current.description}\n\n"
                f"Acceptance criteria: {current.acceptance_criteria}\n"
                f"Expected checks: {current.checks}\n"
                f"Permitted files: {current.permitted_files or ['repository-scoped']}\n"
                f"{repeated_failure_warning}"
                "Complete this step now. Inspect actual repository state, use tools as needed, "
                "run the narrowest relevant checks, and do not claim success without tool-backed "
                "evidence. Keep changes minimal and architecture-consistent. Do not commit, push, "
                "deploy, or expand the task scope."
            )
            agent._enforce_plan_tool_contract = True  # noqa: SLF001
            try:
                response, events = agent._run_hosted_turn(  # noqa: SLF001
                    step_prompt,
                    analysis,
                    plan,
                    interactive=interactive,
                    emit_ui=emit_ui,
                    max_turns_override=step_budget,
                )
            finally:
                agent._enforce_plan_tool_contract = False  # noqa: SLF001
            responses.append(response)
            all_events.extend(events)
            turns_used += sum(
                1
                for event in events
                if isinstance(event, dict) and event.get("type") == "model_turn"
            )
            agent.run_ledger.record_tasks(plan.steps)
            agent.run_ledger.record_plan(plan)
            if current.status == TaskStatus.COMPLETED:
                retry_contexts.pop(current.id, None)
                agent.run_ledger.checkpoint(
                    f"plan-step-{current.id}-completed",
                    plan=plan,
                    metadata={"task_id": current.id, "model_turns_used": turns_used},
                )
            elif current.status == TaskStatus.FAILED:
                failure = (current.error or current.result or response or "Unknown step failure").strip()
                fingerprint = failure[:1000]
                seen = failure_fingerprints.setdefault(current.id, set())
                repeated = fingerprint in seen
                seen.add(fingerprint)
                can_retry = current.attempts <= current.retry_limit and turns_used < agent.max_turns
                if can_retry:
                    from nexus.intelligence.repository.evidence import (  # noqa: PLC0415
                        FailureEvidenceExtractor,
                    )

                    brain = getattr(agent, "engineering_brain", None)
                    repository_paths: list[str] = []
                    context_files: list[str] = []
                    if brain is not None:
                        repository_paths = list(getattr(brain.repository, "files", {}))
                        context_bundle = getattr(brain, "context_bundle", None)
                        if context_bundle is not None:
                            context_files = [item.path for item in context_bundle.files]
                    signals = FailureEvidenceExtractor.extract(
                        failure,
                        repository_paths=repository_paths,
                    )
                    evidence_payload = {
                        "source_type": "plan_step_failure",
                        "summary": failure[:1000],
                        "context_files": context_files,
                        "failing_stack_files": list(signals.paths),
                        "failing_tests": list(signals.tests),
                        "unresolved_symbols": list(signals.symbols),
                        "missing_dependencies": list(signals.modules),
                        "failure_kinds": list(signals.failure_kinds),
                        "concurrency_terms": list(signals.concurrency_terms),
                        "migration_terms": list(signals.migration_terms),
                        "hypothesis_contradicted": repeated,
                        "root_cause_invalidated": repeated,
                    }
                    context_revision: dict[str, Any] = {}
                    if brain is not None and getattr(brain, "contract", None) is not None:
                        try:
                            context_revision = brain.expand_context_from_failure(
                                f"plan step {current.id} failed",
                                {"failure": failure, "signals": evidence_payload},
                            )
                            evidence_payload["additional_files"] = context_revision.get(
                                "added_paths", []
                            )
                            evidence_payload["task_profile"] = context_revision.get(
                                "task_profile", {}
                            )
                        except (OSError, TypeError, ValueError) as exc:
                            self._logger.warning(
                                "Plan-step context expansion failed: %s", exc
                            )
                    canonical_replanned = agent.planner.revise_canonical_plan(
                        plan,
                        trigger_reason=failure,
                        failed_step_id=current.id,
                        evidence=evidence_payload,
                    )
                    retry_contexts[current.id] = (
                        failure
                        + (
                            "\nNexus structurally revised the canonical engineering plan from "
                            "this failure evidence. Follow the inserted investigation and targeted "
                            "verification obligations before editing again."
                            if canonical_replanned
                            else ""
                        )
                        + (
                            "\nRepository context expanded with: "
                            + ", ".join(context_revision.get("added_paths", []))
                            if context_revision.get("added_paths")
                            else ""
                        )
                        + (
                            "\nThe same failure repeated. Re-evaluate the root-cause assumption and "
                            "inspect callers, tests, and dependency contracts before another edit."
                            if repeated
                            else ""
                        )
                    )
                    agent.run_ledger.append_event(
                        "plan_step_retry",
                        status="verified",
                        detail=f"Retrying failed plan step {current.id}.",
                        metadata={
                            "task_id": current.id,
                            "attempts": current.attempts,
                            "retry_limit": current.retry_limit,
                            "repeated_failure": repeated,
                            "failure": failure[:4000],
                            "canonical_replanned": canonical_replanned,
                            "context_revision": context_revision,
                        },
                    )
                    agent.planner.retry_step(current.id, failure)
                    agent.run_ledger.record_tasks(plan.steps)
                    agent.run_ledger.record_plan(plan)
                    continue
                break
            if "Local Nova fallback" in (response or "") or (response or "").lstrip().upper().startswith(("ERROR:", "BLOCKED:")):
                if current.status == TaskStatus.IN_PROGRESS:
                    if "Local Nova fallback" in (response or ""):
                        agent.planner.advance_step(current.id, TaskStatus.COMPLETED, response[:2000])
                    else:
                        agent.planner.advance_step(current.id, TaskStatus.FAILED, response[:2000])
                break

        return (responses[-1] if responses else ""), all_events

    def _stage_verification(self) -> StageResult:
        """Run post-execution verification checks."""
        t = time.monotonic()
        try:
            evidence = self._agent.evidence.records()[
                getattr(self._agent, "_turn_evidence_start", 0) :
            ]
            mutations = self._agent._effective_evidence(evidence, "file_mutation")  # noqa: SLF001
            checks = self._agent._effective_evidence(  # noqa: SLF001
                evidence, "verification_check"
            )
            engine_checks = [item for item in checks if item.get("tool") == "verification_engine"]
            if mutations and not engine_checks:
                self._agent._record_verification_report(  # noqa: SLF001
                    self._agent._run_verification_suite()
                )
                evidence = self._agent.evidence.records()[
                    getattr(self._agent, "_turn_evidence_start", 0) :
                ]
                checks = self._agent._effective_evidence(  # noqa: SLF001
                    evidence, "verification_check"
                )
            verified_mutations = all(item.get("status") == "verified" for item in mutations)
            verified_checks = bool(checks) and all(
                item.get("status") == "verified" for item in checks
            )
            if not mutations:
                return StageResult(
                    stage=PipelineStage.VERIFICATION,
                    success=True,
                    applicable=False,
                    duration_ms=int((time.monotonic() - t) * 1000),
                    metadata={
                        "status": "not_applicable",
                        "mutations": 0,
                        "verified": 0,
                        "checks": len(checks),
                        "checks_passed": sum(
                            1 for item in checks if item.get("status") == "verified"
                        ),
                    },
                )
            verified = verified_mutations and verified_checks
            horizon = getattr(getattr(self._agent, "engineering_brain", None), "long_horizon", None)
            if horizon is not None and verified:
                try:
                    from nexus.intelligence.engineering import LongHorizonPhase
                    ids = [str(item.get("id")) for item in [*mutations, *checks] if item.get("id")]
                    if ids and horizon.state.phase == LongHorizonPhase.IMPLEMENT:
                        horizon.transition(
                            LongHorizonPhase.VERIFY,
                            summary="Mutation evidence and deterministic verification passed.",
                            evidence_ids=ids,
                        )
                except ValueError as exc:
                    self._logger.debug("Long-horizon verification transition skipped: %s", exc)
            return StageResult(
                stage=PipelineStage.VERIFICATION,
                success=verified,
                duration_ms=int((time.monotonic() - t) * 1000),
                metadata={
                    "mutations": len(mutations),
                    "verified": sum(1 for e in mutations if e.get("status") == "verified"),
                    "checks": len(checks),
                    "checks_passed": sum(1 for e in checks if e.get("status") == "verified"),
                },
                error=""
                if verified
                else (
                    f"{sum(1 for e in mutations if e.get('status') != 'verified')} "
                    "mutations unverified; "
                    f"{sum(1 for e in checks if e.get('status') != 'verified')} checks failed"
                ),
            )
        except Exception as exc:
            self._logger.debug("Verification stage error: %s", exc)
            return StageResult(
                stage=PipelineStage.VERIFICATION,
                success=False,
                duration_ms=int((time.monotonic() - t) * 1000),
                metadata={"warning": str(exc)},
            )

    def _stage_repair(self, user_input: str, ver_result: StageResult) -> StageResult:
        """Attempt autonomous repair when verification fails."""
        t = time.monotonic()
        try:
            brain = getattr(self._agent, "engineering_brain", None)
            if brain is not None:
                brain.record_failure(
                    category="verification",
                    phase="verification",
                    summary=ver_result.error or "Deterministic verification failed.",
                )
                horizon = brain.long_horizon
                if horizon is not None:
                    from nexus.intelligence.engineering import LongHorizonPhase
                    if horizon.state.phase in {LongHorizonPhase.VERIFY, LongHorizonPhase.REVIEW}:
                        horizon.transition(
                            LongHorizonPhase.IMPLEMENT,
                            summary="Verification/review failed; returning to bounded implementation.",
                        )
            from nexus.repair import RepairLoop  # noqa: PLC0415

            loop = RepairLoop(
                self._agent,
                max_iterations=max(3, int(getattr(self._agent.mode_policy, "retry_budget", 0)) + 2),
                budget_seconds=240 if self._agent.mode_policy.model_strategy == "quality" else 150,
            )
            repaired = loop.attempt(user_input, failure_context=ver_result.error)
            return StageResult(
                stage=PipelineStage.REPAIR,
                success=repaired,
                duration_ms=int((time.monotonic() - t) * 1000),
                metadata={"attempted": True},
                error="" if repaired else "Repair budget exhausted without success",
            )
        except ImportError:
            # repair module not yet available — skip silently
            return StageResult(
                stage=PipelineStage.REPAIR,
                success=False,
                duration_ms=int((time.monotonic() - t) * 1000),
                metadata={"skipped": True},
            )
        except (TypeError, ValueError) as exc:
            self._logger.warning("Repair stage error: %s", exc)
            return StageResult(
                stage=PipelineStage.REPAIR,
                success=False,
                duration_ms=int((time.monotonic() - t) * 1000),
                error=str(exc),
            )

    def _stage_review(self, routing_mode: str, verification_succeeded: bool) -> StageResult:
        """Require a fail-closed review after deterministic verification."""

        t = time.monotonic()
        if not verification_succeeded:
            return StageResult(
                stage=PipelineStage.REVIEW,
                success=False,
                duration_ms=int((time.monotonic() - t) * 1000),
                metadata={"skipped": True},
                error="Independent review withheld because deterministic verification failed.",
            )
        if routing_mode == "nova":
            if self._agent.mode_policy.require_review:
                return StageResult(
                    stage=PipelineStage.REVIEW,
                    success=False,
                    duration_ms=int((time.monotonic() - t) * 1000),
                    metadata={
                        "review_assurance": "deterministic_only",
                        "local_guardrails": True,
                    },
                    error=(
                        "This execution mode requires an independent semantic reviewer, "
                        "but the local Nova route provides deterministic validation only. "
                        "Use local-only/autonomous mode or configure a hosted executor/reviewer."
                    ),
                )
            return StageResult(
                stage=PipelineStage.REVIEW,
                success=True,
                duration_ms=int((time.monotonic() - t) * 1000),
                metadata={
                    "review_assurance": "deterministic_only",
                    "local_guardrails": True,
                    "independent_semantic_review": False,
                },
            )
        try:
            approved, summary = self._agent._run_independent_review()  # noqa: SLF001
            return StageResult(
                stage=PipelineStage.REVIEW,
                success=approved,
                duration_ms=int((time.monotonic() - t) * 1000),
                metadata={"summary": summary[:1000]},
                error="" if approved else summary[:2000],
            )
        except (LookupError, TypeError, ValueError) as exc:
            return StageResult(
                stage=PipelineStage.REVIEW,
                success=False,
                duration_ms=int((time.monotonic() - t) * 1000),
                error=f"Independent review failed closed: {exc}",
            )

    def _stage_semantic_verification(self, review_result: StageResult) -> StageResult:
        """Verify requirement satisfaction, scope discipline, and external evidence."""
        t = time.monotonic()
        brain = getattr(self._agent, "engineering_brain", None)
        if brain is None or getattr(brain, "contract", None) is None:
            return StageResult(
                stage=PipelineStage.EVIDENCE,
                success=False,
                applicable=False,
                duration_ms=int((time.monotonic() - t) * 1000),
                metadata={"status": RunStatus.PARTIALLY_VERIFIED.value},
                error="Engineering contract unavailable.",
            )
        evidence = self._agent.evidence.records()[
            getattr(self._agent, "_turn_evidence_start", 0) :
        ]
        changed_files = []
        for item in self._agent.history.changes[getattr(self._agent, "_run_history_start", 0) :]:
            raw = item.get("filepath", "")
            if not raw:
                continue
            try:
                changed_files.append(Path(raw).resolve().relative_to(Path(self._agent.working_dir).resolve()).as_posix())
            except ValueError:
                changed_files.append(str(raw))
        criteria = []
        plan = getattr(self._agent, "_active_plan", None)
        if plan is not None:
            criteria = list(getattr(plan, "acceptance_criteria", []) or [])
        result = brain.semantic_verify(
            evidence=evidence,
            changed_files=changed_files,
            acceptance_criteria=criteria,
            review_required=bool(self._agent.mode_policy.require_review),
        )
        horizon = brain.long_horizon
        if horizon is not None:
            try:
                from nexus.intelligence.engineering import LongHorizonPhase
                semantic_ids = [str(item.get("id")) for item in evidence if item.get("id")]
                if review_result.success and horizon.state.phase == LongHorizonPhase.VERIFY and semantic_ids:
                    horizon.transition(
                        LongHorizonPhase.REVIEW,
                        summary="Independent review completed after deterministic verification.",
                        evidence_ids=semantic_ids,
                    )
                if result.satisfied and horizon.state.phase == LongHorizonPhase.REVIEW and semantic_ids:
                    horizon.transition(
                        LongHorizonPhase.COMPLETE,
                        summary="Semantic acceptance and scope checks passed.",
                        evidence_ids=semantic_ids,
                    )
                elif not result.satisfied and horizon.state.phase not in {LongHorizonPhase.COMPLETE, LongHorizonPhase.FAILED}:
                    horizon.transition(
                        LongHorizonPhase.BLOCKED,
                        summary="Semantic acceptance remained incomplete.",
                        error="; ".join(item.message for item in result.findings),
                    )
            except Exception as exc:
                from nexus.intelligence.engineering import LongHorizonIntegrityError
                if isinstance(exc, LongHorizonIntegrityError):
                    self._agent.evidence.append(
                        kind="engineering_state_integrity",
                        claim="Long-horizon state remained intact during semantic completion",
                        status="failed",
                        raw_output=str(exc),
                    )
                    return StageResult(
                        stage=PipelineStage.EVIDENCE,
                        success=False,
                        duration_ms=int((time.monotonic() - t) * 1000),
                        metadata={"status": RunStatus.FAILED.value, "integrity_error": str(exc)},
                        error=str(exc),
                    )
                self._logger.debug("Long-horizon completion transition skipped: %s", exc)
        self._agent.evidence.append(
            kind="semantic_verification",
            claim="Engineering Brain evaluated objective, scope, and acceptance evidence",
            status=(
                "verified"
                if result.satisfied
                else ("failed" if result.status == RunStatus.FAILED.value else "unverified")
            ),
            raw_output=str(result.to_dict()),
            metadata=result.to_dict(),
        )
        return StageResult(
            stage=PipelineStage.EVIDENCE,
            success=result.satisfied,
            duration_ms=int((time.monotonic() - t) * 1000),
            metadata=result.to_dict(),
            error="" if result.satisfied else "; ".join(item.message for item in result.findings),
        )

    def _stage_evidence(self) -> StageResult:
        """Snapshot evidence and finalize the run record."""
        t = time.monotonic()
        try:
            count = len(self._agent.evidence.records())
            return StageResult(
                stage=PipelineStage.EVIDENCE,
                success=True,
                duration_ms=int((time.monotonic() - t) * 1000),
                metadata={"evidence_records": count},
            )
        except (TypeError, ValueError) as exc:
            return StageResult(
                stage=PipelineStage.EVIDENCE,
                success=False,
                duration_ms=int((time.monotonic() - t) * 1000),
                metadata={"warning": str(exc)},
            )


    def _run_two_node_turn(
        self, user_input: str, analysis: dict, emit_ui: bool = True
    ) -> tuple[str, list[dict]]:
        """Run a hosted-model turn through Ceiling planning and Nova Intern execution."""
        from nexus.two_node_backend import TwoNodeBackend

        agent = self._agent
        events: list[dict] = []
        agent.messages.append({"role": "user", "content": user_input})

        backend = TwoNodeBackend(
            client=agent.client,
            ceiling_model_id=agent.model_cfg["id"],
            ceiling_model_name=agent.model_cfg["name"],
            working_dir=agent.working_dir,
            intern_model=agent.model_cfg.get("intern_model", "nova_codex"),
            run_ledger=agent.run_ledger,
        )

        try:
            if emit_ui:
                live = ui.LiveStatus()
                live.start("Preparing task graph...")
                try:
                    result = backend.run(user_input, planner_analysis=analysis)
                finally:
                    live.stop()
            else:
                result = backend.run(user_input, planner_analysis=analysis)
        except (OSError, RuntimeError) as e:
            if emit_ui:
                ui.print_warning(f"Two-node backend error: {e}")
            if agent.messages and agent.messages[-1]["role"] == "user":
                agent.messages.pop()
            raise RuntimeError(f"Two-node backend failed: {e}") from e

        def record_result(candidate, phase: str) -> None:
            if candidate.execution_plan is not None:
                agent._active_plan = candidate.execution_plan
                agent.planner.current_plan = candidate.execution_plan
            agent.run_ledger.append_model_call(
                role=f"ceiling_{phase}",
                model=agent.model_cfg["id"],
                status=("verified" if candidate.review_approved else "failed"),
                usage=agent.budget.snapshot().get("usage", {}),
                detail=candidate.review_summary,
            )
            agent.evidence.append(
                kind="planning_review",
                claim=f"independent reviewer evaluated {phase} candidate",
                status="verified" if candidate.review_approved else "failed",
                raw_output=candidate.review_summary,
                metadata={"findings": candidate.review_findings},
            )
            for execution in candidate.executions:
                if execution.node.startswith("Nova") and not execution.escalated:
                    agent.routing_stats["nova_tasks"] += 1
                else:
                    agent.routing_stats["ceiling_tasks"] += 1
                agent.routing_stats["nova_retries"] += max(0, execution.attempts - 1)
                if execution.escalated:
                    agent.routing_stats["escalations"] += 1
                agent.evidence.append(
                    kind="routing",
                    claim=f"subtask {execution.task.id} routed to {execution.node}",
                    status="verified" if execution.proposals else "failed",
                    raw_output=(
                        execution.guardrail_log + "\n\n[RAW MODEL OUTPUT]\n" + execution.raw_output
                    ).strip(),
                    metadata={
                        "reason": execution.route_reason,
                        "attempts": execution.attempts,
                        "verdict": execution.verdict,
                        "escalated": execution.escalated,
                        "failure_kind": execution.failure_kind,
                    },
                )

        def apply_result(candidate, phase: str) -> list[str]:
            changed: list[str] = []
            for proposal in candidate.proposals:
                args = dict(proposal.args)
                display_args = {
                    key: value for key, value in args.items() if key != "_nova_guardrail"
                }
                if emit_ui:
                    ui.print_tool_call(proposal.name, display_args)
                tool_result, success = agent._execute_tool_with_safety(proposal.name, args)
                if emit_ui:
                    ui.print_tool_result(tool_result, success)
                events.append(
                    {
                        "type": "tool_call",
                        "name": proposal.name,
                        "args": display_args,
                        "result": tool_result,
                        "success": success,
                        "node": phase,
                        "guardrail": proposal.guardrail_summary,
                    }
                )
                if success:
                    path = str(display_args.get("path", ""))
                    if path:
                        changed.append(path)
            return changed

        breakdowns = [result.format_breakdown()]
        record_result(result, "initial")

        if not result.review_approved and result.review_findings:
            repair_analysis = {
                key: value for key, value in analysis.items() if key != "resume_plan"
            }
            focused_request = (
                f"{user_input}\n\nIndependent review rejected the candidate. "
                "Produce the smallest complete repair addressing only these findings:\n"
                + "\n".join(f"- {item}" for item in result.review_findings)
            )
            repair_result = backend.run(
                focused_request,
                planner_analysis=repair_analysis,
            )
            record_result(repair_result, "review_repair")
            breakdowns.append(repair_result.format_breakdown())
            result = repair_result

        changed_paths = apply_result(result, "two-node")
        applied = bool(changed_paths) and all(
            event.get("success", False) for event in events if event.get("type") == "tool_call"
        )
        recovered_without_edits = bool(
            result.review_approved
            and not result.proposals
            and result.execution_plan is not None
            and result.execution_plan.steps
            and all(step.status == TaskStatus.COMPLETED for step in result.execution_plan.steps)
            and any(
                execution.route_reason == "recovered verified checkpoint"
                for execution in result.executions
            )
        )
        if applied:
            security_result, security_ok = agent._execute_tool_with_safety(
                "security_scan",
                {"paths": changed_paths},
            )
            events.append(
                {
                    "type": "tool_call",
                    "name": "security_scan",
                    "args": {"paths": changed_paths},
                    "result": security_result,
                    "success": security_ok,
                    "node": "nexus-verifier",
                }
            )
        if applied or recovered_without_edits:
            verification_report = agent._record_verification_report(agent._run_verification_suite())
            if emit_ui:
                ui.console.print(verification_report.format_report())
            if not verification_report.all_passed:
                repair_analysis = {
                    key: value for key, value in analysis.items() if key != "resume_plan"
                }
                focused_request = (
                    f"{user_input}\n\nThe candidate was applied in an isolated workspace, "
                    "but deterministic verification failed. Repair only the failing checks "
                    "and preserve already passing behavior.\n\n"
                    f"{verification_report.format_report()}"
                )
                repair_result = backend.run(
                    focused_request,
                    planner_analysis=repair_analysis,
                )
                record_result(repair_result, "verification_repair")
                breakdowns.append(repair_result.format_breakdown())
                if repair_result.review_approved:
                    apply_result(repair_result, "two-node-repair")
                    rerun = agent._record_verification_report(agent._run_verification_suite())
                    if emit_ui:
                        ui.console.print(rerun.format_report())

        breakdown = "\n\n".join(breakdowns)
        if emit_ui:
            ui.console.print(breakdown)
        breakdown = agent._guard_completion_claims(breakdown)
        agent.messages.append({"role": "assistant", "content": breakdown})
        agent._auto_save()
        return breakdown, events



    def _run_nova_turn(self, user_input: str, emit_ui: bool = True) -> tuple[str, list[dict]]:
        """Run one turn through the local Nova pipeline backend."""
        from nexus.nova_backend import NovaBackendError, NovaPipelineBackend

        agent = self._agent
        events: list[dict] = []
        agent._load_rules_and_preferences()
        agent.messages.append({"role": "user", "content": user_input})

        backend = NovaPipelineBackend(
            model=agent.model_cfg.get("ollama_model", "nova_codex"),
            working_dir=agent.working_dir,
        )

        try:
            if emit_ui:
                live = ui.LiveStatus()
                live.start("Running local worker...")
                try:
                    nova_result = backend.run(user_input)
                finally:
                    live.stop()
            else:
                nova_result = backend.run(user_input)
        except NovaBackendError as e:
            content = f"Nova guardrails blocked the output: {e}"
            if emit_ui:
                ui.print_error(content)
            if agent.messages and agent.messages[-1].get("role") == "user":
                agent.messages.pop()
            raise RuntimeError(content) from e
        except (LookupError, OSError, RuntimeError) as e:
            content = f"Nova backend error: {e}"
            if emit_ui:
                ui.print_error(content)
            if agent.messages and agent.messages[-1].get("role") == "user":
                agent.messages.pop()
            raise RuntimeError(content) from e

        agent.routing_stats["nova_tasks"] += 1
        agent.run_ledger.append_model_call(
            role="intern",
            model=agent.model_cfg.get("ollama_model", "nova_codex"),
            status="completed" if nova_result.raw_output else "failed",
            detail=(
                f"guarded proposals={len(nova_result.proposals)}; "
                f"declared_test={bool(nova_result.test_command)}"
            ),
        )

        if emit_ui and nova_result.raw_output:
            ui.console.print(nova_result.raw_output)
        if emit_ui and nova_result.guardrail_output:
            ui.print_info("Nova guardrail verdicts:")
            ui.console.print(nova_result.guardrail_output)

        # Structured/headless callers receive the same complete model and
        # guardrail transcript that interactive users see.  This is evidence,
        # not a shortened summary, so rejected generations remain auditable.
        events.append(
            {
                "type": "model_trace",
                "node": "nova",
                "raw_output": nova_result.raw_output,
                "guardrail_output": nova_result.guardrail_output,
            }
        )
        events.append(
            {
                "type": "model_turn",
                "node": "nova",
                "proposals": len(nova_result.proposals),
                "declared_test": bool(nova_result.test_command),
            }
        )

        mutated = False
        proposal_failed = False
        for proposal in nova_result.proposals:
            args = dict(proposal.args)
            display_args = {k: v for k, v in args.items() if k != "_nova_guardrail"}
            if emit_ui:
                ui.print_tool_call(proposal.name, display_args)
            result, success = agent._execute_tool_with_safety(proposal.name, args)
            if emit_ui:
                ui.print_tool_result(result, success)
            events.append(
                {
                    "type": "tool_call",
                    "name": proposal.name,
                    "args": display_args,
                    "result": result,
                    "success": success,
                    "nova_guardrail": proposal.guardrail_summary,
                }
            )
            if success and proposal.name in {
                "write_file",
                "edit_file",
                "patch_file",
                "multi_edit",
                "replace_file_content",
                "multi_replace_file_content",
                "write_to_file",
            }:
                mutated = True
            if not success:
                proposal_failed = True

        test_failed = False
        if mutated and not proposal_failed and nova_result.test_command:
            test_result, test_success, evidence_id = agent._run_declared_test_command(
                nova_result.test_command,
                source="nova",
                emit_ui=emit_ui,
            )
            test_failed = not test_success
            events.append(
                {
                    "type": "tool_call",
                    "name": "run_command",
                    "args": {"command": nova_result.test_command},
                    "result": test_result,
                    "success": test_success,
                    "node": "nova-declared-test",
                    "evidence_id": evidence_id,
                }
            )

        final_text = nova_result.assistant_text
        if proposal_failed:
            final_text += (
                "\n\nOne or more guarded file operations failed; completion is unverified."
            )
        if test_failed:
            final_text += "\n\nThe model-declared acceptance test failed; completion is unverified."
        final_content = agent._guard_completion_claims(final_text)
        if emit_ui:
            ui.print_response_complete()
        agent.messages.append({"role": "assistant", "content": final_content})
        agent._auto_save()
        return final_content, events

    # ── Subagent Integration ─────────────────────────────────────────────

