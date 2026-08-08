"""
Adaptive Model Router — Task-Specific Capability Matching, Portfolio Modes and Phase Routing.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from nexus.model_doctor import CapabilityBand, CapabilityDimension, CapabilityProfile, model_doctor
from nexus.models import ModelDescriptor, ModelTier, PrivacyClass, model_registry


class PortfolioMode(str, Enum):
    CHEAPEST = "CHEAPEST"
    PRIVATE = "PRIVATE"
    FASTEST = "FASTEST"
    BALANCED = "BALANCED"
    STRONGEST = "STRONGEST"
    MANUAL = "MANUAL"


class EngineeringPhase(str, Enum):
    REPOS_SUMMARY = "REPOS_SUMMARY"
    CLASSIFICATION = "CLASSIFICATION"
    PLANNING = "PLANNING"
    PLAN_CRITICISM = "PLAN_CRITICISM"
    CODE_EDIT = "CODE_EDIT"
    DEBUGGING = "DEBUGGING"
    RECOVERY = "RECOVERY"
    VERIFICATION = "VERIFICATION"
    DOCUMENTATION = "DOCUMENTATION"


@dataclass
class TaskCapabilityRequirements:
    task_type: str
    phase: EngineeringPhase
    minimum_capabilities: dict[CapabilityDimension, float] = field(default_factory=dict)
    required_features: set[str] = field(default_factory=set)
    preferred_privacy: PrivacyClass = PrivacyClass.APPROVED_CLOUD
    maximum_latency_ms: float | None = None
    risk_level: str = "medium"  # low, medium, high, critical
    context_required: int = 32000
    structured_output_required: bool = True
    tool_use_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_type": self.task_type,
            "phase": self.phase.value,
            "minimum_capabilities": {k.value: v for k, v in self.minimum_capabilities.items()},
            "required_features": sorted(list(self.required_features)),
            "preferred_privacy": self.preferred_privacy.value,
            "maximum_latency_ms": self.maximum_latency_ms,
            "risk_level": self.risk_level,
            "context_required": self.context_required,
            "structured_output_required": self.structured_output_required,
            "tool_use_required": self.tool_use_required,
        }


@dataclass
class RoutingDecision:
    decision_id: str
    task_phase: EngineeringPhase
    selected_model: str
    selected_model_key: str
    alternatives: list[str]
    reasons: list[str]
    expected_cost_usd: float
    expected_latency_ms: float | None
    capability_confidence: float
    policy_constraints: list[str]
    escalation_allowed: bool = True
    approval_required: bool = False
    meets_requirements: bool = True
    capability_gaps: list[str] = field(default_factory=list)
    evaluation_source: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "task_phase": self.task_phase.value,
            "selected_model": self.selected_model,
            "selected_model_key": self.selected_model_key,
            "alternatives": self.alternatives,
            "reasons": self.reasons,
            "expected_cost_usd": round(self.expected_cost_usd, 6),
            "expected_latency_ms": self.expected_latency_ms,
            "capability_confidence": round(self.capability_confidence, 3),
            "policy_constraints": self.policy_constraints,
            "escalation_allowed": self.escalation_allowed,
            "approval_required": self.approval_required,
            "meets_requirements": self.meets_requirements,
            "capability_gaps": self.capability_gaps,
            "evaluation_source": self.evaluation_source,
            "timestamp": self.timestamp,
        }


class ModelRouter:
    """Authoritative Adaptive Model Router for Noryx CLI."""

    def __init__(self) -> None:
        self.history: list[RoutingDecision] = []

    def derive_task_requirements(
        self,
        task_type: str,
        phase: EngineeringPhase | str = EngineeringPhase.CODE_EDIT,
        file_count: int = 1,
        risk_level: str = "medium",
        context_needed: int = 32000,
        privacy_policy: PrivacyClass = PrivacyClass.APPROVED_CLOUD,
    ) -> TaskCapabilityRequirements:
        """Derive typed capability requirements for an engineering phase."""
        phase_enum = EngineeringPhase(phase) if isinstance(phase, str) else phase
        min_caps: dict[CapabilityDimension, float] = {}

        if phase_enum == EngineeringPhase.DOCUMENTATION:
            min_caps[CapabilityDimension.INSTRUCTION_FOLLOWING] = 0.6
            tool_req = False
        elif phase_enum in (EngineeringPhase.PLANNING, EngineeringPhase.PLAN_CRITICISM):
            min_caps[CapabilityDimension.PLAN_QUALITY] = 0.7
            min_caps[CapabilityDimension.SECURITY_REASONING] = (
                0.7 if risk_level in ("high", "critical") else 0.5
            )
            tool_req = True
        elif phase_enum == EngineeringPhase.RECOVERY:
            min_caps[CapabilityDimension.RECOVERY_QUALITY] = 0.75
            min_caps[CapabilityDimension.DEBUGGING] = 0.7
            tool_req = True
        elif file_count > 1:
            min_caps[CapabilityDimension.MULTI_FILE_REASONING] = 0.7
            min_caps[CapabilityDimension.PATH_DISCIPLINE] = 0.8
            tool_req = True
        else:
            min_caps[CapabilityDimension.SINGLE_FILE_REPAIR] = 0.6
            min_caps[CapabilityDimension.PATH_DISCIPLINE] = 0.7
            tool_req = True

        return TaskCapabilityRequirements(
            task_type=task_type,
            phase=phase_enum,
            minimum_capabilities=min_caps,
            preferred_privacy=privacy_policy,
            risk_level=risk_level,
            context_required=context_needed,
            structured_output_required=True,
            tool_use_required=tool_req,
        )

    def route(
        self,
        requirements: TaskCapabilityRequirements,
        mode: PortfolioMode | str = PortfolioMode.BALANCED,
        user_model_choice: str | None = None,
        budget_remaining_usd: float | None = None,
        ask_before_frontier: bool = True,
        previous_failed_models: list[str] | None = None,
    ) -> RoutingDecision:
        """Select cheapest suitable model adhering to portfolio mode, budget & privacy policies."""
        portfolio_mode = PortfolioMode(mode) if isinstance(mode, str) else mode
        failed_set = set(previous_failed_models or [])

        descriptors = [
            item
            for item in model_registry.list_all()
            if item.enabled
            and (item.backend != "custom" or os.environ.get("NEXUS_MODEL_ID", "").strip())
        ]
        reasons: list[str] = []
        policy_constraints: list[str] = []

        # Filter 1: Privacy policy
        if (
            portfolio_mode == PortfolioMode.PRIVATE
            or requirements.preferred_privacy == PrivacyClass.LOCAL_ONLY
        ):
            valid_descs = [
                d
                for d in descriptors
                if d.local
                or d.privacy_class in (PrivacyClass.LOCAL_ONLY, PrivacyClass.PRIVATE_INFRASTRUCTURE)
            ]
            policy_constraints.append(
                f"Privacy constraint enforced: {requirements.preferred_privacy.value}"
            )
        else:
            valid_descs = list(descriptors)

        # Filter 2: Tools & Structured Output
        if requirements.tool_use_required:
            valid_descs = [d for d in valid_descs if d.supports_tools]
        if requirements.structured_output_required:
            valid_descs = [d for d in valid_descs if d.supports_structured_output]

        # Filter 3: Context Window
        valid_descs = [
            d for d in valid_descs if (d.context_window or 128000) >= requirements.context_required
        ]

        # Filter 4: Exclude previous failed models for this run if alternatives exist
        non_failed = [
            d
            for d in valid_descs
            if d.model_id not in failed_set
            and model_registry.resolve_key(d.model_id) not in failed_set
        ]
        if non_failed:
            valid_descs = non_failed

        if not valid_descs:
            # Fallback to all enabled descriptors if filtering over-constrained
            valid_descs = model_registry.list_all()

        if not valid_descs:
            raise RuntimeError(
                "No enabled model satisfies the required privacy, tool, structured-output, and context constraints."
            )

        # Score & rank candidates.  Certified candidates are always preferred
        # over merely high-scoring candidates for high-risk work.
        scored_candidates: list[tuple[float, ModelDescriptor, CapabilityProfile, list[str]]] = []
        for desc in valid_descs:
            profile = model_doctor.get_profile(desc.model_id)
            gaps = self._capability_gaps(profile, requirements)
            score = self._evaluate_model_suitability(
                desc, profile, requirements, portfolio_mode, budget_remaining_usd
            )
            if not gaps:
                score += 500.0
            elif requirements.risk_level in ("high", "critical"):
                score -= 500.0
            scored_candidates.append((score, desc, profile, gaps))

        scored_candidates.sort(key=lambda x: x[0], reverse=True)

        if portfolio_mode == PortfolioMode.MANUAL and user_model_choice:
            user_desc = model_registry.get_descriptor(user_model_choice)
            if user_desc and user_desc in [c[1] for c in scored_candidates]:
                selected_desc = user_desc
                reasons.append(f"User manual selection override: {user_model_choice}")
            else:
                selected_desc = scored_candidates[0][1]
                reasons.append(
                    f"Manual choice {user_model_choice} invalid or policy-blocked; defaulted to suitable candidate {selected_desc.display_name}"
                )
        else:
            selected_desc = scored_candidates[0][1]
            reasons.append(f"Selected via portfolio mode {portfolio_mode.value}")

        selected_key = model_registry.resolve_key(selected_desc.model_id) or selected_desc.model_id
        alternatives = [c[1].display_name for c in scored_candidates[1:4]]

        # Estimate cost (assume typical turn of 2,000 prompt tokens + 500 completion tokens)
        in_cost = (selected_desc.input_cost or 0.0) * 2000 / 1_000_000
        out_cost = (selected_desc.output_cost or 0.0) * 500 / 1_000_000
        expected_cost = in_cost + out_cost

        # Escalation & Approval checks
        approval = False
        if selected_desc.tier == ModelTier.FRONTIER and ask_before_frontier:
            approval = True
            policy_constraints.append("Frontier tier model selected; requires user approval")

        selected_row = next(item for item in scored_candidates if item[1] == selected_desc)
        sel_profile = selected_row[2]
        capability_gaps = list(selected_row[3])
        meets_requirements = not capability_gaps
        measured = bool(
            sel_profile and sel_profile.source not in {"conservative-prior", "prior", "unknown"}
        )
        if requirements.risk_level in ("high", "critical") and not measured:
            capability_gaps.append(
                "high-risk routing requires measured Model Doctor evidence; profile is prior-only"
            )
            meets_requirements = False
        confidence = (
            0.85
            if meets_requirements
            and sel_profile
            and sel_profile.overall_band in (CapabilityBand.STRONG, CapabilityBand.SUITABLE)
            else 0.45
        )
        if not meets_requirements:
            approval = True
            policy_constraints.append(
                "Selected model is not certified for every required capability; autonomous execution is blocked pending approval or escalation"
            )
            reasons.append("Capability gaps: " + "; ".join(capability_gaps))

        reasons.append(f"Phase {requirements.phase.value} risk {requirements.risk_level}")
        reasons.append(f"Estimated cost ${expected_cost:.6f}")

        decision = RoutingDecision(
            decision_id=f"route-{uuid.uuid4().hex[:12]}",
            task_phase=requirements.phase,
            selected_model=selected_desc.display_name,
            selected_model_key=selected_key,
            alternatives=alternatives,
            reasons=reasons,
            expected_cost_usd=expected_cost,
            expected_latency_ms=300.0 if selected_desc.local else 800.0,
            capability_confidence=confidence,
            policy_constraints=policy_constraints,
            escalation_allowed=True,
            approval_required=approval,
            meets_requirements=meets_requirements,
            capability_gaps=capability_gaps,
            evaluation_source=f"model_doctor:{getattr(sel_profile, 'source', 'missing')}",
        )

        self.history.append(decision)
        return decision

    @staticmethod
    def _capability_gaps(
        profile: CapabilityProfile | None,
        requirements: TaskCapabilityRequirements,
    ) -> list[str]:
        if profile is None:
            return ["capability profile unavailable"]
        gaps: list[str] = []
        for dimension, minimum in requirements.minimum_capabilities.items():
            score = profile.capabilities.get(dimension.value)
            if score is None:
                gaps.append(f"{dimension.value}: missing (required {minimum:.2f})")
            elif score.score < minimum:
                gaps.append(f"{dimension.value}: {score.score:.2f} < {minimum:.2f}")
            elif score.confidence < 0.50:
                gaps.append(f"{dimension.value}: confidence {score.confidence:.2f} < 0.50")
        return gaps

    def _evaluate_model_suitability(
        self,
        desc: ModelDescriptor,
        profile: CapabilityProfile | None,
        reqs: TaskCapabilityRequirements,
        mode: PortfolioMode,
        budget_remaining: float | None,
    ) -> float:
        score = 50.0

        # Privacy fit
        if reqs.preferred_privacy == PrivacyClass.LOCAL_ONLY and not desc.local:
            return -1000.0

        # Cost factor
        in_p = desc.input_cost or 0.0
        out_p = desc.output_cost or 0.0
        total_p = in_p + out_p

        if mode == PortfolioMode.CHEAPEST:
            score += (10.0 - total_p) * 5.0
        elif mode == PortfolioMode.STRONGEST:
            if desc.tier == ModelTier.FRONTIER:
                score += 40.0
            elif desc.tier == ModelTier.STRONG:
                score += 30.0
        elif mode == PortfolioMode.FASTEST:
            if desc.local:
                score += 35.0

        # High-risk work must not be routed to weak local models merely because
        # they are cheap.  Strong/frontier tiers receive an explicit reliability
        # premium, while unqualified local profiles are penalized.
        if reqs.risk_level in ("high", "critical"):
            if desc.tier == ModelTier.FRONTIER:
                score += 35.0
            elif desc.tier == ModelTier.STRONG:
                score += 28.0
            elif desc.tier == ModelTier.AFFORDABLE:
                score += 8.0
            elif desc.tier == ModelTier.LOCAL:
                score -= 35.0

        # Capability profile matching
        if profile:
            for dim, min_val in reqs.minimum_capabilities.items():
                c_score = profile.capabilities.get(dim.value)
                if c_score:
                    if c_score.score >= min_val:
                        score += 15.0
                    else:
                        score -= 25.0

        # Budget constraint penalty
        if budget_remaining is not None and (total_p * 0.005) > budget_remaining:
            score -= 200.0

        return score

    def downshift_if_suitable(
        self,
        current_model_key: str,
        downstream_phase: EngineeringPhase,
        risk_level: str = "low",
    ) -> str:
        """Safely downshift from strong model to cheap model for documentation/boilerplate edits."""
        if risk_level in ("high", "critical") or downstream_phase in (
            EngineeringPhase.PLANNING,
            EngineeringPhase.DEBUGGING,
        ):
            return current_model_key

        if downstream_phase in (EngineeringPhase.DOCUMENTATION, EngineeringPhase.REPOS_SUMMARY):
            # Prefer local Nova or cheapest cloud model
            local_desc = model_registry.get_descriptor("nova3b")
            if local_desc and local_desc.enabled:
                return "nova3b"
            flash_desc = model_registry.get_descriptor("deepseek-flash")
            if flash_desc and flash_desc.enabled:
                return "deepseek-flash"

        return current_model_key


# Global ModelRouter singleton
model_router = ModelRouter()
