"""Runtime Execution Contract Engine (Sprint 6)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Set

from nexus.planning.engineering_plan import EngineeringPlan
from nexus.planning.task_contract import TaskContract


@dataclass
class ExecutionContract:
    contract_id: str = field(default_factory=lambda: f"exec-contract-{uuid.uuid4().hex[:8]}")
    plan_id: str = ""
    plan_version: int = 1
    repository_snapshot_id: str = "snap-initial"
    allowed_tools: List[str] = field(default_factory=list)
    allowed_mutation_paths: List[str] = field(default_factory=list)
    protected_paths: List[str] = field(default_factory=list)
    command_policy: Dict[str, Any] = field(default_factory=dict)
    network_policy: Dict[str, Any] = field(default_factory=dict)
    budget: Dict[str, Any] = field(default_factory=dict)
    max_retries: int = 3
    acceptance_criteria: List[Dict[str, Any]] = field(default_factory=list)
    required_checkpoints: List[str] = field(default_factory=list)
    required_verification_gates: List[str] = field(default_factory=list)
    cancellation_behavior: str = "SAFE_ROLLBACK"

    def is_tool_allowed(self, tool_name: str) -> bool:
        if not self.allowed_tools:
            return True
        return tool_name in self.allowed_tools

    def is_mutation_allowed(self, path: str) -> bool:
        if not self.allowed_mutation_paths:
            return True
        for allowed in self.allowed_mutation_paths:
            if path == allowed or path.startswith(allowed):
                return True
        return False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> ExecutionContract:
        return cls(**data)


class ExecutionContractGenerator:
    """Translates an approved EngineeringPlan into a runtime-enforceable ExecutionContract."""

    def generate(
        self,
        plan: EngineeringPlan,
        task_contract: Optional[TaskContract] = None,
        tool_policy: Optional[List[str]] = None,
    ) -> ExecutionContract:
        tools: Set[str] = set(tool_policy or [])
        mutation_paths: Set[str] = set()

        for step in plan.steps:
            tools.update(step.allowed_tools)
            mutation_paths.update(step.mutation_scope)
            mutation_paths.update(step.intended_targets)

        # Default allowed tools if none specified
        if not tools:
            tools = {"read_file", "write_file", "replace_file_content", "run_command", "view_file"}

        gates: List[str] = []
        for ac in plan.acceptance_criteria:
            if isinstance(ac, dict) and ac.get("mandatory", True):
                gates.append(ac.get("id", "AC-gate"))

        return ExecutionContract(
            plan_id=plan.plan_id,
            plan_version=plan.version,
            repository_snapshot_id=plan.repository_snapshot_id,
            allowed_tools=list(tools),
            allowed_mutation_paths=list(mutation_paths) or plan.affected_scope,
            protected_paths=["pyproject.toml", "setup.py", "SECURITY.md"],
            command_policy={"allow_destructive": False, "command_budget": 20},
            network_policy={"allow_external": False},
            budget={"max_turns": 15, "max_tokens": 100000},
            max_retries=3,
            acceptance_criteria=plan.acceptance_criteria,
            required_checkpoints=["pre_mutation", "post_mutation", "pre_finalization"],
            required_verification_gates=gates,
            cancellation_behavior="SAFE_ROLLBACK",
        )
