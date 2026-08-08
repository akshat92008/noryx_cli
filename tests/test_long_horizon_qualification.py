"""Contracts for resumable, live-provider, long-horizon qualification."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

from nexus.agent import Agent
from nexus.benchmark import BenchmarkRunner, BenchmarkSuite, BenchmarkTask, _quality_gates
from nexus.pipeline import ExecutionPipeline
from nexus.planner import (
    Difficulty,
    ExecutionPlan,
    IntentType,
    PlanningEngine,
    PlanStep,
    PlanType,
    TaskStatus,
)
from nexus.run_state import RunLedger
from nexus.sandbox import SandboxBackend, SandboxRunner


def test_long_horizon_manifest_is_valid_and_dry_runnable():
    manifest = Path(__file__).parents[1] / "benchmarks" / "long_horizon.json"

    suite = BenchmarkSuite.load(manifest)
    report = BenchmarkRunner(suite).run(dry_run=True).to_dict()

    assert suite.schema_version == "nexus.benchmark.v2"
    assert suite.profile == "long-horizon"
    assert suite.tasks[0].max_attempts == 4
    assert suite.tasks[0].max_turns_per_attempt == 40
    assert {task.id for task in suite.tasks} == {
        "startup-control-plane-from-one-prompt",
        "operations-erp-from-one-prompt",
    }
    assert report["summary"]["passed"] == 0
    assert report["summary"]["manifest_valid_tasks"] == 2
    assert report["summary"]["executed_tasks"] == 0
    assert report["summary"]["pass_rate"] == 0.0


def test_long_horizon_agent_command_preserves_single_prompt_across_resumes(tmp_path):
    task = BenchmarkTask(
        id="massive",
        category="long-horizon-system",
        prompt="Build the whole product",
        repository=str(tmp_path),
        verification=(("python", "verify.py"),),
        max_attempts=3,
        max_turns_per_attempt=25,
        max_hosted_calls_per_attempt=30,
        max_provider_attempts_per_attempt=45,
    )

    initial = BenchmarkRunner._agent_command(task, tmp_path)
    resumed = BenchmarkRunner._agent_command(
        task,
        tmp_path,
        resume_from="session/turn-0001",
    )

    assert initial.count("Build the whole product") == 1
    assert "--resume-run" not in initial
    assert "--resume-run" in resumed
    assert "session/turn-0001" in resumed
    assert "Build the whole product" not in resumed
    assert resumed[resumed.index("--max-turns") + 1] == "25"
    assert resumed[resumed.index("--max-provider-attempts") + 1] == "45"


def test_complex_quality_gates_fail_closed_on_placeholders_and_missing_depth(tmp_path):
    (tmp_path / "service.py").write_text("raise NotImplementedError\n", encoding="utf-8")
    task = BenchmarkTask(
        id="quality",
        category="startup-build",
        prompt="Build it",
        repository=str(tmp_path),
        verification=(("python", "verify.py"),),
        required_paths=("service.py", "tests/test_*.py"),
        forbidden_content=("NotImplementedError",),
        minimum_changed_files=3,
        minimum_test_files=2,
    )

    gates = _quality_gates(
        task,
        tmp_path,
        ["service.py"],
        [{"success": True}],
        [],
        [],
    )

    failed = {item["name"] for item in gates if not item["passed"]}
    assert failed == {
        "required_artifacts",
        "minimum_change_surface",
        "test_suite_depth",
        "no_placeholder_implementation",
    }


def test_benchmark_automatically_resumes_same_run_until_verified(tmp_path, monkeypatch):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "verify.py").write_text("print('ok')\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        """{
          "schema_version": "nexus.benchmark.v2",
          "name": "resume-contract",
          "profile": "long-horizon",
          "tasks": [{
            "id": "resume",
            "category": "long-horizon-system",
            "prompt": "Build the entire system",
            "repository": "repo",
            "verification": [["python", "verify.py"]],
            "max_attempts": 2,
            "max_turns_per_attempt": 5
          }]
        }""",
        encoding="utf-8",
    )
    agent_commands = []


    from nexus.sandbox import CommandResult, SandboxBackend
    import nexus.process_gateway
    agent_commands = []
    
    def fake_gateway_run(req):
        command = " ".join(req.command)
        if "nexus" in command:
            agent_commands.append(command)
            index = len(agent_commands)
            status = "FAILED" if index == 1 else "VERIFIED"
            import json
            payload = {
                "session_id": "session",
                "run": {
                    "turn_id": f"turn-{index:04d}",
                    "status": status,
                    "outcome": status,
                    "metadata": {},
                },
            }
            return CommandResult(
                argv=list(req.command),
                cwd=str(req.workspace),
                backend=SandboxBackend.RESTRICTED,
                success=index != 1,
                exit_code=2 if index == 1 else 0,
                stdout=json.dumps(payload),
                stderr="",
                timed_out=False
            )
        return CommandResult(
            argv=list(req.command),
            cwd=str(req.workspace),
            backend=SandboxBackend.RESTRICTED,
            success=True,
            exit_code=0,
            stdout="ok\n",
            stderr="",
            timed_out=False
        )
    monkeypatch.setattr(nexus.process_gateway.ProcessExecutionGateway, "run", fake_gateway_run)

    result = BenchmarkRunner(BenchmarkSuite.load(manifest)).run().results[0]

    assert result.status == "PASSED"
    assert result.attempts == 2
    assert result.resume_count == 1
    assert result.recovery_succeeded is True
    assert "--resume-run" not in agent_commands[0]
    assert "--resume-run" in agent_commands[1]
    assert "Build the entire system" not in agent_commands[1]


def test_pipeline_consumes_persisted_resume_plan_once():
    planner = PlanningEngine()
    plan = ExecutionPlan(
        id="resume-plan",
        goal="Build a large product",
        intent=IntentType.BUILD,
        difficulty=Difficulty.MASSIVE,
        plan_type=PlanType.PLANNED,
        steps=[PlanStep(id=0, title="Continue", description="Continue work")],
    )
    analysis = {
        "intent": IntentType.BUILD,
        "difficulty": Difficulty.MASSIVE,
        "plan_type": PlanType.PLANNED,
        "skills_needed": ["backend"],
    }
    agent = SimpleNamespace(
        planner=planner,
        _resume_analysis_override=analysis,
        _resume_plan_override=plan,
    )

    recovered_analysis, recovered_plan, stage = ExecutionPipeline(agent)._stage_planning(
        "recovery prompt"
    )

    assert recovered_analysis is analysis
    assert recovered_plan is plan
    assert stage.metadata["resumed"] is True
    assert agent._resume_analysis_override is None
    assert agent._resume_plan_override is None


def test_resume_retries_only_unfinished_plan_steps(tmp_path, monkeypatch):
    state_root = tmp_path / "state"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("NEXUS_HOME", str(state_root))
    plan = ExecutionPlan(
        id="interrupted",
        goal="Build the system",
        intent=IntentType.BUILD,
        difficulty=Difficulty.MASSIVE,
        plan_type=PlanType.PLANNED,
        steps=[
            PlanStep(
                id=0,
                title="Architecture",
                description="done",
                status=TaskStatus.COMPLETED,
            ),
            PlanStep(
                id=1,
                title="Implementation",
                description="retry",
                depends_on=[0],
                status=TaskStatus.FAILED,
                error="turn budget exhausted",
            ),
            PlanStep(
                id=2,
                title="Verification",
                description="pending",
                depends_on=[1],
            ),
        ],
        status=TaskStatus.FAILED,
    )
    ledger = RunLedger("resume-session", workspace)
    ledger.begin(
        "Build the system",
        analysis={"intent": "build"},
        plan=plan,
    )
    ledger.checkpoint("architecture-complete", plan=plan)

    agent = Agent(api_key="test", working_dir=str(workspace), workspace_isolation=False)
    captured = {}

    def inspect_resume(prompt):
        captured["prompt"] = prompt
        captured["plan"] = agent._resume_plan_override
        return "continued", []

    monkeypatch.setattr(agent, "run_non_interactive", inspect_resume)
    result, events = agent.resume_interrupted("resume-session/turn-0001")

    resumed = captured["plan"]
    assert result == "continued"
    assert events == []
    assert resumed.steps[0].status == TaskStatus.COMPLETED
    assert resumed.steps[1].status == TaskStatus.PENDING
    assert resumed.steps[1].error == ""
    assert resumed.steps[2].status == TaskStatus.PENDING
    assert resumed.current_step == 1
    assert "Already completed task ids: [0]" in captured["prompt"]


def test_live_provider_workflow_is_manual_and_cost_gated():
    workflow = (
        Path(__file__).parents[1] / ".github" / "workflows" / "live-provider.yml"
    ).read_text(encoding="utf-8")
    script = (Path(__file__).parents[1] / "scripts" / "run_live_provider_gate.py").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow
    assert "NEXUS_RUN_LIVE_PROVIDER" in workflow
    assert "--allow-cost" in workflow
    assert "live-provider execution requires --allow-cost" in script
    assert "configured_hosted_credentials" in script


def test_release_workflow_blocks_on_three_fresh_long_horizon_trials():
    root = Path(__file__).parents[1]
    workflow = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    suite = BenchmarkSuite.load(root / "benchmarks" / "release_long_horizon.json")

    assert suite.profile == "release-long-horizon"
    assert len(suite.tasks) == 1
    assert suite.tasks[0].id == "startup-control-plane-from-one-prompt"
    assert "Install and verify kernel sandbox" in workflow
    assert "SandboxBackend.BUBBLEWRAP" in workflow
    assert "benchmarks/release_long_horizon.json" in workflow
    assert "--trials 3" in workflow
    assert "--required-pass-rate 1.0" in workflow
