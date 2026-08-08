"""
RunFinalizer — extracted service for run completion and evidence evaluation.

This module provides a clean boundary for the ~379-line ``_run_finalizer.finish``
logic inside Agent.  By making it a standalone service, the completion pipeline
becomes independently testable, readable, and replaceable without touching the
monolithic Agent class.

Architecture::

    RunFinalizer
    ├── evaluate_evidence()     classify mutations, checks, commands, review
    ├── assess_criteria()       match task criteria to evidence records
    ├── determine_status()      VERIFIED / PARTIALLY_VERIFIED / FAILED / …
    ├── write_report()          persist final_report.json to the run directory
    └── summarise()             return the machine-readable report dict
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Any

from nexus.planner import IntentType, TaskType, get_task_type
from nexus.run_state import CriterionResult, CriterionStatus, RunStatus


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root.resolve())
        return True
    except ValueError:
        return False


if TYPE_CHECKING:
    from nexus.agent import Agent
    from nexus.run_state import RunStatus

logger = logging.getLogger(__name__)


class EvidenceClass(str, Enum):
    """Broad category used during final-report assembly."""

    MUTATION = "file_mutation"
    VERIFICATION = "verification_check"
    COMMAND = "command"
    BEHAVIORAL = "behavioral_verification"
    REVIEW = "independent_review"


@dataclass
class EvidenceSummary:
    """Aggregated evidence for one run turn."""

    verified_mutations: list[dict[str, Any]] = field(default_factory=list)
    passing_checks: list[dict[str, Any]] = field(default_factory=list)
    passing_commands: list[dict[str, Any]] = field(default_factory=list)
    passing_behavioral: list[dict[str, Any]] = field(default_factory=list)
    approved_reviews: list[dict[str, Any]] = field(default_factory=list)
    failed_evidence: list[dict[str, Any]] = field(default_factory=list)
    reproduction_evidence: list[dict[str, Any]] = field(default_factory=list)

    @property
    def has_any_success(self) -> bool:
        return bool(
            self.verified_mutations
            or self.passing_checks
            or self.passing_commands
            or self.passing_behavioral
        )

    @property
    def has_failures(self) -> bool:
        return bool(self.failed_evidence)


class RunFinalizer:
    """
    Service that evaluates evidence and produces the final run report.

    This is a *delegation target* for ``Agent._run_finalizer.finish``.
    It surfaces the evaluation logic as independently callable methods so
    that the report generation pipeline can be tested without a full agent.

    Usage::

        finalizer = RunFinalizer(agent)
        report = finalizer.finish(content, events)
    """

    def __init__(self, agent: "Agent") -> None:
        self._agent = agent

    # ──────────────────────────────────────────────────────────────────────────
    # Public interface
    # ──────────────────────────────────────────────────────────────────────────

    def _evaluate_unrelated_files(
        self, criterion: str, plan: Any, changes: list
    ) -> CriterionResult:
        permitted = list(getattr(plan, "permitted_files", []) or [])
        outside = []
        for item in changes:
            changed_path = Path(item["filepath"]).resolve()
            if not _is_relative_to(changed_path, Path(self._agent.working_dir)):
                outside.append(item["filepath"])
                continue
            relative = changed_path.relative_to(Path(self._agent.working_dir).resolve()).as_posix()
            if permitted and not any(
                fnmatch(relative, pattern) or relative == pattern for pattern in permitted
            ):
                outside.append(relative)
        return CriterionResult(
            criterion,
            CriterionStatus.UNSATISFIED if outside else CriterionStatus.SATISFIED,
            detail=(
                "Out-of-scope changes: " + ", ".join(outside)
                if outside
                else (
                    "All recorded changes matched the plan's permitted files."
                    if permitted
                    else "All recorded changes remained inside the authorized workspace."
                )
            ),
        )

    def _evaluate_fingerprinted_mutations(self, criterion: str, evidence: list) -> CriterionResult:
        mutation_records = [item for item in evidence if item.get("kind") == "file_mutation"]
        task_type = get_task_type(self._agent._active_analysis.get("intent", IntentType.UNKNOWN))
        satisfied = (
            (not mutation_records and task_type == TaskType.READ_ONLY)
            or bool(mutation_records)
            and all(item.get("status") == "verified" for item in mutation_records)
        )
        return CriterionResult(
            criterion,
            CriterionStatus.SATISFIED if satisfied else CriterionStatus.UNVERIFIED,
            evidence_ids=[item["id"] for item in mutation_records],
            detail=(
                "Every recorded mutation passed disk verification."
                if satisfied
                else "No complete verified mutation set was recorded."
            ),
        )

    def _evaluate_objective_implementation(
        self,
        criterion: str,
        verified_mutations: list,
        passing_checks: list,
        passing_behavioral: list,
        approved_reviews: list,
        successful_command_text: set,
    ) -> CriterionResult:
        objective_evidence = [
            *verified_mutations,
            *passing_checks,
            *passing_behavioral,
            *approved_reviews,
        ]
        task_type = get_task_type(self._agent._active_analysis.get("intent", IntentType.UNKNOWN))

        if task_type == TaskType.READ_ONLY:
            objective_satisfied = True
        elif task_type == TaskType.OPERATIONAL:
            objective_satisfied = bool(
                passing_checks or passing_behavioral or successful_command_text
            )
        else:
            review_satisfied = bool(approved_reviews) or (
                self._agent._is_nova_model() and not self._agent.mode_policy.require_review
            )
            objective_satisfied = (
                bool(verified_mutations)
                and bool(passing_checks or passing_behavioral)
                and review_satisfied
            )

        return CriterionResult(
            criterion,
            CriterionStatus.SATISFIED if objective_satisfied else CriterionStatus.UNVERIFIED,
            evidence_ids=[item["id"] for item in objective_evidence],
            detail=(
                (
                    "Verified mutations and deterministic checks support the objective; "
                    "this local-only run has no independent semantic reviewer."
                    if self._agent._is_nova_model() and not approved_reviews
                    else "Verified mutations, deterministic checks, and independent review "
                    "support the requested objective."
                )
                if objective_satisfied
                else "A mutation alone is insufficient; deterministic checks and the "
                "review assurance required by this mode must be present."
            ),
        )

    def _evaluate_verification_checks(
        self, criterion: str, matched_checks: list
    ) -> CriterionResult:
        return CriterionResult(
            criterion,
            CriterionStatus.SATISFIED if matched_checks else CriterionStatus.UNVERIFIED,
            evidence_ids=[item["id"] for item in matched_checks],
            detail=(
                "A matching passing project check exists."
                if matched_checks
                else "No matching passing project check was recorded."
            ),
        )

    def _evaluate_security_constraints(
        self, criterion: str, passing_behavioral: list, matched_checks: list
    ) -> CriterionResult:
        security_evidence = [
            item for item in passing_behavioral if item.get("tool") == "security_scan"
        ] + matched_checks
        return CriterionResult(
            criterion,
            CriterionStatus.SATISFIED if security_evidence else CriterionStatus.UNVERIFIED,
            evidence_ids=[item["id"] for item in security_evidence],
            detail=(
                "A passing bounded security check was recorded."
                if security_evidence
                else "No passing security check was recorded."
            ),
        )

    def _apply_verified_workspace(self) -> tuple[bool, str]:
        """Apply a verified isolated workspace exactly once.

        Automatic application is restricted to modes whose policy explicitly
        grants ``may_apply``. Review/workspace modes continue to return a diff
        for human approval. A failed merge is treated as a failed run rather
        than reporting a false VERIFIED outcome.
        """
        if self._agent._workspace_applied:
            return True, self._agent._workspace_apply_detail or "Workspace already applied."
        if not self._agent.mode_policy.may_apply:
            return True, "Execution mode requires manual workspace application."
        if self._agent.worktree is None or self._agent.worktree.info is None:
            return True, "No isolated workspace needs application."

        try:
            pending_diff = self._agent.worktree.diff()
            if not pending_diff.strip():
                self._agent._workspace_applied = True
                self._agent._workspace_apply_detail = "Verified run produced no workspace diff."
                return True, self._agent._workspace_apply_detail
            self._agent.worktree.apply()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            detail = f"Verified workspace could not be applied safely: {exc}"
            self._agent._workspace_apply_detail = detail
            self._agent.evidence.append(
                kind="workspace_apply",
                claim="apply verified isolated workspace to source repository",
                status="failed",
                raw_output=detail,
                metadata={
                    "source": self._agent.source_working_dir,
                    "workspace": self._agent.working_dir,
                },
            )
            return False, detail

        self._agent._workspace_applied = True
        self._agent._workspace_apply_detail = (
            "Verified isolated workspace was applied to the source repository."
        )
        self._agent._permissions_used.add("workspace: apply verified changes")
        self._agent.evidence.append(
            kind="workspace_apply",
            claim="apply verified isolated workspace to source repository",
            status="verified",
            raw_output=self._agent._workspace_apply_detail,
            metadata={
                "source": self._agent.source_working_dir,
                "workspace": self._agent.working_dir,
                "backend": self._agent.worktree.info.backend,
            },
        )
        return True, self._agent._workspace_apply_detail

    def _current_workspace_revision(self) -> str:
        try:
            from nexus.intelligence.repository.snapshot import workspace_revision
            return workspace_revision(self._agent.working_dir)
        except (OSError, ValueError):
            return ""

    @staticmethod
    def _revision_bound_verification(
        records: list[dict[str, Any]], revision: str
    ) -> list[dict[str, Any]]:
        if not revision:
            return records
        return [
            item for item in records
            if not (item.get("metadata") or {}).get("workspace_revision")
            or (item.get("metadata") or {}).get("workspace_revision") == revision
        ]

    @staticmethod
    def _validated_passing_checks(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        selected: list[dict[str, Any]] = []
        for item in records:
            metadata = item.get("metadata") or {}
            if item.get("status") != "verified":
                continue
            if metadata.get("independently_validated") is not True:
                continue
            if metadata.get("check_type") in {"test", "tests"} and not (
                metadata.get("runner_valid") is True
                and metadata.get("verification_valid") is True
            ):
                continue
            selected.append(item)
        return selected

    def _project_test_gate_issue(
        self,
        changes: list[dict[str, Any]],
        passing_checks: list[dict[str, Any]],
        revision: str,
    ) -> str:
        commands = getattr(getattr(self._agent, "verifier", None), "commands", {})
        if not changes or not str(commands.get("test", "") or ""):
            return ""
        for item in passing_checks:
            metadata = item.get("metadata") or {}
            if metadata.get("check_type") not in {"test", "tests"}:
                continue
            if metadata.get("project_gate") is not True:
                continue
            if metadata.get("verification_valid") is not True:
                continue
            if revision and metadata.get("workspace_revision") != revision:
                continue
            return ""
        return (
            "Configured project test suite lacks validated full-suite evidence "
            "for the final workspace revision."
        )

    def finish(
        self,
        content: str,
        events: list[dict[str, Any]] | None = None,
        *,
        status_override: RunStatus | None = None,
    ) -> dict[str, Any]:
        from nexus.agent import _redact_runtime_text

        
        """Evaluate evidence and write a machine-readable final report."""
        if not self._agent.run_ledger.turn_dir:
            return {}
        evidence = self._agent.evidence.records()[getattr(self, "_turn_evidence_start", 0) :]
        mutation_records = self._agent._effective_evidence(evidence, "file_mutation")
        verification_records = self._agent._effective_evidence(evidence, "verification_check")
        current_workspace_revision = self._current_workspace_revision()
        verification_records = self._revision_bound_verification(
            verification_records, current_workspace_revision
        )
        effective_state_ids = {
            str(item.get("id")) for item in [*mutation_records, *verification_records]
        }
        changes = self._agent.history.changes[self._agent._run_history_start :]
        command_records = [item for item in evidence if item.get("kind") == "command"]
        passing_commands = [
            item
            for item in command_records
            if item.get("status") == "verified" and item.get("exit_code") == 0
        ]
        successful_command_text = {
            item.get("command", "") for item in passing_commands if item.get("command")
        }
        latest_behavioral_status = {
            item.get("tool", ""): item.get("status")
            for item in evidence
            if item.get("kind") == "behavioral_verification"
        }
        failed_evidence = [
            item
            for item in evidence
            if item.get("status") == "failed"
            and item.get("kind") not in {"routing", "independent_review"}
            and (
                item.get("kind") not in {"file_mutation", "verification_check"}
                or str(item.get("id")) in effective_state_ids
            )
            and not (
                item.get("kind") == "command" and item.get("command", "") in successful_command_text
            )
            and not (
                item.get("kind") == "behavioral_verification"
                and latest_behavioral_status.get(item.get("tool", "")) == "verified"
            )
        ]
        verified_mutations = [item for item in mutation_records if item.get("status") == "verified"]
        passing_behavioral = [
            item
            for item in evidence
            if item.get("kind") == "behavioral_verification" and item.get("status") == "verified"
        ]
        approved_reviews = [
            item
            for item in evidence
            if item.get("kind") == "independent_review" and item.get("status") == "verified"
        ]
        passing_checks = self._validated_passing_checks(verification_records)
        reproduction_evidence = [
            item
            for item in command_records
            if item.get("status") == "failed" or item.get("exit_code") not in (None, 0)
        ] + [
            item for item in evidence
            if item.get("kind") == "verification_check" and item.get("status") == "failed"
        ]

        def matching_checks(criterion: str) -> list[dict[str, Any]]:
            lowered = criterion.lower()
            passing_by_type: dict[str, list[dict[str, Any]]] = {}
            for item in passing_checks:
                check_type = str(item.get("metadata", {}).get("check_type", ""))
                passing_by_type.setdefault(check_type, []).append(item)

            if "executable test" in lowered and "build" in lowered:
                target_types = {"test", "build", "browser", "api", "database"}
            elif "lint" in lowered and "type" in lowered:
                # A build is neither a linter nor a type checker. Combined
                # criteria require one passing record of each exact type.
                if not passing_by_type.get("lint") or not passing_by_type.get("type_check"):
                    return []
                return [*passing_by_type["lint"], *passing_by_type["type_check"]]
            elif "lint" in lowered:
                target_types = {"lint"}
            elif "type" in lowered:
                target_types = {"type_check"}
            elif "security" in lowered or "vulnerab" in lowered:
                target_types = {"security"}
            elif "coverage" in lowered:
                target_types = {"coverage"}
            elif "build" in lowered or "compile" in lowered:
                target_types = {"build"}
            elif "test" in lowered or "regression" in lowered:
                return [
                    item for item in passing_by_type.get("test", [])
                    if (item.get("metadata") or {}).get("project_gate") is True
                    and (item.get("metadata") or {}).get("verification_valid") is True
                    and (item.get("metadata") or {}).get("runner_valid") is True
                ]
            elif "run the project" in lowered or "works" in lowered or "smoke" in lowered:
                target_types = {"test", "build", "browser", "api", "database"}
            else:
                return []
            return [
                item
                for item in passing_checks
                if item.get("metadata", {}).get("check_type") in target_types
            ]

        plan = self._agent._active_plan
        if plan is not None:
            criteria_text = list(plan.acceptance_criteria)
            self._agent.run_ledger.record_plan(plan)
        else:
            verification = self._agent._applicable_verification(
                self._agent._active_analysis.get("intent", IntentType.UNKNOWN),
                self._agent._active_analysis.get("skills_needed", []),
            )
            criteria_text = self._agent.planner._generate_acceptance_criteria(
                self._agent._active_objective,
                self._agent._active_analysis.get("intent", IntentType.UNKNOWN),
                verification,
            )

        results: list[CriterionResult] = []
        for criterion in criteria_text:
            lowered = criterion.lower()
            if "unrelated files" in lowered:
                results.append(self._evaluate_unrelated_files(criterion, plan, changes))
            elif "fingerprinted" in lowered:
                results.append(self._evaluate_fingerprinted_mutations(criterion, evidence))
            elif "requested objective is implemented" in lowered:
                results.append(
                    self._evaluate_objective_implementation(
                        criterion,
                        verified_mutations,
                        passing_checks,
                        passing_behavioral,
                        approved_reviews,
                        successful_command_text,
                    )
                )
            elif "reported failure is reproduced" in lowered:
                results.append(
                    CriterionResult(
                        criterion,
                        CriterionStatus.SATISFIED
                        if reproduction_evidence
                        else CriterionStatus.UNVERIFIED,
                        evidence_ids=[item["id"] for item in reproduction_evidence],
                        detail=(
                            "A failing command or verification check reproduced the defect before repair."
                            if reproduction_evidence
                            else "No failing reproduction evidence was recorded before the fix."
                        ),
                    )
                )
            elif "security" in lowered or "vulnerab" in lowered:
                results.append(
                    self._evaluate_security_constraints(
                        criterion, passing_behavioral, matching_checks(criterion)
                    )
                )
            elif "verification completed" in lowered or any(
                term in lowered
                for term in (
                    "test",
                    "regression",
                    "build",
                    "lint",
                    "type",
                    "coverage",
                    "smoke check",
                )
            ):
                results.append(
                    self._evaluate_verification_checks(criterion, matching_checks(criterion))
                )
            elif failed_evidence:
                results.append(
                    CriterionResult(
                        criterion,
                        CriterionStatus.UNSATISFIED,
                        evidence_ids=[item["id"] for item in failed_evidence],
                        detail="One or more execution evidence records failed.",
                    )
                )
            else:
                results.append(
                    CriterionResult(
                        criterion,
                        CriterionStatus.UNVERIFIED,
                        detail="The run did not record sufficient deterministic evidence.",
                    )
                )

        project_gate_issue = self._project_test_gate_issue(
            changes, passing_checks, current_workspace_revision
        )
        if project_gate_issue:
            results.append(
                CriterionResult(
                    "Configured project test suite passes after the final mutation",
                    CriterionStatus.UNSATISFIED,
                    detail=project_gate_issue,
                )
            )

        # Autonomous execution is iterative: a failed command or edit attempt
        # is not an unresolved run failure when a later call to that tool
        # succeeds and final deterministic verification passes.
        latest_tool_events: dict[str, dict[str, Any]] = {}
        for item in events or []:
            if item.get("type") == "tool_call":
                latest_tool_events[str(item.get("name", "unknown"))] = item
        event_failures = [
            item for item in latest_tool_events.values() if not item.get("success", False)
        ]
        if status_override is not None:
            run_status = status_override
        elif (content or "").strip().upper().startswith("BLOCKED:"):
            run_status = RunStatus.BLOCKED
        elif self._agent._pending_edits or self._agent._pending_confirmations:
            run_status = RunStatus.AWAITING_APPROVAL
        elif failed_evidence or event_failures:
            run_status = (
                RunStatus.PARTIALLY_VERIFIED
                if verified_mutations or passing_checks
                else RunStatus.FAILED
            )
        elif results and all(item.status == CriterionStatus.SATISFIED for item in results):
            run_status = RunStatus.VERIFIED
        elif verified_mutations or passing_checks:
            run_status = RunStatus.PARTIALLY_VERIFIED
        else:
            run_status = RunStatus.UNVERIFIED

        completion_issue = ""
        if run_status == RunStatus.VERIFIED:
            brain = getattr(self._agent, "engineering_brain", None)
            if brain is not None:
                try:
                    assessment = brain.completion_assessment(
                        item.get("filepath", "") for item in changes if item.get("filepath")
                    )
                except (OSError, TypeError, ValueError) as exc:
                    assessment = None
                    completion_issue = f"Completion contract could not be evaluated: {exc}"
                    run_status = RunStatus.PARTIALLY_VERIFIED
                if assessment is not None and not assessment.complete:
                    completion_issue = "Multi-file completion contract incomplete: " + str(assessment.to_dict())
                    run_status = RunStatus.PARTIALLY_VERIFIED
                    self._agent.evidence.append(
                        kind="completion_contract",
                        claim="All repository-scale obligations are complete",
                        status="failed",
                        raw_output=completion_issue,
                        metadata=assessment.to_dict(),
                    )

        workspace_apply_error = ""
        if run_status == RunStatus.VERIFIED and self._agent.mode_policy.may_apply:
            applied, apply_detail = self._apply_verified_workspace()
            if not applied:
                workspace_apply_error = apply_detail
                run_status = RunStatus.FAILED

        checks = [
            {
                "evidence_id": item.get("id"),
                "command": item.get("command", ""),
                "status": item.get("status"),
                "exit_code": item.get("exit_code"),
            }
            for item in evidence
            if item.get("kind") == "command"
        ]
        risks = []
        if run_status != RunStatus.VERIFIED:
            risks.append("Not every acceptance criterion has passing deterministic evidence.")
        if self._agent._pending_edits:
            risks.append(f"{len(self._agent._pending_edits)} file edit(s) still require approval.")
        if self._agent._pending_confirmations:
            risks.append(
                f"{len(self._agent._pending_confirmations)} protected operation(s) still require approval."
            )
        if workspace_apply_error:
            risks.append(workspace_apply_error)
        if completion_issue:
            risks.append(completion_issue)
        if project_gate_issue:
            risks.append(project_gate_issue)

        if run_status == RunStatus.VERIFIED:
            outcome = "COMPLETED_VERIFIED"
        elif run_status == RunStatus.BLOCKED:
            outcome = "BLOCKED_BY_POLICY"
        elif run_status == RunStatus.AWAITING_APPROVAL:
            outcome = "AWAITING_APPROVAL"
        elif run_status == RunStatus.ROLLED_BACK:
            outcome = "ROLLED_BACK"
        elif run_status == RunStatus.FAILED:
            outcome = "FAILED"
        elif changes:
            outcome = "CHANGES_APPLIED_UNVERIFIED"
        elif run_status == RunStatus.PARTIALLY_VERIFIED:
            outcome = "COMPLETED_PARTIALLY_VERIFIED"
        else:
            outcome = "NO_CHANGES"

        turn_dir = self._agent.run_ledger.turn_dir
        model_call_records, _model_call_corruption = self._agent.run_ledger.read_jsonl("model_calls.jsonl")
        logical_model_calls = [
            item for item in model_call_records if item.get("role") != "provider_attempt"
        ]
        provider_attempt_records = [
            item for item in model_call_records if item.get("role") == "provider_attempt"
        ]

        def jsonl_count(filename: str) -> int:
            if turn_dir is None:
                return 0
            try:
                return sum(
                    1
                    for line in (turn_dir / filename).read_text(encoding="utf-8").splitlines()
                    if line.strip()
                )
            except OSError:
                return 0

        def event_kind_count(kind: str) -> int:
            if turn_dir is None:
                return 0
            try:
                records = [
                    json.loads(line)
                    for line in (turn_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
            except (OSError, json.JSONDecodeError):
                return 0
            return sum(item.get("kind") == kind for item in records)

        report = self._agent.run_ledger.finalize(
            run_status,
            objective=self._agent._active_objective,
            outcome=outcome,
            criteria=results,
            files_changed=[item["filepath"] for item in changes],
            checks=checks,
            costs=self._agent.budget.snapshot(),
            risks=risks,
            work_completed=[f"Updated {Path(item['filepath']).name}" for item in changes],
            checks_skipped=[
                item.criterion
                for item in results
                if item.status
                in {
                    CriterionStatus.SKIPPED,
                    CriterionStatus.BLOCKED,
                    CriterionStatus.UNVERIFIED,
                }
            ],
            dependencies_added=sorted(
                {
                    (
                        f"{item.get('metadata', {}).get('registry', 'registry')}:"
                        f"{item.get('metadata', {}).get('name', 'unknown')}"
                    )
                    for item in evidence
                    if item.get("kind") == "package_registry"
                    and item.get("status") in {"pass", "warn"}
                }
            ),
            permissions_used=sorted(self._agent._permissions_used),
            network_calls=list(dict.fromkeys(self._agent._network_calls)),
            model_providers=list(
                dict.fromkeys(
                    [
                        self._agent.model_key,
                        *(
                            [self._agent.model_cfg.get("intern_model", "nova_codex")]
                            if self._agent.routing_stats["nova_tasks"]
                            else []
                        ),
                    ]
                )
            ),
            assumptions=[],
            metadata={
                "model": self._agent.model_key,
                "response_excerpt": _redact_runtime_text((content or "")[:2000]),
                "evidence_path": str(self._agent.evidence.path),
                "workspace": self._agent.working_dir,
                "history_start": self._agent._run_history_start,
                "history_end": len(self._agent.history.changes),
                "local_intern_mode": self._agent.local_intern_mode,
                "local_intern_enabled": self._agent.local_intern_enabled,
                "plugins_enabled": self._agent._plugins_enabled,
                "model_calls": len(logical_model_calls),
                "provider_attempts": len(provider_attempt_records),
                "tool_calls": jsonl_count("tool_calls.jsonl"),
                "tests_executed": len(verification_records),
                "workspace_revision": current_workspace_revision,
                "project_test_gate_passed": not bool(project_gate_issue),
                "criteria_satisfied": sum(
                    item.status == CriterionStatus.SATISFIED for item in results
                ),
                "criteria_unverified": sum(
                    item.status == CriterionStatus.UNVERIFIED for item in results
                ),
                "rollbacks": event_kind_count("rollback"),
                "workspace_applied": self._agent._workspace_applied,
                "workspace_apply_detail": self._agent._workspace_apply_detail,
                "review_assurance": (
                    "independent_semantic"
                    if approved_reviews
                    else ("deterministic_only" if self._agent._is_nova_model() else "none")
                ),
            },
        )
        return report

    def get_run_status(self) -> str:
        """Return the latest durable run and workspace status."""
        summary = self._agent.run_ledger.resume_summary()
        if not summary:
            return "No durable run exists for this session."
        state = summary.get("state", {})
        report = summary.get("final_report", {})
        lines = [
            f"Run: {state.get('turn_id', 'unknown')}",
            f"Status: {report.get('status') or state.get('status', 'unknown')}",
            f"Objective: {report.get('objective') or summary.get('request', {}).get('request', '')}",
            f"Run directory: {self._agent.run_ledger._latest_turn_dir()}",
        ]
        if self._agent.worktree:
            worktree_status = self._agent.worktree.status()
            lines.extend(
                [
                    f"Worktree: {worktree_status.get('path', self._agent.working_dir)}",
                    f"Branch: {worktree_status.get('branch', '')}",
                    worktree_status.get("git_status", ""),
                ]
            )
        checkpoint = summary.get("checkpoint", {})
        if checkpoint:
            lines.append(
                f"Latest checkpoint: {checkpoint.get('checkpoint')} {checkpoint.get('label', '')}"
            )
        return "\n".join(item for item in lines if item)

    def rollback_current_run(self) -> tuple[bool, str]:
        """Atomically roll back every file operation recorded by this run."""
        change_count = len(self._agent.history.changes) - self._agent._run_history_start
        if change_count <= 0:
            return False, "The current run has no applied file changes to roll back."
        success, detail = self._agent.history.undo_changes(change_count)
        if success:
            self._agent.run_ledger.mark_rolled_back(detail)
            try:
                self._agent.repo_graph.build()
            except (OSError, ValueError) as exc:
                logger.debug("Repository graph refresh after rollback failed: %s", exc)
        return success, detail

    def _refresh_final_report_after_approval(self) -> None:
        """Recompute the final status after an approval queue changes."""
        if not self._agent.run_ledger.turn_dir or not self._agent._active_objective:
            return
        prior = self._agent.run_ledger.resume_summary().get("final_report", {})
        content = prior.get("metadata", {}).get("response_excerpt", "")
        self._agent._run_finalizer.finish(content, [])

    # ──────────────────────────────────────────────────────────────────────────
    # Evidence helpers (independently testable)
    # ──────────────────────────────────────────────────────────────────────────

    @staticmethod
    def classify_evidence(records: list[dict[str, Any]]) -> EvidenceSummary:
        """
        Partition a flat list of evidence records into the structured summary.

        This is a pure function — it does not touch disk or agent state.
        """
        summary = EvidenceSummary()

        # Track latest status per mutation/verification ID so stale failed
        # attempts don't poison the report.
        latest_by_id: dict[str, str] = {}
        for item in records:
            kind = item.get("kind", "")
            if kind in (EvidenceClass.MUTATION, EvidenceClass.VERIFICATION):
                rid = str(item.get("id", ""))
                if rid:
                    latest_by_id[rid] = item.get("status", "")

        effective_ids = {
            rid for rid, status in latest_by_id.items() if status == "verified"
        }

        # Latest behavioral status per tool
        latest_behavioral: dict[str, str] = {
            item.get("tool", ""): item.get("status", "")
            for item in records
            if item.get("kind") == EvidenceClass.BEHAVIORAL
        }

        # Passing command set (for deduplication)
        passing_cmd_text: set[str] = set()

        # Deduplicate success records
        mutations_dict: dict[str, dict[str, Any]] = {}
        verifications_dict: dict[str, dict[str, Any]] = {}
        commands_dict: dict[str, dict[str, Any]] = {}
        behavioral_dict: dict[str, dict[str, Any]] = {}

        for item in records:
            kind = item.get("kind", "")
            status = item.get("status", "")
            rid = str(item.get("id", ""))

            if kind == EvidenceClass.MUTATION and rid in effective_ids and status == "verified":
                mutations_dict[rid] = item
            elif kind == EvidenceClass.VERIFICATION and rid in effective_ids and status == "verified":
                verifications_dict[rid] = item
            elif kind == EvidenceClass.COMMAND and status == "verified" and item.get("exit_code") == 0:
                cmd_text = item.get("command", "")
                commands_dict[cmd_text] = item
                passing_cmd_text.add(cmd_text)
            elif kind == EvidenceClass.BEHAVIORAL and status == "verified":
                behavioral_dict[item.get("tool", "")] = item
            elif kind == EvidenceClass.REVIEW and status == "verified":
                summary.approved_reviews.append(item)

        summary.verified_mutations.extend(mutations_dict.values())
        summary.passing_checks.extend(verifications_dict.values())
        summary.passing_commands.extend(commands_dict.values())
        summary.passing_behavioral.extend(behavioral_dict.values())

        # Failed evidence (exclude stale retried items that later succeeded)
        for item in records:
            kind = item.get("kind", "")
            status = item.get("status", "")
            rid = str(item.get("id", ""))

            if status != "failed":
                continue
            if kind in ("routing", EvidenceClass.REVIEW):
                continue
            if kind in (EvidenceClass.MUTATION, EvidenceClass.VERIFICATION):
                if rid in effective_ids:
                    continue  # Later attempt succeeded
            if kind == EvidenceClass.COMMAND and item.get("command", "") in passing_cmd_text:
                continue
            if kind == EvidenceClass.BEHAVIORAL:
                if latest_behavioral.get(item.get("tool", "")) == "verified":
                    continue
            summary.failed_evidence.append(item)

        # Reproduction evidence (failed commands + failed verification checks)
        for item in records:
            kind = item.get("kind", "")
            if kind == EvidenceClass.COMMAND and (
                item.get("status") == "failed" or item.get("exit_code") not in (None, 0)
            ):
                summary.reproduction_evidence.append(item)
            elif kind == EvidenceClass.VERIFICATION and item.get("status") == "failed":
                summary.reproduction_evidence.append(item)

        return summary

    @staticmethod
    def determine_status(
        summary: EvidenceSummary,
        *,
        awaiting_approval: bool = False,
        blocked: bool = False,
    ) -> str:
        """
        Map an EvidenceSummary to a RunStatus string.

        This is a pure function — no side effects.
        """
        if blocked:
            return "BLOCKED"
        if awaiting_approval:
            return "AWAITING_APPROVAL"
        if not summary.has_any_success and summary.has_failures:
            return "FAILED"
        if summary.has_failures:
            return "PARTIALLY_VERIFIED"
        if summary.has_any_success:
            return "VERIFIED"
        return "UNVERIFIED"


# ─── Convenience factory ─────────────────────────────────────────────────────


def make_finalizer(agent: "Agent") -> RunFinalizer:
    """Create and attach a ``RunFinalizer`` to *agent*."""
    finalizer = RunFinalizer(agent)
    agent._run_finalizer = finalizer  # type: ignore[attr-defined]
    return finalizer
