"""Canonical Planning Engine Facade for Nexus CLI (Sprint 6)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from nexus.events import EventBus, EventType
from nexus.paths import nexus_home
from nexus.planning.acceptance import AcceptanceEngine
from nexus.planning.ambiguity import AmbiguityEngine
from nexus.planning.cost import CostEstimator
from nexus.planning.critic import CritiqueDecision, PlanCritic, PlanCritique
from nexus.planning.engineering_plan import ActionType, EngineeringPlan, Hypothesis, PlanStep
from nexus.planning.execution_contract import ExecutionContract, ExecutionContractGenerator
from nexus.planning.graph import PlanDependencyGraph
from nexus.planning.policies import PlanningPolicyRegistry
from nexus.planning.replanner import PlanReplanner
from nexus.planning.risk import RiskAssessor, RiskLevel
from nexus.planning.scope import ScopeEstimator
from nexus.planning.task_contract import (
    Constraint,
    Requirement,
    RequirementSource,
    TaskContract,
    TaskType,
)
from nexus.planning.validator import DeterministicValidator, ValidationIssue
from nexus.tools import TOOL_DEFINITIONS


class PlanningEngine:
    """Canonical Sprint 6 Planning Engine governing task interpretation, criticism, and execution contracts."""

    def __init__(self, workspace_root: Optional[Union[str, Path]] = None, repo_intelligence: Any = None):
        self.root_dir = Path(workspace_root).resolve() if workspace_root else Path.cwd().resolve()
        self.repo_intelligence = repo_intelligence

        self.ambiguity_engine = AmbiguityEngine(self.repo_intelligence)
        self.acceptance_engine = AcceptanceEngine()
        self.scope_estimator = ScopeEstimator(self.repo_intelligence)
        self.risk_assessor = RiskAssessor()
        self.cost_estimator = CostEstimator()
        self.validator = DeterministicValidator(root_dir=str(self.root_dir))
        self.allowed_tools = {definition.name for definition in TOOL_DEFINITIONS}
        self.critic = PlanCritic(self.validator, allowed_tools=self.allowed_tools)
        self.contract_generator = ExecutionContractGenerator()
        self.replanner = PlanReplanner()

        self.current_contract: Optional[TaskContract] = None
        self.current_plan: Optional[EngineeringPlan] = None
        self.current_critique: Optional[PlanCritique] = None
        self.current_execution_contract: Optional[ExecutionContract] = None

    def interpret_task(
        self, raw_request: str, repo_context: Optional[Dict[str, Any]] = None
    ) -> TaskContract:
        """Step 1: Convert raw user request into canonical TaskContract."""
        EventBus.publish(
            EventType.TASK_STARTED,
            run_id="global",
            component="PlanningEngine",
            metadata={"request": raw_request[:100]},
        )

        lower_req = raw_request.lower()

        # Classify the primary requested action before considering subject-matter
        # keywords.  "Build an auth service" is a feature; "Fix auth" is repair.
        import re

        if re.search(r"^\s*(?:please\s+)?(?:fix|debug|repair|patch|resolve)\b", lower_req):
            ttype = TaskType.BUG_REPAIR
        elif re.search(r"^\s*(?:please\s+)?(?:build|create|implement|add|develop|write)\b", lower_req):
            ttype = TaskType.FEATURE_IMPLEMENTATION
        elif re.search(r"^\s*(?:please\s+)?(?:refactor|restructure|simplify|extract)\b", lower_req):
            ttype = TaskType.REFACTOR
        elif re.search(r"^\s*(?:please\s+)?(?:migrate|port|convert|upgrade)\b", lower_req):
            ttype = TaskType.MIGRATION
        elif re.search(r"^\s*(?:please\s+)?(?:test|add tests|write tests)\b", lower_req):
            ttype = TaskType.TEST_CREATION
        elif re.search(r"^\s*(?:please\s+)?(?:secure|harden|remediate)\b", lower_req):
            ttype = TaskType.SECURITY_REMEDIATION
        elif re.search(r"^\s*(?:please\s+)?(?:optimize|speed up|improve performance)\b", lower_req):
            ttype = TaskType.PERFORMANCE_OPTIMIZATION
        elif re.search(r"^\s*(?:please\s+)?(?:configure|set up|setup)\b", lower_req):
            ttype = TaskType.CONFIGURATION_CHANGE
        elif re.search(r"^\s*(?:please\s+)?(?:document|write docs|update docs)\b", lower_req):
            ttype = TaskType.DOCUMENTATION
        elif re.search(r"^\s*(?:please\s+)?(?:explain|describe)\b", lower_req):
            ttype = TaskType.CODE_EXPLANATION
        elif re.search(r"^\s*(?:please\s+)?(?:investigate|review|analyze|analyse|audit)\b", lower_req):
            ttype = TaskType.INVESTIGATION
        elif "fix" in lower_req or "bug" in lower_req or "error" in lower_req or "fail" in lower_req:
            ttype = TaskType.BUG_REPAIR
        elif "refactor" in lower_req or "clean" in lower_req:
            ttype = TaskType.REFACTOR
        elif "migrate" in lower_req:
            ttype = TaskType.MIGRATION
        elif "security" in lower_req or "auth" in lower_req:
            ttype = TaskType.SECURITY_REMEDIATION
        elif "upgrade" in lower_req or "dependency" in lower_req:
            ttype = TaskType.DEPENDENCY_UPGRADE
        elif "test" in lower_req:
            ttype = TaskType.TEST_CREATION
        else:
            ttype = TaskType.FEATURE_IMPLEMENTATION

        # Mandatory requirements
        reqs = [
            Requirement(
                id="REQ-1",
                statement=raw_request,
                source=RequirementSource.EXPLICIT_USER,
                mandatory=True,
            )
        ]

        # Check ambiguity
        questions, assumptions = self.ambiguity_engine.analyze(raw_request, repo_context)

        # Risk assessment
        temp_contract = TaskContract(
            raw_user_request=raw_request,
            normalized_objective=raw_request.strip(),
            task_type=ttype,
            mandatory_requirements=reqs,
            assumptions=assumptions,
            unresolved_questions=questions,
        )
        risk_info = self.risk_assessor.assess_task_risk(temp_contract, (repo_context or {}).get("relevant_files"))
        temp_contract.risk_level = RiskLevel(risk_info["risk_level"])
        temp_contract.completion_definition = f"Verified completion of: {raw_request}"

        self.current_contract = temp_contract

        EventBus.publish(
            EventType.TASK_STARTED,
            run_id=temp_contract.task_id,
            component="PlanningEngine",
            metadata={"task_type": ttype.value, "risk_level": temp_contract.risk_level.value},
        )

        return temp_contract

    def create_engineering_plan(
        self,
        task_contract: TaskContract,
        repo_context: Optional[Dict[str, Any]] = None,
    ) -> EngineeringPlan:
        """Step 2: Generate canonical EngineeringPlan from TaskContract and repo context."""
        targets = (repo_context or {}).get("relevant_files", [])
        tests = (repo_context or {}).get("tests", [])

        # Estimate scope
        scope = self.scope_estimator.estimate_scope(targets, task_contract.raw_user_request)

        # Acceptance criteria
        criteria = self.acceptance_engine.generate_criteria(task_contract, repo_context)

        # Construct steps
        steps: List[PlanStep] = []

        # Step 1: Inspection / Analysis
        steps.append(
            PlanStep(
                step_id="step-1",
                title="Inspect repository state and relevant files",
                objective="Confirm existing implementation details and test coverage",
                action_type=ActionType.INSPECT,
                dependencies=[],
                intended_targets=targets[:3],
                allowed_tools=["read_file", "search_code", "repo_symbols"],
                expected_outcome="Confirmed code context and baseline test state",
                completion_condition="Target files inspected and relevant symbols located",
                verification_method="Context check",
                risk_level=RiskLevel.LOW,
            )
        )

        # Step 2: Implementation / Mutation
        steps.append(
            PlanStep(
                step_id="step-2",
                title="Execute implementation changes",
                objective=task_contract.normalized_objective,
                action_type=ActionType.MUTATE,
                dependencies=["step-1"],
                intended_targets=targets[:5],
                allowed_tools=["edit_file", "patch_file", "write_file", "multi_edit"],
                mutation_scope=scope.allowed_paths,
                expected_outcome="Code modification matching requirements",
                completion_condition="Code edits applied cleanly",
                verification_method="Syntax and AST validation",
                risk_level=task_contract.risk_level,
                rollback_strategy="Git checkout target files on failure",
            )
        )

        # Step 3: Verification
        test_file = tests[0] if tests else "tests/"
        steps.append(
            PlanStep(
                step_id="step-3",
                title="Verify changes via automated tests",
                objective="Confirm all acceptance criteria pass with zero regressions",
                action_type=ActionType.VERIFY,
                dependencies=["step-2"],
                intended_targets=[test_file],
                allowed_tools=["run_process"],
                expected_outcome="All tests pass cleanly",
                completion_condition="Test command exits with code 0",
                verification_method=f"python3 -m pytest {test_file}",
                risk_level=RiskLevel.LOW,
            )
        )

        hypotheses: List[Hypothesis] = []
        if task_contract.task_type in {TaskType.BUG_REPAIR, TaskType.TEST_REPAIR, TaskType.SECURITY_REMEDIATION}:
            hypotheses = [
                Hypothesis(
                    hypothesis_id="hyp-1",
                    statement=(
                        "The reported failure is caused by an invariant violation in the "
                        "repository paths selected by impact analysis, not by the tests themselves."
                    ),
                    confidence=0.45,
                    validation_action=(
                        "Reproduce the failure, trace the canonical call path, and collect "
                        "symbol/caller evidence before mutation."
                    ),
                ),
                Hypothesis(
                    hypothesis_id="hyp-2",
                    statement=(
                        "A dependent caller or compatibility contract outside the initially "
                        "reported file contributes to the failure."
                    ),
                    confidence=0.30,
                    validation_action=(
                        "Inspect callers, impacted tests, and public contracts; expand scope only "
                        "with repository evidence."
                    ),
                ),
            ]

        plan = EngineeringPlan(
            task_contract_id=task_contract.task_id,
            repository_snapshot_id=task_contract.repository_snapshot_id,
            objective=task_contract.normalized_objective,
            root_cause_hypotheses=hypotheses,
            affected_scope=scope.allowed_paths,
            steps=steps,
            acceptance_criteria=[c.to_dict() for c in criteria],
            verification_strategy={"command": f"python3 -m pytest {test_file}"},
            risk_assessment=self.risk_assessor.assess_task_risk(task_contract, targets),
            assumptions=task_contract.assumptions,
            version=1,
        )

        cost_est = self.cost_estimator.estimate(plan)
        plan.estimated_cost = cost_est.to_dict()

        self.current_plan = plan
        return plan

    def critique_and_finalize(
        self,
        plan: EngineeringPlan,
        task_contract: Optional[TaskContract] = None,
        repo_context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[PlanCritique, Optional[ExecutionContract]]:
        """Step 3: Run independent PlanCritic and generate runtime ExecutionContract if approved."""
        critique = self.critic.critique(plan, task_contract, repo_context)
        self.current_critique = critique

        if critique.decision in (CritiqueDecision.APPROVE, CritiqueDecision.APPROVE_WITH_WARNINGS):
            exec_contract = self.contract_generator.generate(plan, task_contract)
            self.current_execution_contract = exec_contract
            self.persist_run_artifacts(task_contract, plan, critique, exec_contract)
            return critique, exec_contract
        else:
            return critique, None

    def persist_run_artifacts(
        self,
        task_contract: Optional[TaskContract],
        plan: EngineeringPlan,
        critique: PlanCritique,
        exec_contract: Optional[ExecutionContract],
        run_id: str = "latest",
    ) -> Path:
        """Store machine-readable planning artifacts under .nexus/runs/<run-id>/."""
        run_dir = nexus_home() / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)

        if task_contract:
            (run_dir / "task-contract.json").write_text(
                json.dumps(task_contract.to_dict(), indent=2), encoding="utf-8"
            )

        (run_dir / f"plan-v{plan.version}.json").write_text(
            json.dumps(plan.to_dict(), indent=2), encoding="utf-8"
        )
        (run_dir / f"critique-v{plan.version}.json").write_text(
            json.dumps(critique.to_dict(), indent=2), encoding="utf-8"
        )

        if exec_contract:
            (run_dir / f"execution-contract-v{plan.version}.json").write_text(
                json.dumps(exec_contract.to_dict(), indent=2), encoding="utf-8"
            )

        return run_dir
