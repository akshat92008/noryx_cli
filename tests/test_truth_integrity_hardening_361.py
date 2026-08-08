from __future__ import annotations

import sys
from pathlib import Path

import pytest

from nexus.agent import Agent
from nexus.capabilities import ToolCapability
from nexus.planner import Difficulty, IntentType, PlanningEngine, PlanType, classify_intent
from nexus.tools import TOOL_DEFINITIONS, ToolResult, ToolStatus, execute_tool
from nexus.workspace_journal import (
    ContentAddressedWorkspaceJournal,
    WorkspaceSnapshotError,
)


@pytest.fixture
def agent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Agent:
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path / "state"))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    instance = Agent(
        api_key="test",
        working_dir=str(workspace),
        workspace_isolation=False,
        allow_unisolated_host_process=True,
    )
    instance.mode_policy.require_os_isolation = False
    return instance


def test_external_tools_execute_through_agent_with_structured_truth(agent: Agent):
    class Plugin:
        def get_tool_dispatch(self):
            return {"plugin_echo": lambda **_kwargs: "plugin-ok"}

    class Extension:
        name = "extension_fail"
        description = "typed failure"
        capabilities = ("pure",)
        filesystem = {}

        def invoke(self, _arguments, _context):
            return {"status": "failure", "output": "permission denied", "error": "denied"}

    class MCP:
        def is_mcp_tool(self, name):
            return name == "mcp_fail"

        def call_tool(self, _name, _arguments):
            return {"content": [{"text": "permission denied"}], "isError": True}

    agent.plugin_loader.plugins = {"plugin": Plugin()}
    agent.extensions.loaded = lambda group: [Extension()] if group == "tools" else []
    agent.mcp = MCP()
    agent._register_tool_capability("plugin_echo", frozenset({ToolCapability.PURE}))
    agent._register_tool_capability("extension_fail", frozenset({ToolCapability.PURE}))
    agent._register_tool_capability(
        "mcp_fail",
        frozenset(
            {
                ToolCapability.NETWORK,
                ToolCapability.EXTERNAL_EFFECTS,
                ToolCapability.CONFIRMATION_REQUIRED,
            }
        ),
    )

    output, success = agent._execute_tool_with_safety("plugin_echo", {})
    assert (output, success) == ("plugin-ok", True)

    output, success = agent._execute_tool_with_safety("extension_fail", {})
    assert output == "permission denied"
    assert success is False

    output, success = agent._execute_tool_with_safety("mcp_fail", {}, _user_confirmed=True)
    assert output == "permission denied"
    assert success is False


def test_unknown_external_status_fails_closed():
    from nexus.tool_executor import ToolExecutionController

    converted = ToolExecutionController._external_result(
        {"status": "mystery", "output": "looks fine"},
        source="extension",
    )
    assert converted.status == ToolStatus.FAILURE
    assert converted.success is False
    assert "Invalid extension status" in converted.error


def test_boolean_structured_status_fails_closed():
    result = ToolResult(status=False, output="looks fine")
    assert result.status == ToolStatus.FAILURE
    assert result.success is False
    assert "Invalid structured tool status" in result.error


def test_workspace_enumeration_errors_fail_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    import nexus.workspace_journal as journal_module

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    journal = ContentAddressedWorkspaceJournal(
        workspace,
        preimage_dir=tmp_path / "preimages",
    )

    def broken_walk(*_args, onerror=None, **_kwargs):
        assert onerror is not None
        onerror(PermissionError("denied"))
        return iter(())

    monkeypatch.setattr(journal_module.os, "walk", broken_walk)
    with pytest.raises(WorkspaceSnapshotError, match="Unable to enumerate workspace"):
        journal.capture(store_preimages=True)


