"""Risk Assessment Module for Nexus Planning (Sprint 6)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from nexus.planning.task_contract import RiskLevel, TaskContract, TaskType


class RiskAssessor:
    """Evaluates task and plan characteristics to assign risk levels and approval criteria."""

    HIGH_RISK_KEYWORDS = [
        "auth", "login", "password", "secret", "token", "credential", "security",
        "crypto", "permission", "payment", "database", "migration", "drop", "delete"
    ]

    def assess_task_risk(
        self, task_contract: TaskContract, target_files: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        targets = target_files or []
        raw_text = (task_contract.raw_user_request + " " + task_contract.normalized_objective).lower()

        score = 0
        factors: List[str] = []

        # 1. Security / Data Sensitivity
        for kw in self.HIGH_RISK_KEYWORDS:
            if kw in raw_text or any(kw in f.lower() for f in targets):
                score += 3
                factors.append(f"Contains security-sensitive keyword '{kw}'")

        # 2. Task Type
        if task_contract.task_type in (TaskType.SECURITY_REMEDIATION, TaskType.MIGRATION):
            score += 4
            factors.append(f"Task type '{task_contract.task_type.value}' is inherently high risk")
        elif task_contract.task_type in (TaskType.BUG_REPAIR, TaskType.FEATURE_IMPLEMENTATION):
            score += 1

        # 3. Target Scope Size
        if len(targets) > 5:
            score += 2
            factors.append(f"Broad target file count ({len(targets)} files)")

        # Determine level
        if score >= 6:
            level = RiskLevel.CRITICAL
        elif score >= 4:
            level = RiskLevel.HIGH
        elif score >= 2:
            level = RiskLevel.MEDIUM
        else:
            level = RiskLevel.LOW

        approval_reqs: List[str] = []
        if level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            approval_reqs.append("USER_EXPLICIT_APPROVAL")
            approval_reqs.append("CRITIC_VERIFICATION")
            approval_reqs.append("TARGETED_SECURITY_TESTS")

        return {
            "risk_level": level.value,
            "risk_score": score,
            "risk_factors": factors,
            "approval_requirements": approval_reqs,
            "requires_rollback_plan": level in (RiskLevel.HIGH, RiskLevel.CRITICAL),
        }
