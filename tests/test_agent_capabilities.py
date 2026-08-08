"""
tests/test_agent_capabilities.py

Tests for AgentCapabilityRegistry and role enforcement.
All tests are deterministic — no live providers.
"""

import pytest

from nexus.collaboration.capabilities import AgentCapabilityRegistry
from nexus.collaboration.models import AgentRole, ModelTier, RiskLevel


@pytest.fixture
def registry():
    return AgentCapabilityRegistry()


def test_all_roles_registered(registry):
    """Every role must have a registered profile."""
    for role in AgentRole:
        assert registry.get_profile(role) is not None, f"Role {role.value} not registered"


def test_lead_engineer_can_mutate(registry):
    assert registry.can_mutate(AgentRole.LEAD_ENGINEER) is True


def test_repository_analyst_cannot_mutate(registry):
    assert registry.can_mutate(AgentRole.REPOSITORY_ANALYST) is False


def test_security_reviewer_cannot_mutate(registry):
    assert registry.can_mutate(AgentRole.SECURITY_REVIEWER) is False


def test_independent_verifier_cannot_mutate(registry):
    assert registry.can_mutate(AgentRole.INDEPENDENT_VERIFIER) is False


def test_implementation_engineer_can_mutate(registry):
    assert registry.can_mutate(AgentRole.IMPLEMENTATION_ENGINEER) is True


def test_documentation_engineer_can_mutate(registry):
    assert registry.can_mutate(AgentRole.DOCUMENTATION_ENGINEER) is True


def test_lead_engineer_can_create_workers(registry):
    assert registry.can_create_workers(AgentRole.LEAD_ENGINEER) is True


def test_worker_roles_cannot_create_workers(registry):
    """Non-lead roles must not be able to create sub-workers by default."""
    non_lead_roles = [r for r in AgentRole if r != AgentRole.LEAD_ENGINEER]
    for role in non_lead_roles:
        assert registry.can_create_workers(role) is False, (
            f"Role {role.value} should not be able to create workers"
        )


def test_validate_tool_access_allowed(registry):
    assert registry.validate_tool_access(AgentRole.LEAD_ENGINEER, "read_file") is True


def test_validate_tool_access_denied(registry):
    # REPOSITORY_ANALYST has no write_file access
    assert registry.validate_tool_access(AgentRole.REPOSITORY_ANALYST, "write_file") is False


def test_validate_assignment_role_ok(registry):
    ok, reason = registry.validate_assignment_role(
        AgentRole.IMPLEMENTATION_ENGINEER, "implementation", requires_mutation=True
    )
    assert ok is True
    assert reason == ""


def test_validate_assignment_role_wrong_task_type(registry):
    ok, reason = registry.validate_assignment_role(
        AgentRole.REPOSITORY_ANALYST, "implementation", requires_mutation=False
    )
    assert ok is False
    assert "does not support task type" in reason


def test_validate_assignment_role_mutation_not_allowed(registry):
    ok, reason = registry.validate_assignment_role(
        AgentRole.REPOSITORY_ANALYST, "analysis", requires_mutation=True
    )
    assert ok is False
    assert "does not allow mutation" in reason


def test_unregistered_role_returns_none(registry):
    # Directly calling get_profile with a made-up value
    assert registry.get_profile("NONEXISTENT_ROLE") is None  # type: ignore


def test_custom_profile_registration(registry):
    from nexus.collaboration.models import AgentCapabilityProfile
    custom = AgentCapabilityProfile(
        role=AgentRole.DEBUGGER,
        supported_task_types=("debugging", "tracing"),
        supported_languages=("*",),
        allowed_tool_capabilities=("read_file",),
        mutation_allowed=False,
        maximum_risk_level=RiskLevel.NONE,
        preferred_model_tiers=(ModelTier.LOCAL_MEDIUM,),
        context_budget=10_000,
        can_request_approval=False,
        can_create_workers=False,
    )
    registry.register_profile(custom)
    profile = registry.get_profile(AgentRole.DEBUGGER)
    assert profile is not None
    assert "tracing" in profile.supported_task_types


def test_all_profiles_have_preferred_model_tiers(registry):
    for role in AgentRole:
        profile = registry.get_profile(role)
        assert profile is not None
        assert len(profile.preferred_model_tiers) > 0, (
            f"Role {role.value} has no preferred_model_tiers"
        )
