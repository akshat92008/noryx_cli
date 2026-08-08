"""Regression coverage for the durable Nexus runtime contract."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexus.agent import Agent
from nexus.budget import BudgetController, BudgetedClient, BudgetExceeded, BudgetLimits
from nexus.planner import Difficulty, IntentType, PlanningEngine, PlanType, TaskStatus
from nexus.policy import get_mode_policy
from nexus.repo_graph import RepoGraph
from nexus.run_state import CriterionResult, CriterionStatus, RunLedger, RunStatus
from nexus.workspace import GitWorktreeSession


def test_run_ledger_persists_request_events_checkpoint_and_report(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = RunLedger("session-test", workspace, root=tmp_path / "state")

    turn_id = ledger.begin(
        "Fix the parser and add a regression test",
        analysis={"intent": IntentType.FIX, "plan_type": PlanType.PLANNED},
        plan={"id": "plan-1", "steps": []},
    )
    event_id = ledger.append_event(
        "tool_call",
        status="verified",
        detail="pytest passed",
        metadata={"tool": "run_command"},
    )
    checkpoint = ledger.checkpoint(
        "tests-green",
        plan={"id": "plan-1", "steps": []},
        evidence_count=3,
        history_count=1,
    )
    report = ledger.finalize(
        RunStatus.VERIFIED,
        objective="Fix the parser and add a regression test",
        criteria=[
            CriterionResult(
                "Regression test passes",
                CriterionStatus.SATISFIED,
                evidence_ids=["ev-1"],
            )
        ],
        files_changed=["parser.py", "test_parser.py"],
    )

    assert turn_id == "turn-0001"
    assert event_id == "event-000001"
    assert checkpoint.is_file()
    assert report["status"] == "VERIFIED"
    summary = RunLedger("session-test", workspace, root=tmp_path / "state").resume_summary()
    assert summary["request"]["request"].startswith("Fix the parser")
    assert summary["checkpoint"]["label"] == "tests-green"
    assert summary["final_report"]["acceptance_criteria"][0]["status"] == "SATISFIED"


def test_budget_controller_blocks_calls_and_currency_without_prices():
    controller = BudgetController(BudgetLimits(max_hosted_calls=1))
    controller.before_hosted_call()
    with pytest.raises(BudgetExceeded, match="Hosted-call budget exhausted"):
        controller.before_hosted_call()

    with pytest.raises(ValueError, match="requires explicit input and output prices"):
        BudgetController(BudgetLimits(max_cost_usd=1.0))


def test_budgeted_client_accounts_provider_usage():
    response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=120, completion_tokens=30))

    class Client:
        def chat(self, *args, **kwargs):
            return response

    controller = BudgetController(
        BudgetLimits(
            max_hosted_calls=2,
            max_cost_usd=1.0,
            input_price_per_million=2.0,
            output_price_per_million=4.0,
        )
    )
    client = BudgetedClient(Client(), controller)
    assert client.chat(model_id="test", messages=[], stream=False) is response
    snapshot = controller.snapshot()
    assert snapshot["usage"]["hosted_calls"] == 1
    assert snapshot["usage"]["prompt_tokens"] == 120
    assert snapshot["usage"]["completion_tokens"] == 30
    assert snapshot["usage"]["estimated_cost_usd"] == pytest.approx(0.00036)


def test_budgeted_client_caps_completion_before_provider_call():
    captured = {}
    response = SimpleNamespace(usage=SimpleNamespace(prompt_tokens=2, completion_tokens=4))

    class Client:
        def chat(self, *args, **kwargs):
            captured.update(kwargs)
            return response

    controller = BudgetController(BudgetLimits(max_completion_tokens=7))
    client = BudgetedClient(Client(), controller)
    client.chat(
        model_id="test",
        messages=[{"role": "user", "content": "hello"}],
        max_tokens=100,
        stream=False,
    )
    assert captured["max_tokens"] == 7


def test_budget_blocks_prompt_before_network_activity():
    controller = BudgetController(BudgetLimits(max_prompt_tokens=1))
    with pytest.raises(BudgetExceeded, match="would be exceeded before"):
        controller.before_hosted_call(
            [{"role": "user", "content": "this prompt is larger than one token"}],
            10,
        )


def test_planner_creates_acceptance_criteria_and_task_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path / "state"))
    planner = PlanningEngine()
    analysis = {
        "intent": IntentType.FIX,
        "difficulty": Difficulty.COMPLEX,
        "plan_type": PlanType.PLANNED,
        "skills_needed": ["testing"],
    }
    plan = planner.create_plan(
        "Fix parser.py and add regression coverage in tests/test_parser.py",
        analysis,
    )

    assert "parser.py" in plan.permitted_files
    assert "tests/test_parser.py" in plan.permitted_files
    assert any("regression" in item.lower() for item in plan.acceptance_criteria)
    assert all(step.retry_limit >= 1 for step in plan.steps)
    assert all(step.max_tool_calls > 0 for step in plan.steps)
    assert plan.next_step.id == 0

    planner.advance_step(0, TaskStatus.COMPLETED, "reproduced")
    assert plan.next_step.id == 1
    restored = planner.load_plan(plan.id)
    assert restored is not None
    assert restored.steps[0].status == TaskStatus.COMPLETED
    assert restored.acceptance_criteria == plan.acceptance_criteria


def test_repo_graph_indexes_symbols_callers_and_impacted_tests(tmp_path):
    (tmp_path / "app.py").write_text(
        "def calculate(value):\n    return value + 1\n",
        encoding="utf-8",
    )
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_app.py").write_text(
        "from app import calculate\n\ndef test_calculate():\n    assert calculate(1) == 2\n",
        encoding="utf-8",
    )
    graph = RepoGraph(tmp_path, state_root=tmp_path / "state")

    first = graph.build()
    assert first.indexed == 2
    symbols = graph.find_symbols("calculate")
    assert any(item.path == "app.py" and item.kind == "function" for item in symbols)
    callers = graph.find_callers("calculate")
    assert callers[0]["path"] == "tests/test_app.py"
    assert graph.impacted_tests(["app.py"]) == ["tests/test_app.py"]

    second = graph.build()
    assert second.reused == 2
    (tmp_path / "app.py").write_text(
        "def calculate(value):\n    return value + 2\n",
        encoding="utf-8",
    )
    updated = graph.update_paths(["app.py"])
    assert updated.indexed == 1


def test_git_worktree_session_creates_isolated_branch(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "nexus@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Nexus Test"],
        cwd=repository,
        check=True,
    )
    (repository / "README.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=repository, check=True)

    session = GitWorktreeSession(
        repository,
        "session-123",
        state_root=tmp_path / "state",
    )
    info = session.create()

    assert Path(info.path).is_dir()
    assert info.branch == "nexus/session-123"
    assert Path(info.path) != repository
    assert session.status()["git_status"].startswith("## nexus/session-123")


def test_git_worktree_preserves_and_applies_over_dirty_source(tmp_path):
    repository = tmp_path / "repo"
    repository.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repository, check=True)
    subprocess.run(
        ["git", "config", "user.email", "nexus@example.invalid"],
        cwd=repository,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Nexus Test"],
        cwd=repository,
        check=True,
    )
    tracked = repository / "tracked.txt"
    tracked.write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=repository, check=True)
    tracked.write_text("uncommitted\n", encoding="utf-8")
    (repository / "user-note.txt").write_text("preserve me\n", encoding="utf-8")

    session = GitWorktreeSession(
        repository,
        "dirty-session",
        state_root=tmp_path / "state",
    )
    info = session.create()
    isolated = Path(info.path)
    assert isolated.joinpath("tracked.txt").read_text(encoding="utf-8") == "uncommitted\n"
    isolated.joinpath("tracked.txt").write_text("uncommitted plus nexus\n", encoding="utf-8")
    isolated.joinpath("agent.txt").write_text("created\n", encoding="utf-8")

    session.apply()

    assert tracked.read_text(encoding="utf-8") == "uncommitted plus nexus\n"
    assert (repository / "user-note.txt").read_text(encoding="utf-8") == "preserve me\n"
    assert (repository / "agent.txt").read_text(encoding="utf-8") == "created\n"
    session.discard()


def test_run_ledger_json_files_are_valid_after_repeated_updates(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = RunLedger("session-json", workspace, root=tmp_path / "state")
    ledger.begin("Build a feature", analysis={}, plan={"id": "p"})
    for index in range(10):
        ledger.append_event("progress", status="verified", detail=str(index))
        ledger.checkpoint(f"step-{index}", evidence_count=index)

    for path in ledger.turn_dir.rglob("*.json"):
        json.loads(path.read_text(encoding="utf-8"))


def test_agent_run_ledger_tracks_verified_tools_and_complete_rollback(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    test_policy = get_mode_policy("autonomous")
    test_policy.require_os_isolation = False
    agent = Agent(
        api_key="nvapi-test",
        model_key="glm-5.2",
        working_dir=str(tmp_path),
        permission_mode="acceptEdits",
        mode_policy=test_policy,
        allow_unisolated_host_process=True,
    )
    request = "Build calculator.py with a regression test"
    analysis = agent.planner.analyze(request)
    plan = agent.planner.create_plan(request, analysis)
    agent._begin_managed_run(request, analysis, plan)

    code_result, code_ok = agent._execute_tool_with_safety(
        "write_file",
        {
            "path": "calculator.py",
            "content": "def add(left, right):\n    return left + right\n",
        },
    )
    test_result, test_ok = agent._execute_tool_with_safety(
        "write_file",
        {
            "path": "test_calculator.py",
            "content": (
                "from calculator import add\n\ndef test_add():\n    assert add(2, 3) == 5\n"
            ),
        },
    )
    command_result, command_ok = agent._execute_tool_with_safety(
        "run_process",
        {"argv": [sys.executable, "-m", "pytest", "-q"], "cwd": str(tmp_path)},
    )
    report = agent._run_finalizer.finish(
        "Implemented calculator with regression coverage.",
        [],
    )

    assert code_ok, code_result
    assert test_ok, test_result
    assert command_ok, command_result
    assert report["status"] in {"VERIFIED", "PARTIALLY_VERIFIED"}
    assert report["files_changed"] == [
        str(tmp_path / "calculator.py"),
        str(tmp_path / "test_calculator.py"),
    ]
    assert agent.run_ledger.latest_checkpoint()["label"] == "command-completed"

    rolled_back, detail = agent.rollback_current_run()
    assert rolled_back, detail
    assert not (tmp_path / "calculator.py").exists()
    assert not (tmp_path / "test_calculator.py").exists()
    assert (
        agent.run_ledger.resume_summary()["final_report"]["status"] == RunStatus.ROLLED_BACK.value
    )
