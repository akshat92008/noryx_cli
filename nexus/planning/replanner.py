"""Evidence-directed, versioned plan revision.

A revision must change the investigation or execution structure.  Appending a
failure message to an objective is not considered replanning.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple

from nexus.planning.engineering_plan import (
    ActionType,
    EngineeringPlan,
    EvidenceReference,
    Hypothesis,
    HypothesisStatus,
    PlanStep,
)
from nexus.planning.task_contract import RiskLevel


class PlanReplanner:
    """Create bounded, evidence-backed structural plan revisions."""

    def __init__(self, max_revisions: int = 5):
        self.max_revisions = max(1, int(max_revisions))
        self.plan_history: Dict[int, EngineeringPlan] = {}
        self.signature_history: List[str] = []
        self.evidence_history: List[str] = []

    def compute_plan_signature(self, plan: EngineeringPlan) -> str:
        payload = {
            "objective": plan.objective,
            "scope": sorted(plan.affected_scope),
            "hypotheses": [
                {
                    "statement": item.statement,
                    "status": getattr(item.status, "value", item.status),
                    "validation": item.validation_action,
                }
                for item in plan.root_cause_hypotheses
            ],
            "steps": [
                {
                    "id": step.step_id,
                    "title": step.title,
                    "action": getattr(step.action_type, "value", step.action_type),
                    "objective": step.objective,
                    "targets": sorted(step.intended_targets),
                    "dependencies": sorted(step.dependencies),
                    "verification": step.verification_method,
                }
                for step in plan.steps
            ],
            "verification": plan.verification_strategy,
        }
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    @staticmethod
    def _evidence_signature(trigger_reason: str, evidence: Dict[str, Any]) -> str:
        payload = {"reason": " ".join(trigger_reason.lower().split()), "evidence": evidence}
        return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()

    @staticmethod
    def _paths(evidence: Dict[str, Any]) -> list[str]:
        values: list[str] = []
        for key in (
            "context_files",
            "additional_files",
            "missing_files",
            "failing_stack_files",
            "required_changes",
            "affected_files",
        ):
            raw = evidence.get(key, []) or []
            if isinstance(raw, str):
                raw = [raw]
            values.extend(str(item).replace("\\", "/") for item in raw if str(item).strip())
        return list(dict.fromkeys(values))

    @staticmethod
    def _tests(evidence: Dict[str, Any]) -> list[str]:
        raw = evidence.get("failing_tests", []) or evidence.get("tests", []) or []
        if isinstance(raw, str):
            raw = [raw]
        return list(dict.fromkeys(str(item).split("::", 1)[0] for item in raw if str(item).strip()))

    @staticmethod
    def _symbols(evidence: Dict[str, Any]) -> list[str]:
        raw = evidence.get("symbols", []) or evidence.get("unresolved_symbols", []) or []
        if isinstance(raw, str):
            raw = [raw]
        return list(dict.fromkeys(str(item) for item in raw if str(item).strip()))

    @staticmethod
    def _next_step_id(plan: EngineeringPlan, revision: int, suffix: str) -> str:
        existing = {step.step_id for step in plan.steps}
        base = f"rev-{revision}-{suffix}"
        candidate = base
        index = 2
        while candidate in existing:
            candidate = f"{base}-{index}"
            index += 1
        return candidate

    def revise_plan(
        self,
        current_plan: EngineeringPlan,
        trigger_reason: str,
        failed_step_id: Optional[str] = None,
        new_evidence: Optional[Dict[str, Any]] = None,
    ) -> Tuple[EngineeringPlan, bool]:
        """Return a structurally revised plan or reject duplicate/empty replans."""
        if current_plan.version >= self.max_revisions:
            return current_plan, False

        evidence = dict(new_evidence or {})
        evidence_sig = self._evidence_signature(trigger_reason, evidence)
        if evidence_sig in self.evidence_history:
            return current_plan, False

        current_sig = self.compute_plan_signature(current_plan)
        if current_sig not in self.signature_history:
            self.signature_history.append(current_sig)

        revised = copy.deepcopy(current_plan)
        revised.version += 1
        paths = self._paths(evidence)
        tests = self._tests(evidence)
        symbols = self._symbols(evidence)
        contradiction = bool(
            evidence.get("hypothesis_contradicted")
            or evidence.get("root_cause_invalidated")
            or evidence.get("contradicting_evidence")
        )

        evidence_ref = EvidenceReference(
            source_type=str(evidence.get("source_type", "runtime_failure")),
            reference_id=evidence_sig[:16],
            summary=(str(evidence.get("summary") or trigger_reason))[:1000],
        )

        failed_step = next((step for step in revised.steps if step.step_id == failed_step_id), None)
        insertion_index = revised.steps.index(failed_step) if failed_step is not None else 0
        revision_id = revised.version

        if contradiction:
            for hypothesis in revised.root_cause_hypotheses:
                if hypothesis.status in {HypothesisStatus.PROPOSED, HypothesisStatus.SUPPORTED}:
                    hypothesis.status = HypothesisStatus.CONTRADICTED
                    hypothesis.contradicting_evidence.append(evidence_ref)
            revised.root_cause_hypotheses.append(
                Hypothesis(
                    hypothesis_id=f"hyp-rev-{revision_id}",
                    statement=str(
                        evidence.get("replacement_hypothesis")
                        or "The prior root-cause hypothesis is incomplete; the new failure evidence points to an indirect dependency or violated contract."
                    ),
                    supporting_evidence=[evidence_ref],
                    confidence=float(evidence.get("replacement_confidence", 0.55)),
                    validation_action=(
                        "Inspect the newly evidenced files/symbols, reproduce the failure through the canonical call path, "
                        "and reject this hypothesis unless targeted verification changes."
                    ),
                )
            )

        investigation_targets = list(dict.fromkeys([*paths, *tests]))
        if investigation_targets or symbols or contradiction or failed_step is not None:
            inspect_id = self._next_step_id(revised, revision_id, "evidence-inspection")
            inspect_step = PlanStep(
                step_id=inspect_id,
                title="Investigate new failure evidence",
                objective=(
                    f"Invalidate or confirm the revised root cause after: {trigger_reason[:300]}. "
                    f"Symbols: {', '.join(symbols) or 'none explicitly identified'}."
                ),
                action_type=ActionType.ANALYZE,
                dependencies=list(failed_step.dependencies if failed_step else []),
                evidence_inputs=[evidence_ref],
                intended_targets=investigation_targets,
                allowed_tools=["read_file", "search_files", "find_references", "run_process"],
                mutation_scope=[],
                risk_level=RiskLevel.MEDIUM,
                expected_outcome="A falsifiable root cause linked to concrete repository evidence.",
                completion_condition="At least one hypothesis is supported or contradicted by reproduced evidence.",
                verification_method=str(evidence.get("reproduction_command", "targeted reproduction or failing test")),
                parallelizable=False,
            )
            revised.steps.insert(insertion_index, inspect_step)
            if failed_step is not None:
                failed_step.dependencies = [inspect_id]
                failed_step.evidence_inputs.append(evidence_ref)
                failed_step.intended_targets = list(dict.fromkeys([*failed_step.intended_targets, *paths]))
                failed_step.mutation_scope = list(dict.fromkeys([*failed_step.mutation_scope, *paths]))
                failed_step.objective = (
                    f"{failed_step.objective} Revise implementation using evidence {evidence_sig[:12]}; "
                    "do not repeat the previous patch unchanged."
                )

        verification_commands = evidence.get("verification_commands", []) or []
        if isinstance(verification_commands, str):
            verification_commands = [verification_commands]
        if tests or verification_commands:
            verify_id = self._next_step_id(revised, revision_id, "targeted-verification")
            dependency = failed_step.step_id if failed_step is not None else (
                revised.steps[-1].step_id if revised.steps else ""
            )
            verify_method = " && ".join(str(item) for item in verification_commands if str(item).strip())
            if not verify_method and tests:
                verify_method = "python -m pytest " + " ".join(tests)
            revised.steps.append(
                PlanStep(
                    step_id=verify_id,
                    title="Verify revised causal hypothesis",
                    objective="Run the narrow failing checks first, then the impacted regression set.",
                    action_type=ActionType.VERIFY,
                    dependencies=[dependency] if dependency else [],
                    evidence_inputs=[evidence_ref],
                    intended_targets=tests,
                    allowed_tools=["run_process"],
                    risk_level=RiskLevel.LOW,
                    expected_outcome="The original failure is removed without introducing impacted-test regressions.",
                    completion_condition="Targeted and impacted checks pass with recorded exit-code evidence.",
                    verification_method=verify_method,
                )
            )
            revised.verification_strategy = {
                **dict(revised.verification_strategy),
                "targeted_commands": list(verification_commands),
                "failing_tests": tests,
                "evidence_revision": evidence_sig[:16],
            }

        revised.affected_scope = list(dict.fromkeys([*revised.affected_scope, *paths]))
        revised.limitations.append(f"Plan revised from evidence {evidence_sig[:16]}: {trigger_reason[:500]}")
        revised.confidence = max(0.2, min(0.95, revised.confidence - (0.12 if contradiction else 0.05)))

        new_sig = self.compute_plan_signature(revised)
        if new_sig == current_sig or new_sig in self.signature_history:
            return current_plan, False

        self.plan_history[current_plan.version] = copy.deepcopy(current_plan)
        self.signature_history.append(new_sig)
        self.evidence_history.append(evidence_sig)
        return revised, True
