"""Comprehensive Unit Test Suite for Sprint 6 Planning Intelligence Subsystem."""

import pytest
from pathlib import Path
from nexus.planning.task_contract import (
    TaskContract, Requirement, RequirementSource, TaskType, RiskLevel, Question, Assumption
)
from nexus.planning.ambiguity import AmbiguityEngine
from nexus.planning.engineering_plan import (
    EngineeringPlan, PlanStep, ActionType, Hypothesis, HypothesisStatus
)
from nexus.planning.acceptance import AcceptanceEngine, VerificationType
from nexus.planning.validator import DeterministicValidator, IssueSeverity
from nexus.planning.graph import PlanDependencyGraph
from nexus.planning.scope import ScopeEstimator
from nexus.planning.risk import RiskAssessor
from nexus.planning.cost import CostEstimator
from nexus.planning.critic import PlanCritic, CritiqueDecision
from nexus.planning.execution_contract import ExecutionContractGenerator
from nexus.planning.replanner import PlanReplanner
from nexus.planning.policies import PlanningPolicyRegistry
from nexus.planning.engine import PlanningEngine


def test_task_contract_serialization():
    req = Requirement(
        id="REQ-1",
        statement="Fix bug in context manager",
        source=RequirementSource.EXPLICIT_USER,
        mandatory=True
    )
    contract = TaskContract(
        raw_user_request="Fix bug in context manager",
        normalized_objective="Fix bug in context manager",
        task_type=TaskType.BUG_REPAIR,
        mandatory_requirements=[req],
        risk_level=RiskLevel.LOW
    )
    data = contract.to_dict()
    restored = TaskContract.from_dict(data)
    assert restored.raw_user_request == contract.raw_user_request
    assert restored.task_type == TaskType.BUG_REPAIR
    assert len(restored.mandatory_requirements) == 1
    assert restored.mandatory_requirements[0].source == RequirementSource.EXPLICIT_USER


def test_ambiguity_engine():
    engine = AmbiguityEngine()
    
    # Destructive action -> blocking question
    questions, assumptions = engine.analyze("delete all tables from database")
    assert any(q.is_blocking for q in questions)
    
    # Simple request -> non-blocking assumptions
    questions, assumptions = engine.analyze("add helper method in utils")
    assert not any(q.is_blocking for q in questions)
    assert len(assumptions) >= 1


def test_acceptance_criteria_engine():
    engine = AcceptanceEngine()
    assert engine.is_vague("works properly")
    assert engine.is_vague("make robust")
    assert not engine.is_vague("All unit tests in test_auth.py pass cleanly")

    req = Requirement(
        id="REQ-1",
        statement="All auth tests pass",
        source=RequirementSource.EXPLICIT_USER
    )
    contract = TaskContract(
        raw_user_request="All auth tests pass",
        mandatory_requirements=[req]
    )
    criteria = engine.generate_criteria(contract, {"tests": ["tests/test_auth.py"]})
    assert len(criteria) >= 1
    assert criteria[0].verification.target_path == "tests/test_auth.py"


def test_deterministic_validator():
    validator = DeterministicValidator()
    
    # Valid plan
    step1 = PlanStep(
        step_id="step-1",
        title="Check code",
        objective="Inspect code",
        action_type=ActionType.INSPECT,
        completion_condition="Inspected successfully",
        verification_method="Visual review"
    )
    plan = EngineeringPlan(
        steps=[step1],
        acceptance_criteria=[{"id": "AC-1", "statement": "passes"}]
    )
    issues = validator.validate(plan)
    assert not any(i.severity == IssueSeverity.ERROR for i in issues)

    # Empty plan -> Error
    empty_plan = EngineeringPlan(steps=[], acceptance_criteria=[])
    issues_empty = validator.validate(empty_plan)
    assert any(i.code == "EMPTY_PLAN" for i in issues_empty)


def test_plan_dependency_graph():
    step1 = PlanStep(step_id="step-1", title="Step 1", objective="o1", completion_condition="c1", verification_method="v1")
    step2 = PlanStep(step_id="step-2", title="Step 2", objective="o2", dependencies=["step-1"], completion_condition="c2", verification_method="v2")
    
    plan = EngineeringPlan(steps=[step1, step2])
    graph = PlanDependencyGraph(plan)
    
    assert graph.detect_cycles() == []
    order = graph.get_execution_order()
    assert [s.step_id for s in order] == ["step-1", "step-2"]


def test_plan_dependency_graph_cycle():
    step1 = PlanStep(step_id="step-1", title="Step 1", objective="o1", dependencies=["step-2"], completion_condition="c1", verification_method="v1")
    step2 = PlanStep(step_id="step-2", title="Step 2", objective="o2", dependencies=["step-1"], completion_condition="c2", verification_method="v2")
    
    plan = EngineeringPlan(steps=[step1, step2])
    graph = PlanDependencyGraph(plan)
    cycles = graph.detect_cycles()
    assert len(cycles) > 0


def test_plan_critic():
    critic = PlanCritic()
    step1 = PlanStep(
        step_id="step-1",
        title="Edit code",
        objective="Edit code",
        action_type=ActionType.MUTATE,
        completion_condition="Code edited",
        verification_method="Run test"
    )
    plan = EngineeringPlan(
        steps=[step1],
        acceptance_criteria=[{"id": "AC-1", "statement": "passes"}],
        verification_strategy={"command": "pytest"}
    )
    critique = critic.critique(plan)
    assert critique.decision in (CritiqueDecision.APPROVE, CritiqueDecision.APPROVE_WITH_WARNINGS)


def test_execution_contract_generator():
    generator = ExecutionContractGenerator()
    step1 = PlanStep(
        step_id="step-1",
        title="Mutate file",
        objective="Mutate file",
        action_type=ActionType.MUTATE,
        intended_targets=["nexus/planner.py"],
        allowed_tools=["replace_file_content"],
        completion_condition="Mutated",
        verification_method="pytest"
    )
    plan = EngineeringPlan(
        steps=[step1],
        affected_scope=["nexus/planner.py"],
        acceptance_criteria=[{"id": "AC-1", "mandatory": True}]
    )
    contract = generator.generate(plan)
    assert contract.is_tool_allowed("replace_file_content")
    assert contract.is_mutation_allowed("nexus/planner.py")
    assert not contract.is_mutation_allowed("nexus/secret.py")


def test_plan_replanner_loop_prevention():
    replanner = PlanReplanner(max_revisions=3)
    step1 = PlanStep(step_id="step-1", title="Step 1", objective="o1", completion_condition="c1", verification_method="v1")
    plan = EngineeringPlan(steps=[step1], version=1)

    # First revision -> succeeds
    rev1, ok1 = replanner.revise_plan(plan, "test failure", "step-1")
    assert ok1
    assert rev1.version == 2

    # If revision produces identical step signature to rev1, blocked
    rev2, ok2 = replanner.revise_plan(rev1, "test failure", "non_existent_step")
    assert not ok2


def test_planning_engine_end_to_end(tmp_path):
    engine = PlanningEngine(workspace_root=tmp_path)
    contract = engine.interpret_task("Fix bug in context_manager.py")
    assert contract.task_type == TaskType.BUG_REPAIR

    plan = engine.create_engineering_plan(contract, {"relevant_files": ["nexus/context_manager.py"]})
    assert len(plan.steps) == 3

    critique, exec_contract = engine.critique_and_finalize(plan, contract)
    assert critique.decision in (CritiqueDecision.APPROVE, CritiqueDecision.APPROVE_WITH_WARNINGS)
    assert exec_contract is not None
    assert exec_contract.plan_id == plan.plan_id
