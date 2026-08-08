"""
Diagnosis Engine for Nexus CLI Recovery Subsystem.
Consumes failure records, context, plan, and attempt history to produce structured FailureDiagnosis.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from nexus.recovery.records import (
    FailureCategory,
    FailureDiagnosis,
    FailureHypothesis,
    FailureKind,
    FailureRecord,
    HypothesisStatus,
)

logger = logging.getLogger(__name__)


class DiagnosisEngine:
    """Canonical diagnosis stage producing evidence-linked hypotheses and recovery recommendations."""

    @classmethod
    def diagnose(
        cls,
        failure: FailureRecord,
        context: dict | None = None,
        *,
        plan_version: int = 1,
        mutations: list[dict] | None = None,
        previous_attempts: list[dict] | None = None,
        context_bundle: str = "",
        baseline_failures: list[str] | None = None,
        **kwargs: Any,
    ) -> FailureDiagnosis:
        muts = mutations or []
        prevs = previous_attempts or []
        base_fails = baseline_failures or []

        hypotheses: list[FailureHypothesis] = []
        rejected: list[FailureHypothesis] = []
        missing_evidence: list[str] = []

        rollback_req = False
        replan_req = False
        context_exp_req = False
        model_esc_rec = False
        strategy = "RETRY_TRANSIENT"

        # Diagnose by failure category & kind
        if failure.category == FailureCategory.TOOL_EXECUTION:
            if failure.kind == FailureKind.EXECUTABLE_NOT_FOUND:
                hypotheses.append(
                    FailureHypothesis(
                        hypothesis_id="hyp-001",
                        statement=f"Executable '{failure.command.split()[0] if failure.command else 'tool'}' is missing in environment.",
                        confidence=0.95,
                        cheap_check="Verify command location or install package",
                        status=HypothesisStatus.SUPPORTED,
                    )
                )
                strategy = "STOP_BLOCKED"
            elif failure.kind == FailureKind.PERMISSION_DENIED:
                hypotheses.append(
                    FailureHypothesis(
                        hypothesis_id="hyp-002",
                        statement="Operation blocked due to missing filesystem/system permission.",
                        confidence=0.9,
                        cheap_check="Check file/directory permissions or request approval",
                        status=HypothesisStatus.SUPPORTED,
                    )
                )
                strategy = "REQUEST_MISSING_PERMISSION"
            elif failure.kind == FailureKind.COMMAND_TIMEOUT:
                hypotheses.append(
                    FailureHypothesis(
                        hypothesis_id="hyp-003",
                        statement="Command execution timed out during test/build execution.",
                        confidence=0.8,
                        cheap_check="Run targeted narrower test or increase timeout limit",
                        status=HypothesisStatus.SUPPORTED,
                    )
                )
                strategy = "REPRODUCE_FAILURE_DIFFERENTLY"

        elif failure.category == FailureCategory.MUTATION:
            if failure.kind in (FailureKind.PATCH_CONFLICT, FailureKind.WORKSPACE_CORRUPTION, FailureKind.OUT_OF_SCOPE_MUTATION):
                hypotheses.append(
                    FailureHypothesis(
                        hypothesis_id="hyp-004",
                        statement="Patch conflict or workspace corruption occurred during mutation.",
                        confidence=0.9,
                        cheap_check="Reread file content and hash; revert invalid patch",
                        status=HypothesisStatus.SUPPORTED,
                    )
                )
                rollback_req = True
                strategy = "ROLLBACK_TO_CHECKPOINT"

        elif failure.category == FailureCategory.VERIFICATION:
            if failure.failing_tests:
                test_name = failure.failing_tests[0]
                if test_name in base_fails:
                    hypotheses.append(
                        FailureHypothesis(
                            hypothesis_id="hyp-005",
                            statement=f"Failing test '{test_name}' was inherited from repository baseline.",
                            confidence=0.85,
                            cheap_check="Check baseline test results prior to task",
                            status=HypothesisStatus.SUPPORTED,
                        )
                    )
                    strategy = "REDUCE_SCOPE"
                else:
                    hypotheses.append(
                        FailureHypothesis(
                            hypothesis_id="hyp-006",
                            statement="Latest code patch broke test assertions or introduced a regression.",
                            confidence=0.8,
                            cheap_check="Inspect exact assertion diff and changed symbols",
                            status=HypothesisStatus.SUPPORTED,
                        )
                    )
                    strategy = "APPLY_SMALLER_PATCH"
                    if len(prevs) >= 2:
                        replan_req = True
                        strategy = "REVISE_PLAN"

            elif failure.kind == FailureKind.TYPE_CHECK_FAILED or failure.kind == FailureKind.BUILD_FAILED:
                hypotheses.append(
                    FailureHypothesis(
                        hypothesis_id="hyp-007",
                        statement="Type signature or interface mismatch introduced by modification.",
                        confidence=0.85,
                        cheap_check="Inspect caller functions and function definitions",
                        status=HypothesisStatus.SUPPORTED,
                    )
                )
                context_exp_req = True
                strategy = "EXPAND_CONTEXT"

        elif failure.category == FailureCategory.MODEL:
            if failure.attempt_number >= 2:
                model_esc_rec = True
                strategy = "SWITCH_MODEL"

        # General loop detection trigger
        if len(prevs) >= 3:
            replan_req = True
            model_esc_rec = True

        diagnosis_id = f"diag-{hash(failure.failure_id + str(len(prevs))) & 0xFFFFFFFF:08x}"

        return FailureDiagnosis(
            diagnosis_id=diagnosis_id,
            primary_failure=failure,
            likely_root_causes=hypotheses,
            rejected_causes=rejected,
            missing_evidence=missing_evidence,
            recommended_strategy=strategy,
            rollback_required=rollback_req,
            replan_required=replan_req,
            context_expansion_required=context_exp_req,
            model_escalation_recommended=model_esc_rec,
            confidence=0.85 if hypotheses else 0.4,
            limitations=["Diagnosis based on normalized output and attempt history."],
            created_at=datetime.now(timezone.utc).isoformat(),
        )
