"""Planning Policy Registry (Sprint 6)."""

from __future__ import annotations

from typing import Any, Dict

from nexus.planning.task_contract import TaskType


class PlanningPolicyRegistry:
    """Enforces task-class-specific planning rules and templates."""

    POLICIES: Dict[TaskType, Dict[str, Any]] = {
        TaskType.BUG_REPAIR: {
            "require_reproducible_failure": True,
            "require_root_cause_hypothesis": True,
            "max_files_modified": 5,
            "require_regression_test": True,
            "allow_broad_rewrite": False,
        },
        TaskType.FEATURE_IMPLEMENTATION: {
            "require_interface_contract": True,
            "require_architecture_check": True,
            "max_files_modified": 15,
            "require_unit_tests": True,
            "allow_broad_rewrite": False,
        },
        TaskType.REFACTOR: {
            "require_behavior_preservation_criteria": True,
            "require_impact_graph": True,
            "max_files_modified": 25,
            "require_broad_regression_tests": True,
            "allow_broad_rewrite": True,
        },
        TaskType.MIGRATION: {
            "require_phased_milestones": True,
            "require_compatibility_window": True,
            "require_rollback_plan": True,
            "max_files_modified": 30,
            "allow_broad_rewrite": True,
        },
        TaskType.SECURITY_REMEDIATION: {
            "require_threat_statement": True,
            "require_trust_boundary_analysis": True,
            "require_adversarial_tests": True,
            "require_explicit_approval": True,
            "max_files_modified": 10,
            "allow_broad_rewrite": False,
        },
        TaskType.DEPENDENCY_UPGRADE: {
            "require_lockfile_check": True,
            "require_changelog_review": True,
            "require_full_build_test": True,
            "max_files_modified": 5,
            "allow_broad_rewrite": False,
        },
    }

    @classmethod
    def get_policy(cls, task_type: TaskType) -> Dict[str, Any]:
        return cls.POLICIES.get(
            task_type,
            {
                "require_unit_tests": True,
                "max_files_modified": 10,
                "allow_broad_rewrite": False,
            },
        )
