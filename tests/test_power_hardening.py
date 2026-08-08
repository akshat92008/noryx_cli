"""Regression coverage for Nexus execution-intelligence and safety upgrades."""

from __future__ import annotations

import os
import threading

from nexus.agent import Agent
from nexus.api import RoundRobinKeyPool
from nexus.history import FileHistory
from nexus.planner import (
    Difficulty,
    ExecutionPlan,
    IntentType,
    PlanningEngine,
    PlanStep,
    PlanType,
    TaskStatus,
)
from nexus.policy import get_mode_policy
from nexus.repo_graph import RepoGraph
from nexus.tools import tool_context, tool_multi_edit, ToolResult
from nexus.verification import CheckStatus, CheckType, VerificationEngine


def test_confirmed_command_keeps_required_os_isolation(tmp_path):
    agent = Agent(
        api_key="dummy",
        working_dir=str(tmp_path),
        mode_policy=get_mode_policy("review"),
    )
    captured: dict = {}
    agent._tool_controller._dispatch_tool_execution = (  # type: ignore[method-assign]
        lambda name, args: captured.update(name=name, args=dict(args)) or ToolResult(output="✅ ok", status=0)
    )
    try:
        output, success = agent._execute_tool_with_safety(
            "run_process",
            {"argv": ["echo", "ok"]},
            _user_confirmed=True,
        )
    finally:
        agent.close()

    assert success is True
    assert output == "✅ ok"
    assert captured["args"]["require_os_isolation"] is True


def test_multi_edit_rolls_back_every_file_when_commit_fails(tmp_path, monkeypatch):
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("alpha\n", encoding="utf-8")
    second.write_text("beta\n", encoding="utf-8")
    history = FileHistory("atomic-rollback")

    real_replace = os.replace
    calls = 0

    def fail_second_replace(source, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated second-file failure")
        return real_replace(source, target)

    monkeypatch.setattr("nexus.tools.os.replace", fail_second_replace)
    with tool_context(str(tmp_path), history):
        result = tool_multi_edit(
            [
                {"path": "first.txt", "old_text": "alpha", "new_text": "changed-a"},
                {"path": "second.txt", "old_text": "beta", "new_text": "changed-b"},
            ]
        )

    assert result.startswith("❌ Multi-edit transaction failed")
    assert first.read_text(encoding="utf-8") == "alpha\n"
    assert second.read_text(encoding="utf-8") == "beta\n"
    assert history.changes == []


def test_planner_retry_preserves_failure_evidence(tmp_path, monkeypatch):
    monkeypatch.setattr("nexus.planner.PLANS_DIR", tmp_path / "plans")
    planner = PlanningEngine()
    step = PlanStep(
        id=1,
        title="Implement",
        description="Implement feature",
        status=TaskStatus.FAILED,
        attempts=1,
        error="first failure",
    )
    planner.current_plan = ExecutionPlan(
        id="plan-test",
        goal="feature",
        intent=IntentType.BUILD,
        difficulty=Difficulty.COMPLEX,
        plan_type=PlanType.PLANNED,
        steps=[step],
        status=TaskStatus.FAILED,
    )

    assert planner.retry_step(1, "targeted test failed") is True
    assert step.status == TaskStatus.PENDING
    assert step.error == "targeted test failed"
    assert planner.current_plan.status == TaskStatus.IN_PROGRESS
    assert planner.current_plan.failure_replans[-1]["evidence"] == "targeted test failed"


def test_repo_graph_context_bundle_includes_callers_and_tests(tmp_path):
    package = tmp_path / "app"
    tests = tmp_path / "tests"
    package.mkdir()
    tests.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "service.py").write_text(
        "def calculate_total(value: int) -> int:\n    return value * 2\n",
        encoding="utf-8",
    )
    (tests / "test_service.py").write_text(
        "from app.service import calculate_total\n\n"
        "def test_total():\n    assert calculate_total(2) == 4\n",
        encoding="utf-8",
    )
    graph = RepoGraph(tmp_path, state_root=tmp_path / "state")
    graph.build(force=True)

    bundle = graph.context_bundle("fix calculate_total", max_files=5, max_chars=8000)

    assert "app/service.py" in bundle
    assert "tests/test_service.py" in bundle
    assert "calculate_total" in bundle


def test_change_aware_verification_stops_on_targeted_failure(tmp_path, monkeypatch):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
    engine = VerificationEngine(str(tmp_path), require_os_isolation=False)
    calls: list[tuple[CheckType, str]] = []

    def fake_execute(check_type, command, **_kwargs):
        calls.append((check_type, command))
        from nexus.verification import CheckResult

        return CheckResult(check_type, CheckStatus.FAILED, command, "targeted failure")

    monkeypatch.setattr(engine, "_execute_check", fake_execute)
    monkeypatch.setattr(engine, "run_all", lambda: (_ for _ in ()).throw(AssertionError("full gate should not run")))

    report = engine.run_change_aware(
        ["app/service.py"], impacted_tests=["tests/test_service.py"]
    )

    assert report.all_passed is False
    assert len(report.checks) == 1
    assert calls and "tests/test_service.py" in calls[0][1]


def test_round_robin_pool_remains_consistent_under_concurrency():
    pool = RoundRobinKeyPool(["k1", "k2", "k3"])
    results: list[str] = []
    lock = threading.Lock()

    def consume() -> None:
        local = [pool.get_next_key() for _ in range(100)]
        with lock:
            results.extend(local)

    threads = [threading.Thread(target=consume) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == 600
    assert set(results) == {"k1", "k2", "k3"}
    assert all(results.count(key) == 200 for key in ("k1", "k2", "k3"))