def test_restore_preflights_all_preimages_before_mutation(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "state.txt"
    target.write_text("before", encoding="utf-8")
    journal = ContentAddressedWorkspaceJournal(
        workspace,
        preimage_dir=tmp_path / "preimages",
    )
    snapshot = journal.capture(store_preimages=True)
    preimage = Path(snapshot.entries["state.txt"].preimage_path)
    preimage.chmod(0o600)
    preimage.write_text("corrupt", encoding="utf-8")
    target.write_text("after", encoding="utf-8")

    with pytest.raises(WorkspaceSnapshotError, match="Corrupt preimage"):
        journal.restore(snapshot)

    # Preflight failure must happen before the live workspace is touched.
    assert target.read_text(encoding="utf-8") == "after"


def test_internal_legacy_handler_is_structured_at_registry_boundary():
    result = execute_tool("process_status", {"pid": 99999999})
    assert result.status == ToolStatus.FAILURE
    assert result.success is False


def test_command_journal_detects_same_mtime_rewrite(agent: Agent):
    target = Path(agent.working_dir) / "state.txt"
    target.write_text("old", encoding="utf-8")
    original_mtime = target.stat().st_mtime_ns
    code = (
        "from pathlib import Path; import os; "
        "p=Path('state.txt'); p.write_text('new'); "
        f"os.utime(p, ns=({original_mtime},{original_mtime}))"
    )

    output, success = agent._execute_tool_with_safety(
        "run_process",
        {"argv": [sys.executable, "-c", code], "cwd": agent.working_dir},
        _user_confirmed=True,
    )

    assert success is True, output
    assert target.read_text(encoding="utf-8") == "new"
    change = agent.history.get_last_change()
    assert change is not None
    assert change["change_type"] == "modified"
    assert change["before_sha256"] != change["after_sha256"]
    assert change["snapshot_path"]


def test_command_journal_detects_deletion_and_restores_it(agent: Agent):
    target = Path(agent.working_dir) / "delete-me.txt"
    target.write_text("recoverable", encoding="utf-8")

    output, success = agent._execute_tool_with_safety(
        "run_process",
        {
            "argv": [
                sys.executable,
                "-c",
                "from pathlib import Path; Path('delete-me.txt').unlink()",
            ],
            "cwd": agent.working_dir,
        },
        _user_confirmed=True,
    )

    assert success is True, output
    assert not target.exists()
    change = agent.history.get_last_change()
    assert change is not None
    assert change["change_type"] == "deleted"
    assert change["snapshot_path"]

    rollback_ok, _ = agent.history.undo_last_change()
    assert rollback_ok is True
    assert target.read_text(encoding="utf-8") == "recoverable"


def test_failed_command_rolls_back_partial_mutation(agent: Agent):
    target = Path(agent.working_dir) / "atomic.txt"
    target.write_text("before", encoding="utf-8")

    output, success = agent._execute_tool_with_safety(
        "run_process",
        {
            "argv": [
                sys.executable,
                "-c",
                "from pathlib import Path; "
                "Path('atomic.txt').write_text('partial'); raise SystemExit(7)",
            ],
            "cwd": agent.working_dir,
        },
        _user_confirmed=True,
    )

    assert success is False
    assert "Rollback succeeded" in output
    assert target.read_text(encoding="utf-8") == "before"


def test_failed_command_rolls_back_multi_path_transaction(agent: Agent):
    modified = Path(agent.working_dir) / "modified.txt"
    deleted = Path(agent.working_dir) / "deleted.txt"
    created = Path(agent.working_dir) / "created.txt"
    modified.write_text("before-modified", encoding="utf-8")
    deleted.write_text("before-deleted", encoding="utf-8")
    code = (
        "from pathlib import Path; "
        "Path('modified.txt').write_text('partial'); "
        "Path('deleted.txt').unlink(); "
        "Path('created.txt').write_text('partial-new'); "
        "raise SystemExit(9)"
    )

    output, success = agent._execute_tool_with_safety(
        "run_process",
        {"argv": [sys.executable, "-c", code], "cwd": agent.working_dir},
        _user_confirmed=True,
    )

    assert success is False
    assert "Rollback succeeded" in output
    assert modified.read_text(encoding="utf-8") == "before-modified"
    assert deleted.read_text(encoding="utf-8") == "before-deleted"
    assert not created.exists()
    assert all(item.get("transaction_id") == "" for item in agent.history.changes)
    evidence_records = agent.evidence.records()
    assert not any(
        item.get("kind") == "file_mutation" and item.get("metadata", {}).get("transaction_id")
        for item in evidence_records
    )
    verified, detail = agent.evidence.verify_recent(50)
    assert verified is True, detail


def test_failed_command_restores_file_replaced_by_directory(agent: Agent):
    victim = Path(agent.working_dir) / "victim"
    victim.write_text("original-file", encoding="utf-8")
    code = (
        "from pathlib import Path; "
        "p=Path('victim'); p.unlink(); p.mkdir(); "
        "(p/'inside.txt').write_text('partial'); raise SystemExit(11)"
    )

    output, success = agent._execute_tool_with_safety(
        "run_process",
        {"argv": [sys.executable, "-c", code], "cwd": agent.working_dir},
        _user_confirmed=True,
    )

    assert success is False
    assert "Rollback succeeded" in output
    assert victim.is_file()
    assert victim.read_text(encoding="utf-8") == "original-file"


def test_repair_intent_wins_over_add_tests_and_requires_plan():
    request = (
        "Fix the authentication race in src/auth/session.py without changing the "
        "public API and add regression tests"
    )
    assert classify_intent(request) == IntentType.FIX
    analysis = PlanningEngine().analyze(request)
    assert analysis["intent"] == IntentType.FIX
    assert analysis["plan_type"] == PlanType.PLANNED


def test_canonical_plan_uses_only_live_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path / "state"))
    planner = PlanningEngine()
    request = "Fix the authentication race and add regression tests"
    analysis = {
        "intent": IntentType.FIX,
        "difficulty": Difficulty.MODERATE,
        "plan_type": PlanType.PLANNED,
        "skills_needed": ["security", "testing"],
    }
    plan = planner.create_plan(request, analysis)
    live = {definition.name for definition in TOOL_DEFINITIONS}
    requested = {
        tool for step in plan.canonical_plan["steps"] for tool in step.get("allowed_tools", [])
    }
    assert requested <= live
    assert plan.canonical_planning_error == ""
    assert plan.canonical_plan["root_cause_hypotheses"]


@pytest.mark.parametrize(
    "task_text",
    [
        "Build concurrent worker scheduling",
        "Build auth token refresh middleware",
        "Build race-condition diagnostics",
    ],
)
def test_risky_build_requests_do_not_bypass_planning(task_text: str):
    analysis = PlanningEngine().analyze(task_text)
    assert analysis["plan_type"] == PlanType.PLANNED


def test_canonical_security_action_matches_legacy_security_intent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path / "state"))
    planner = PlanningEngine()
    request = "Harden authentication middleware and add regression tests"
    analysis = planner.analyze(request)
    plan = planner.create_plan(request, analysis)
    assert analysis["intent"] == IntentType.SECURITY
    assert plan.canonical_contract["task_type"] == "security_remediation"
    assert plan.intent == IntentType.SECURITY
