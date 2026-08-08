"""Regression coverage for the integrated Nexus final runtime."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from nexus.behavioral import DatabaseVerifier, ProbeStatus, SecurityScanner
from nexus.benchmark import BenchmarkSuite
from nexus.execution import ExecutionEngine, ReviewOutcome, TaskOutcome, classify_failure
from nexus.nova_backend import NovaToolProposal
from nexus.nova_runtime import AtomicTask, NovaOutputParser
from nexus.planner import (
    Difficulty,
    ExecutionPlan,
    IntentType,
    PlanStep,
    PlanType,
)
from nexus.policy import PermissionDecision, PolicyLoader
from nexus.project_memory import ProjectMemory
from nexus.repo_graph import RepoGraph
from nexus.run_catalog import RunCatalog
from nexus.run_state import RunLedger, RunStatus
from nexus.sandbox import CommandSpec, SandboxBackend, SandboxRunner
from nexus.skills.loader import SkillLoader, SkillRegistry
from nexus.two_node_backend import SubtaskExecution, TwoNodeBackend
from nexus.workspace import GitWorktreeSession


def _plan(steps: list[PlanStep]) -> ExecutionPlan:
    return ExecutionPlan(
        id="final-plan",
        goal="Implement and verify the final runtime",
        intent=IntentType.BUILD,
        difficulty=Difficulty.COMPLEX,
        plan_type=PlanType.PLANNED,
        steps=steps,
        acceptance_criteria=["All tasks and checks pass"],
        retry_policy={"per_task": 1, "total_repairs": 2},
    )


def test_sandbox_uses_typed_argv_and_reports_unenforced_network(tmp_path, monkeypatch):
    monkeypatch.setattr(SandboxRunner, "_backend_cache", SandboxBackend.RESTRICTED)
    result = SandboxRunner(tmp_path).run(
        CommandSpec.create(
            [sys.executable, "-c", "print('typed-ok')"],
            tmp_path,
            timeout_seconds=10,
            require_os_isolation=False,
            allow_unisolated_host_process=True,
        )
    )
    assert result.success
    assert result.stdout == "typed-ok"
    assert result.argv[:2] == [sys.executable, "-c"]
    assert not result.network_enforced
    assert "network=policy-only" in result.format_tool_output()


def test_sandbox_can_fail_closed_without_os_isolation(tmp_path, monkeypatch):
    monkeypatch.setattr(SandboxRunner, "_backend_cache", SandboxBackend.RESTRICTED)
    result = SandboxRunner(tmp_path).run(
        CommandSpec.create(
            ["echo", "must-not-run"],
            tmp_path,
            require_os_isolation=True,
        )
    )
    assert not result.success
    assert result.backend == SandboxBackend.BLOCKED
    assert "No supported OS sandbox" in result.blocked_reason


def test_execution_engine_runs_dag_repairs_and_independent_review(tmp_path):
    plan = _plan(
        [
            PlanStep(1, "Implement", "Implement feature", retry_limit=1),
            PlanStep(2, "Verify", "Run checks", depends_on=[1]),
        ]
    )
    ledger = RunLedger("dag-session", tmp_path, root=tmp_path / "state")
    ledger.begin("implement feature", plan=plan)
    calls = []

    def execute(step):
        calls.append(step.id)
        if step.id == 1:
            return TaskOutcome(False, "syntax failed", output="SyntaxError: invalid syntax")
        return TaskOutcome(True, "checks passed", evidence_ids=["check-1"])

    def repair(step, _outcome, failure, attempt):
        assert step.id == 1
        assert failure.value == "syntax"
        assert attempt == 1
        return TaskOutcome(True, "minimal repair passed", evidence_ids=["repair-1"])

    result = ExecutionEngine(plan, ledger).run(
        execute,
        repair=repair,
        reviewer=lambda _plan: ReviewOutcome(True, "independent review passed"),
    )
    assert result.succeeded
    assert calls == [1, 2]
    assert result.repairs == 1
    assert plan.steps[0].attempts == 2
    assert (
        json.loads((ledger.turn_dir / "tasks.json").read_text())["tasks"][1]["status"]
        == "completed"
    )


def test_execution_engine_rejects_cycles(tmp_path):
    plan = _plan(
        [
            PlanStep(1, "A", "A", depends_on=[2]),
            PlanStep(2, "B", "B", depends_on=[1]),
        ]
    )
    ledger = RunLedger("cycle-session", tmp_path, root=tmp_path / "state")
    ledger.begin("cycle", plan=plan)
    with pytest.raises(ValueError, match="cycle"):
        ExecutionEngine(plan, ledger).run(lambda _step: TaskOutcome(True, "ok"))


def test_two_node_runtime_drives_persisted_dag_repair_review_and_resume(
    tmp_path,
    monkeypatch,
):
    class FakeCeiling:
        def decompose(self, _request, planner_context=""):
            assert planner_context
            return (
                [
                    AtomicTask(1, "Modify src/app.py"),
                    AtomicTask(2, "Modify tests/test_app.py", depends_on=[1]),
                ],
                "typed decomposition",
            )

        def review(self, _request, _context):
            return True, "independent review passed", []

    ledger = RunLedger("two-node-session", tmp_path, root=tmp_path / "state")
    ledger.begin("request", plan={"id": "seed", "steps": []})
    backend = object.__new__(TwoNodeBackend)
    backend.working_dir = tmp_path
    backend.ceiling = FakeCeiling()
    backend.ceiling_model_name = "ceiling-test"
    backend.intern_model = "nova_codex"
    backend.run_ledger = ledger
    backend.parser = NovaOutputParser()
    backend.escalation_log_path = tmp_path / ".nexusai" / "escalations.jsonl"
    backend.repo_graph = RepoGraph(tmp_path, state_root=tmp_path / "graph-state")
    backend.repo_graph.build()
    monkeypatch.setattr(backend, "_route_task", lambda _task: ("nova", "atomic"))
    monkeypatch.setattr(backend, "_log_escalation", lambda *_args: None)

    calls = []

    def proposal(task):
        return NovaToolProposal(
            "write_file",
            {"path": f"task-{task.id}.py", "content": "VALUE = 1\n"},
            f"task-{task.id}.py",
            "validated",
        )

    def execute_intern(**kwargs):
        task = kwargs["task"]
        calls.append(("execute", task.id))
        if task.id == 1:
            return SubtaskExecution(
                task,
                "Nova",
                "ESCALATE",
                attempts=2,
                error="SyntaxError: invalid syntax",
            )
        return SubtaskExecution(
            task,
            "Nova",
            "VALIDATED",
            attempts=1,
            raw_output="validated task two",
            proposals=[proposal(task)],
        )

    def execute_repair(**kwargs):
        task = kwargs["task"]
        calls.append(("repair", task.id))
        return SubtaskExecution(
            task,
            "Ceiling directly",
            "CEILING_PASS",
            attempts=2,
            raw_output="validated repair",
            proposals=[proposal(task)],
            escalated=True,
        )

    monkeypatch.setattr(backend, "_execute_with_intern", execute_intern)
    monkeypatch.setattr(backend, "_escalate_to_ceiling", execute_repair)
    result = backend.run(
        "implement feature",
        {
            "intent": IntentType.BUILD,
            "difficulty": Difficulty.COMPLEX,
            "acceptance_criteria": ["All tasks and checks pass"],
        },
    )
    assert result.review_approved
    assert calls == [("execute", 1), ("repair", 1), ("execute", 2)]
    assert [step.status.value for step in result.execution_plan.steps] == [
        "completed",
        "completed",
    ]
    events = (ledger.turn_dir / "events.jsonl").read_text()
    assert '"kind": "repair_started"' in events
    assert '"kind": "independent_review"' in events

    resume_plan = result.execution_plan.to_dict()
    resume_plan["steps"][0]["status"] = "completed"
    resume_plan["steps"][1]["status"] = "failed"
    ledger.begin("resume", plan=resume_plan)
    calls.clear()
    resumed = backend.run("resume feature", {"resume_plan": resume_plan})
    assert resumed.review_approved
    assert calls == [("execute", 2)]
    assert resumed.executions[0].route_reason == "recovered verified checkpoint"


def test_failure_classifier_is_deterministic():
    assert classify_failure("ModuleNotFoundError: no module named x").value == "import"
    assert classify_failure("operation timed out").value == "timeout"


def test_run_ledger_uses_complete_canonical_artifact_contract(tmp_path):
    plan = _plan([PlanStep(1, "Task", "Task")])
    ledger = RunLedger("ledger-session", tmp_path, root=tmp_path / "state")
    ledger.begin("request", plan=plan)
    ledger.append_model_call(role="planner", model="test", status="verified")
    ledger.append_tool_call(tool="read_file", status="verified")
    ledger.record_costs({"usage": {"hosted_calls": 1}})
    ledger.store_artifact("patches", "change.diff", "--- before\n+++ after\n")
    ledger.store_artifact("tests", "pytest.txt", "1 passed\n")
    ledger.finalize(
        status=RunStatus.VERIFIED,
        objective="request",
    )
    expected = {
        "request.json",
        "plan.json",
        "tasks.json",
        "events.jsonl",
        "model_calls.jsonl",
        "tool_calls.jsonl",
        "costs.json",
        "patches",
        "tests",
        "checkpoints",
        "state.json",
        "final_report.json",
    }
    assert expected <= {item.name for item in ledger.turn_dir.iterdir()}
    inspected = RunCatalog(tmp_path / "state").inspect("ledger-session")
    assert inspected["final_report"]["status"] == "VERIFIED"
    with pytest.raises(FileNotFoundError, match="Invalid Nexus run"):
        RunCatalog(tmp_path / "state").inspect("../../etc")


def test_repograph_discovers_routes_models_owners_and_relevance(tmp_path):
    (tmp_path / ".github").mkdir()
    (tmp_path / ".github" / "CODEOWNERS").write_text("src/** @backend\n")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "users.py").write_text(
        "from fastapi import FastAPI\n"
        "from db import Base\n"
        "app = FastAPI()\n\n"
        "class User(Base):\n"
        "    pass\n\n"
        "@app.get('/users/{user_id}')\n"
        "def get_user(user_id: int):\n"
        "    return user_id\n"
    )
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_users.py").write_text(
        "from src.users import get_user\n\ndef test_user():\n    assert get_user(3) == 3\n"
    )
    graph = RepoGraph(tmp_path, state_root=tmp_path / "state")
    graph.build()
    assert graph.routes("users")[0]["route"] == "GET /users/{user_id}"
    assert graph.models("User")[0]["model"] == "User"
    assert graph.ownership("src/users.py") == ["@backend"]
    assert graph.relevant_files("fix user route regression")[0]["path"] in {
        "src/users.py",
        "tests/test_users.py",
    }


def test_policy_deny_precedes_allow_and_defaults_ask(tmp_path):
    policy_dir = tmp_path / ".nexus"
    policy_dir.mkdir()
    (policy_dir / "policies.yml").write_text(
        "allow:\n"
        "  - write: src/**\n"
        "deny:\n"
        "  - write: src/generated/**\n"
        "ask:\n"
        "  - network_access: *\n"
    )
    policy = PolicyLoader(tmp_path).load()
    assert policy.decide("write", "src/app.py") == PermissionDecision.ALLOW
    assert policy.decide("write", "src/generated/api.py") == PermissionDecision.DENY
    assert policy.decide("deployment", "production") == PermissionDecision.ASK


def test_all_nearest_project_instruction_files_are_combined(tmp_path):
    (tmp_path / "NEXUS.md").write_text("## Conventions\n- Use typed tools\n")
    (tmp_path / "AGENTS.md").write_text("## Conventions\n- Run regression tests\n")
    memory = ProjectMemory(str(tmp_path))
    rules = memory.load_rules()
    assert set(rules.conventions) == {"Use typed tools", "Run regression tests"}
    assert {Path(item).name for item in memory.get_rules_paths()} == {
        "NEXUS.md",
        "AGENTS.md",
    }


def test_trusted_project_markdown_skill_is_declarative(tmp_path):
    skills_dir = tmp_path / ".nexus" / "skills"
    skills_dir.mkdir(parents=True)
    skill_path = skills_dir / "api-review.md"
    skill_path.write_text(
        "---\n"
        "name: api-review\n"
        "description: Review API compatibility\n"
        "keywords: api, endpoint\n"
        "---\n"
        "Preserve existing API contracts.\n\n"
        "## Checklist\n"
        "- Run contract tests\n"
    )
    registry = SkillRegistry()
    SkillLoader(registry, tmp_path, trusted=lambda path: Path(path) == skill_path).load_project()
    skill = registry.get("api-review")
    assert skill is not None
    assert skill.get_quality_checklist() == ["Run contract tests"]
    assert "Preserve existing API contracts" in skill.get_system_prompt()


def test_database_and_security_verifiers_are_evidence_scoped(tmp_path):
    database = tmp_path / "app.db"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE users(id INTEGER PRIMARY KEY)")
    connection.commit()
    connection.close()
    db_result = DatabaseVerifier().verify_sqlite(database)
    assert db_result.status == ProbeStatus.PASSED
    risks = DatabaseVerifier().migration_risks("ALTER TABLE users DROP COLUMN email;")
    assert risks and risks[0]["requires_approval"] == "true"

    (tmp_path / "app.py").write_text("def safe():\n    return 'ok'\n")
    (tmp_path / "example.py").write_text("api_key = 'test-placeholder-key'\n")
    (tmp_path / "README.md").write_text("password = 'documentation-only'\n")
    scan = SecurityScanner().scan(tmp_path)
    assert scan.status == ProbeStatus.PASSED
    assert scan.evidence["scanned_files"] == 2
    assert scan.evidence["scope"] == "deterministic-pattern-scan-not-complete-audit"

    (tmp_path / "settings.py").write_text("api_key = 'sk-prod-abcdef123456'\n")
    unsafe = SecurityScanner().scan(tmp_path)
    assert unsafe.status == ProbeStatus.FAILED
    assert unsafe.evidence["findings"][0]["kind"] == "hardcoded-credential"


def test_non_git_workspace_is_a_persistent_isolated_copy(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("VALUE = 1\n")
    workspace = GitWorktreeSession(
        source,
        "copy-session",
        state_root=tmp_path / "state",
    )
    info = workspace.create()
    assert info.backend == "temporary-copy"
    isolated = Path(info.path)
    (isolated / "app.py").write_text("VALUE = 2\n")
    assert (source / "app.py").read_text() == "VALUE = 1\n"
    assert "persistent temporary copy" in workspace.status()["git_status"]


def test_benchmark_manifest_is_versioned_and_shell_free():
    manifest = Path(__file__).parents[1] / "benchmarks" / "core.json"
    suite = BenchmarkSuite.load(manifest)
    assert suite.tasks[0].category == "bug-repair"
    assert suite.tasks[0].verification == (("python", "verify.py"),)
