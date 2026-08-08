"""
Evidence-Based Model Escalation & Failure Attribution Engine for Nexus CLI.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from nexus.model_doctor import CapabilityDimension
from nexus.models import ModelTier, model_registry


class AttributionClass(str, Enum):
    MODEL_CAPABILITY_MISMATCH = "MODEL_CAPABILITY_MISMATCH"
    PROMPT_FORMATTING = "PROMPT_FORMATTING"
    CONTEXT_LIMIT_EXCEEDED = "CONTEXT_LIMIT_EXCEEDED"
    ENVIRONMENT_FAILURE = "ENVIRONMENT_FAILURE"
    TOOL_EXECUTION_FAILURE = "TOOL_EXECUTION_FAILURE"
    SYSTEM_SANDBOX_FAILURE = "SYSTEM_SANDBOX_FAILURE"
    UNKNOWN = "UNKNOWN"


@dataclass
class ModelFailureAttribution:
    failure_id: str
    model_id: str
    capability_dimension: CapabilityDimension | None
    attribution: AttributionClass
    confidence: float
    supporting_evidence: list[str] = field(default_factory=list)
    alternative_causes: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_id": self.failure_id,
            "model_id": self.model_id,
            "capability_dimension": self.capability_dimension.value if self.capability_dimension else None,
            "attribution": self.attribution.value,
            "confidence": round(self.confidence, 3),
            "supporting_evidence": self.supporting_evidence,
            "alternative_causes": self.alternative_causes,
            "timestamp": self.timestamp,
        }


@dataclass
class EscalationDecision:
    escalation_id: str
    current_model: str
    failed_capability: CapabilityDimension | None
    attempts: int
    target_model: str | None
    target_model_key: str | None
    expected_cost_increase_usd: float
    remaining_budget_usd: float
    privacy_impact: str
    approval_required: bool
    escalation_approved: bool
    reason: str
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "escalation_id": self.escalation_id,
            "current_model": self.current_model,
            "failed_capability": self.failed_capability.value if self.failed_capability else None,
            "attempts": self.attempts,
            "target_model": self.target_model,
            "target_model_key": self.target_model_key,
            "expected_cost_increase_usd": round(self.expected_cost_increase_usd, 6),
            "remaining_budget_usd": round(self.remaining_budget_usd, 6),
            "privacy_impact": self.privacy_impact,
            "approval_required": self.approval_required,
            "escalation_approved": self.escalation_approved,
            "reason": self.reason,
            "timestamp": self.timestamp,
        }


class EscalationController:
    """Manages failure attribution and evidence-backed model escalation."""

    def __init__(self) -> None:
        self.history: list[EscalationDecision] = []

    def attribute_failure(
        self,
        raw_error: str,
        failure_kind: str,
        current_model_key: str,
        attempt_number: int = 1,
        context_correct: bool = True,
        tools_available: bool = True,
    ) -> ModelFailureAttribution:
        """Classify whether a failure is a genuine model capability mismatch vs environment/tool failure."""
        norm = (raw_error or "").lower()
        fail_kind_clean = (failure_kind or "").lower()

        # Non-model failures: Environment / Tool / Sandbox
        if any(w in norm for w in ("command not found", "connection refused", "eacces", "permission denied", "no such file or directory", "sh: ", "exec format error")):
            return ModelFailureAttribution(
                failure_id=f"attr-{hash(time.time()) & 0xFFFFFFFF:08x}",
                model_id=current_model_key,
                capability_dimension=None,
                attribution=AttributionClass.ENVIRONMENT_FAILURE,
                confidence=0.95,
                supporting_evidence=[f"Environment error detected in raw log: {raw_error[:100]}"],
                alternative_causes=["Tool environment path mismatch", "Missing executable dependency"],
            )

        if not tools_available or "tool execution failed" in norm:
            return ModelFailureAttribution(
                failure_id=f"attr-{hash(time.time()) & 0xFFFFFFFF:08x}",
                model_id=current_model_key,
                capability_dimension=CapabilityDimension.TOOL_ARGUMENTS,
                attribution=AttributionClass.TOOL_EXECUTION_FAILURE,
                confidence=0.85,
                supporting_evidence=["Tool execution failed due to system/runner error"],
                alternative_causes=["Tool argument malformed", "Executable error"],
            )

        # Genuine Model Capability Failures
        if "jsondecodeerror" in norm or "expecting value" in norm or "malformed json" in norm:
            return ModelFailureAttribution(
                failure_id=f"attr-{hash(time.time()) & 0xFFFFFFFF:08x}",
                model_id=current_model_key,
                capability_dimension=CapabilityDimension.STRUCTURED_OUTPUT,
                attribution=AttributionClass.MODEL_CAPABILITY_MISMATCH,
                confidence=0.90,
                supporting_evidence=["Repeated invalid JSON output generated by model"],
                alternative_causes=["Prompt formatting strictness"],
            )

        if "hunk failed" in norm or "patch conflict" in norm or "corrupt patch" in norm:
            if context_correct:
                return ModelFailureAttribution(
                    failure_id=f"attr-{hash(time.time()) & 0xFFFFFFFF:08x}",
                    model_id=current_model_key,
                    capability_dimension=CapabilityDimension.PATCH_VALIDITY,
                    attribution=AttributionClass.MODEL_CAPABILITY_MISMATCH,
                    confidence=0.85,
                    supporting_evidence=["Patch generation failed despite accurate context bundle"],
                    alternative_causes=["Stale repository state"],
                )

        if "multi_file" in fail_kind_clean or "missed caller" in norm:
            return ModelFailureAttribution(
                failure_id=f"attr-{hash(time.time()) & 0xFFFFFFFF:08x}",
                model_id=current_model_key,
                capability_dimension=CapabilityDimension.MULTI_FILE_REASONING,
                attribution=AttributionClass.MODEL_CAPABILITY_MISMATCH,
                confidence=0.85,
                supporting_evidence=["Model failed to maintain cross-file interface consistency"],
                alternative_causes=["Incomplete context selection"],
            )

        # Default fallback
        return ModelFailureAttribution(
            failure_id=f"attr-{hash(time.time()) & 0xFFFFFFFF:08x}",
            model_id=current_model_key,
            capability_dimension=CapabilityDimension.SINGLE_FILE_REPAIR,
            attribution=AttributionClass.MODEL_CAPABILITY_MISMATCH,
            confidence=0.70,
            supporting_evidence=[f"Repeated task failure under {current_model_key}"],
            alternative_causes=["Ambiguous prompt", "Environment constraint"],
        )

    def evaluate_escalation(
        self,
        attribution: ModelFailureAttribution,
        current_model_key: str,
        attempts: int,
        remaining_budget_usd: float,
        ask_before_frontier: bool = True,
        max_escalations: int = 2,
    ) -> EscalationDecision:
        """Evaluate if model escalation is justified by failure attribution and allowed by budget."""
        # Non-model failures MUST NOT trigger escalation
        if attribution.attribution != AttributionClass.MODEL_CAPABILITY_MISMATCH:
            return EscalationDecision(
                escalation_id=f"esc-{hash(time.time()) & 0xFFFFFFFF:08x}",
                current_model=current_model_key,
                failed_capability=attribution.capability_dimension,
                attempts=attempts,
                target_model=None,
                target_model_key=None,
                expected_cost_increase_usd=0.0,
                remaining_budget_usd=remaining_budget_usd,
                privacy_impact="none",
                approval_required=False,
                escalation_approved=False,
                reason=f"Escalation denied: failure attribution is {attribution.attribution.value}, not a model capability mismatch.",
            )

        current_desc = model_registry.get_descriptor(current_model_key)
        all_descs = model_registry.list_all()

        # Find next higher tier model
        next_candidates = [
            d for d in all_descs
            if d.enabled and d.model_id != getattr(current_desc, "model_id", "")
        ]

        if getattr(current_desc, "tier", None) == ModelTier.LOCAL:
            target_candidates = [d for d in next_candidates if d.tier in (ModelTier.AFFORDABLE, ModelTier.STRONG)]
        elif getattr(current_desc, "tier", None) == ModelTier.AFFORDABLE:
            target_candidates = [d for d in next_candidates if d.tier in (ModelTier.STRONG, ModelTier.FRONTIER)]
        else:
            target_candidates = [d for d in next_candidates if d.tier == ModelTier.FRONTIER]

        if not target_candidates:
            target_candidates = next_candidates

        if not target_candidates:
            return EscalationDecision(
                escalation_id=f"esc-{hash(time.time()) & 0xFFFFFFFF:08x}",
                current_model=current_model_key,
                failed_capability=attribution.capability_dimension,
                attempts=attempts,
                target_model=None,
                target_model_key=None,
                expected_cost_increase_usd=0.0,
                remaining_budget_usd=remaining_budget_usd,
                privacy_impact="none",
                approval_required=False,
                escalation_approved=False,
                reason="Escalation denied: no higher model tier available in registry.",
            )

        target_desc = target_candidates[0]
        target_key = model_registry.resolve_key(target_desc.model_id) or target_desc.model_id

        curr_cost = ((current_desc.input_cost or 0.0) * 2000 + (current_desc.output_cost or 0.0) * 500) / 1_000_000 if current_desc else 0.0
        target_cost = ((target_desc.input_cost or 0.0) * 2000 + (target_desc.output_cost or 0.0) * 500) / 1_000_000
        cost_increase = max(0.0, target_cost - curr_cost)

        if cost_increase > remaining_budget_usd:
            return EscalationDecision(
                escalation_id=f"esc-{hash(time.time()) & 0xFFFFFFFF:08x}",
                current_model=current_model_key,
                failed_capability=attribution.capability_dimension,
                attempts=attempts,
                target_model=target_desc.display_name,
                target_model_key=target_key,
                expected_cost_increase_usd=cost_increase,
                remaining_budget_usd=remaining_budget_usd,
                privacy_impact="none",
                approval_required=False,
                escalation_approved=False,
                reason=f"Escalation denied: required cost increase (${cost_increase:.4f}) exceeds remaining budget (${remaining_budget_usd:.4f}).",
            )

        approval_req = target_desc.tier == ModelTier.FRONTIER and ask_before_frontier
        privacy_note = "remote cloud model selected" if not target_desc.local else "local model selected"

        decision = EscalationDecision(
            escalation_id=f"esc-{hash(time.time()) & 0xFFFFFFFF:08x}",
            current_model=current_model_key,
            failed_capability=attribution.capability_dimension,
            attempts=attempts,
            target_model=target_desc.display_name,
            target_model_key=target_key,
            expected_cost_increase_usd=cost_increase,
            remaining_budget_usd=remaining_budget_usd,
            privacy_impact=privacy_note,
            approval_required=approval_req,
            escalation_approved=not approval_req,
            reason=f"Escalation justified: model failed capability '{attribution.capability_dimension.value if attribution.capability_dimension else 'general'}' {attempts} times.",
        )

        self.history.append(decision)
        return decision


# Global EscalationController singleton
escalation_controller = EscalationController()
