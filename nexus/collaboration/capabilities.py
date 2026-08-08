"""
nexus/collaboration/capabilities.py

Agent capability registry: maps roles to typed capability profiles
and validates assignments before worker launch.
"""

from __future__ import annotations

from typing import Dict, Optional

from nexus.collaboration.models import (
    AgentCapabilityProfile,
    AgentRole,
    ModelTier,
    RiskLevel,
)

# ---------------------------------------------------------------------------
# Default capability profiles
# ---------------------------------------------------------------------------

def _default_profiles() -> Dict[AgentRole, AgentCapabilityProfile]:
    return {
        AgentRole.PLANNER: AgentCapabilityProfile(
            role=AgentRole.PLANNER,
            supported_task_types=("planning", "orchestration", "review", "finalization"),
            supported_languages=("*",),
            allowed_tool_capabilities=(
                "read_file", "write_file", "run_command", "search_code",
                "transaction_gateway", "approve", "verify",
            ),
            mutation_allowed=True,
            maximum_risk_level=RiskLevel.CRITICAL,
            preferred_model_tiers=(
                ModelTier.CLOUD_FRONTIER,
                ModelTier.CLOUD_STANDARD,
                ModelTier.LOCAL_LARGE,
            ),
            context_budget=128_000,
            can_request_approval=True,
            can_create_workers=True,
        ),
        AgentRole.INVESTIGATOR: AgentCapabilityProfile(
            role=AgentRole.INVESTIGATOR,
            supported_task_types=("analysis", "investigation", "mapping"),
            supported_languages=("*",),
            allowed_tool_capabilities=("read_file", "search_code", "list_directory"),
            mutation_allowed=False,
            maximum_risk_level=RiskLevel.NONE,
            preferred_model_tiers=(
                ModelTier.LOCAL_MEDIUM,
                ModelTier.LOCAL_SMALL,
                ModelTier.CLOUD_LIGHT,
            ),
            context_budget=32_000,
            can_request_approval=False,
            can_create_workers=False,
        ),
        AgentRole.IMPLEMENTER: AgentCapabilityProfile(
            role=AgentRole.IMPLEMENTER,
            supported_task_types=("implementation", "refactoring", "bug_fix"),
            supported_languages=("python", "javascript", "typescript", "go", "rust"),
            allowed_tool_capabilities=(
                "read_file", "write_file", "run_command", "search_code", "transaction_gateway",
            ),
            mutation_allowed=True,
            maximum_risk_level=RiskLevel.HIGH,
            preferred_model_tiers=(
                ModelTier.CLOUD_STANDARD,
                ModelTier.CLOUD_FRONTIER,
                ModelTier.LOCAL_LARGE,
            ),
            context_budget=64_000,
            can_request_approval=False,
            can_create_workers=False,
        ),
        AgentRole.TEST_ENGINEER: AgentCapabilityProfile(
            role=AgentRole.TEST_ENGINEER,
            supported_task_types=("test_writing", "test_discovery", "coverage_analysis"),
            supported_languages=("python", "javascript", "typescript"),
            allowed_tool_capabilities=(
                "read_file", "write_file", "run_command", "search_code", "transaction_gateway",
            ),
            mutation_allowed=True,
            maximum_risk_level=RiskLevel.LOW,
            preferred_model_tiers=(
                ModelTier.LOCAL_LARGE,
                ModelTier.CLOUD_STANDARD,
            ),
            context_budget=48_000,
            can_request_approval=False,
            can_create_workers=False,
        ),
        AgentRole.REVIEWER: AgentCapabilityProfile(
            role=AgentRole.REVIEWER,
            supported_task_types=("architecture_review", "design_analysis", "review"),
            supported_languages=("*",),
            allowed_tool_capabilities=("read_file", "search_code"),
            mutation_allowed=False,
            maximum_risk_level=RiskLevel.NONE,
            preferred_model_tiers=(
                ModelTier.CLOUD_FRONTIER,
                ModelTier.CLOUD_STANDARD,
            ),
            context_budget=64_000,
            can_request_approval=False,
            can_create_workers=False,
        ),
        AgentRole.SECURITY_REVIEWER: AgentCapabilityProfile(
            role=AgentRole.SECURITY_REVIEWER,
            supported_task_types=("security_review", "vulnerability_analysis"),
            supported_languages=("*",),
            allowed_tool_capabilities=("read_file", "search_code"),
            mutation_allowed=False,
            maximum_risk_level=RiskLevel.NONE,
            preferred_model_tiers=(
                ModelTier.LOCAL_LARGE,
                ModelTier.CLOUD_STANDARD,
            ),
            context_budget=48_000,
            can_request_approval=False,
            can_create_workers=False,
        ),
        AgentRole.INTEGRATION_ENGINEER: AgentCapabilityProfile(
            role=AgentRole.INTEGRATION_ENGINEER,
            supported_task_types=("integration", "merge", "patch_application"),
            supported_languages=("*",),
            allowed_tool_capabilities=("read_file", "write_file", "run_command", "search_code"),
            mutation_allowed=True,
            maximum_risk_level=RiskLevel.MEDIUM,
            preferred_model_tiers=(
                ModelTier.CLOUD_STANDARD,
                ModelTier.LOCAL_LARGE,
            ),
            context_budget=48_000,
            can_request_approval=False,
            can_create_workers=False,
        ),
        AgentRole.CENTRAL_VERIFIER: AgentCapabilityProfile(
            role=AgentRole.CENTRAL_VERIFIER,
            supported_task_types=("verification", "acceptance_testing", "diff_review"),
            supported_languages=("*",),
            allowed_tool_capabilities=("read_file", "run_command", "search_code"),
            mutation_allowed=False,
            maximum_risk_level=RiskLevel.NONE,
            preferred_model_tiers=(
                ModelTier.LOCAL_LARGE,
                ModelTier.CLOUD_STANDARD,
            ),
            context_budget=32_000,
            can_request_approval=False,
            can_create_workers=False,
        ),
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class AgentCapabilityRegistry:
    """
    Stores and validates agent capability profiles.
    Workers may not launch without a registered and validated profile.
    """

    def __init__(self) -> None:
        self._profiles: Dict[AgentRole, AgentCapabilityProfile] = _default_profiles()

    def get_profile(self, role: AgentRole) -> Optional[AgentCapabilityProfile]:
        return self._profiles.get(role)

    def can_mutate(self, role: AgentRole) -> bool:
        profile = self.get_profile(role)
        return profile.mutation_allowed if profile else False

    def can_create_workers(self, role: AgentRole) -> bool:
        profile = self.get_profile(role)
        return profile.can_create_workers if profile else False

    def validate_assignment(self, role: AgentRole, task_type: str, language: str = "*") -> bool:
        profile = self.get_profile(role)
        if not profile:
            return False
        task_ok = ("*" in profile.supported_task_types) or (task_type in profile.supported_task_types)
        lang_ok = ("*" in profile.supported_languages) or (language in profile.supported_languages)
        return task_ok and lang_ok

    def register_profile(self, profile: AgentCapabilityProfile) -> None:
        self._profiles[profile.role] = profile

    def validate_tool_access(self, role: AgentRole, tool_name: str) -> bool:
        profile = self.get_profile(role)
        if not profile:
            return False
        return ("*" in profile.allowed_tool_capabilities) or (tool_name in profile.allowed_tool_capabilities)

    def validate_assignment_role(self, role: AgentRole, task_type: str, requires_mutation: bool = False) -> tuple[bool, str]:
        profile = self.get_profile(role)
        if not profile:
            return False, f"Role {role} is not registered"
        if ("*" not in profile.supported_task_types) and (task_type not in profile.supported_task_types):
            return False, f"Role {role.value if hasattr(role, 'value') else role} does not support task type {task_type}"
        if requires_mutation and not profile.mutation_allowed:
            return False, f"Role {role.value if hasattr(role, 'value') else role} does not allow mutation"
        return True, ""
