"""
Regression tests for Agent-level safety enforcement.
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import nexus.history as nexus_history
from nexus.agent import Agent
from nexus.policy import get_mode_policy
from nexus.sandbox import SandboxBackend, SandboxRunner


def _agent_for_tmp_path(tmp_path, monkeypatch) -> Agent:
    monkeypatch.setattr(nexus_history, "HISTORY_DIR", tmp_path / ".nexusai" / "history")
    return Agent(model_key="nova3b", working_dir=str(tmp_path))


def test_dangerous_command_requires_confirmation(tmp_path, monkeypatch):
    """DANGEROUS commands must not execute from the agent tool path."""
    target = tmp_path / "should_not_execute"
    target.mkdir()
    old_cwd = os.getcwd()
    try:
        agent = _agent_for_tmp_path(tmp_path, monkeypatch)
        result, success = agent._execute_tool_with_safety(
            "run_command",
            {"command": "rm -rf ./should_not_execute"},
        )
        repeated, repeated_success = agent._execute_tool_with_safety(
            "run_command",
            {"command": "rm -rf ./should_not_execute"},
        )
    finally:
        os.chdir(old_cwd)

    assert not success
    assert not repeated_success
    assert "PENDING_CONFIRMATION" in result
    assert "danger-0001" in result
    assert "danger-0001" in repeated
    assert "not executed" in result
    assert target.is_dir()


def test_explicit_confirmation_executes_only_the_pending_call(tmp_path, monkeypatch):
    """A CLI confirmation id authorizes its exact stored operation once."""
    target = tmp_path / "confirmed_delete"
    target.mkdir()
    old_cwd = os.getcwd()
    try:
        agent = _agent_for_tmp_path(tmp_path, monkeypatch)
        agent.allow_unisolated_host_process = True
        # This test isolates one-time confirmation semantics; native sandbox
        # enforcement is covered separately and must not depend on CI tooling.
        agent.mode_policy.require_os_isolation = False
        pending, pending_success = agent._execute_tool_with_safety(
            "run_command",
            {"command": "rm -rf ./confirmed_delete"},
        )
        confirmed, confirmed_success = agent.confirm_pending_operation("danger-0001")
        expired, expired_success = agent.confirm_pending_operation("danger-0001")
    finally:
        os.chdir(old_cwd)

    assert not pending_success
    assert "PENDING_CONFIRMATION" in pending
    assert confirmed_success
    assert "$ rm -rf ./confirmed_delete" in confirmed
    assert not target.exists()
    assert not expired_success
    assert "expired" in expired


def test_autonomous_mode_disables_shell_and_fails_closed_without_native_sandbox(
    tmp_path, monkeypatch
):
    target = tmp_path / "must_remain"
    target.mkdir()
    monkeypatch.setattr(SandboxRunner, "_backend_cache", SandboxBackend.RESTRICTED)
    agent = Agent(
        model_key="nova3b",
        working_dir=str(tmp_path),
        permission_mode="acceptEdits",
        mode_policy=get_mode_policy("autonomous"),
    )

    shell_result, shell_success = agent._execute_tool_with_safety(
        "run_command", {"command": "rm -rf ./must_remain"}
    )
    typed_result, typed_success = agent._execute_tool_with_safety(
        "run_process",
        {"argv": [sys.executable, "-c", "print('safe')"], "cwd": str(tmp_path)},
    )

    assert not shell_success
    assert "shell-string execution is disabled" in shell_result
    assert not typed_success
    assert "No supported OS sandbox" in typed_result
    assert "run_command" not in {
        item["function"]["name"] for item in (agent._get_tools() or [])
    }
    assert target.is_dir()


def test_quality_mode_rejects_executor_model_as_reviewer(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path / "state"))
    agent = Agent(
        api_key="test",
        working_dir=str(tmp_path),
        mode_policy=get_mode_policy("quality"),
        workspace_isolation=False,
    )
    monkeypatch.setenv("NEXUS_REVIEW_MODEL_ID", agent.model_cfg["id"])
    monkeypatch.setattr(agent.history, "get_recent_diffs", lambda *_args: "+secure = True")
    agent._active_objective = "Apply a secure change"
    agent.evidence.append(
        kind="file_mutation",
        claim="file changed",
        status="verified",
        raw_output="changed",
    )

    approved, detail = agent._run_independent_review()

    assert approved is False
    assert "different from the executor" in detail


def test_cancelled_dangerous_call_never_executes(tmp_path, monkeypatch):
    """Cancelling removes a pending operation without dispatching it."""
    target = tmp_path / "cancelled_delete"
    target.mkdir()
    old_cwd = os.getcwd()
    try:
        agent = _agent_for_tmp_path(tmp_path, monkeypatch)
        agent._execute_tool_with_safety(
            "run_command",
            {"command": "rm -rf ./cancelled_delete"},
        )
        cancelled, cancel_success = agent.cancel_pending_operation("danger-0001")
    finally:
        os.chdir(old_cwd)

    assert cancel_success
    assert "not executed" in cancelled
    assert target.is_dir()


def test_nova_file_edits_require_nova_guardrail_metadata(tmp_path, monkeypatch):
    """Nova-backed file writes must have passed Nova guardrails before Nexus safety."""
    old_cwd = os.getcwd()
    try:
        agent = _agent_for_tmp_path(tmp_path, monkeypatch)
        result, success = agent._execute_tool_with_safety(
            "write_file",
            {"path": "unguarded.txt", "content": "hello"},
        )
    finally:
        os.chdir(old_cwd)

    assert not success
    assert "without a passing Nova guardrail verdict" in result
    assert not (tmp_path / "unguarded.txt").exists()


def test_nova_guarded_file_edit_still_uses_nexus_safety(tmp_path, monkeypatch):
    """A passed Nova verdict is not enough to bypass Nexus SafetyLayer."""
    old_cwd = os.getcwd()
    try:
        agent = _agent_for_tmp_path(tmp_path, monkeypatch)
        result, success = agent._execute_tool_with_safety(
            "write_file",
            {
                "path": "/etc/nova_should_not_write",
                "content": "hello",
                "_nova_guardrail": {"passed": True, "summary": "test"},
            },
        )
    finally:
        os.chdir(old_cwd)

    assert not success
    assert "BLOCKED" in result


def test_multi_edit_cannot_hide_an_outside_workspace_path(tmp_path, monkeypatch):
    """Every path in a batch is scope-checked, not just the top-level args."""
    outside = tmp_path.parent / f"outside-{tmp_path.name}.txt"
    outside.write_text("before\n")
    old_cwd = os.getcwd()
    try:
        agent = Agent(
            model_key="nova3b",
            working_dir=str(tmp_path),
            permission_mode="acceptEdits",
        )
        result, success = agent._execute_tool_with_safety(
            "multi_edit",
            {
                "edits": [
                    {"path": str(outside), "old_text": "before", "new_text": "after"},
                ],
                "_nova_guardrail": {"passed": True, "summary": "test"},
            },
        )
    finally:
        os.chdir(old_cwd)

    assert not success
    assert "PENDING_CONFIRMATION" in result
    assert outside.read_text() == "before\n"
