"""
Tests for the three Agent service extraction modules:
  - nexus.tool_executor.ToolExecutionController
  - nexus.run_finalizer.RunFinalizer, EvidenceSummary, classify_evidence, determine_status
  - nexus.turn_coordinator.TurnCoordinator, TurnResult, parse_tool_call_arguments, validate_tool_call

All tests are hermetic — no provider credentials required.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from nexus.run_finalizer import (
    EvidenceClass,
    EvidenceSummary,
    RunFinalizer,
    make_finalizer,
)
from nexus.tool_executor import ToolExecutionController, make_controller
from nexus.turn_coordinator import (
    TurnCoordinator,
    TurnRequest,
    TurnResult,
    make_coordinator,
)

# ─── Shared fake agent ────────────────────────────────────────────────────────


def _fake_agent(tmp_path: Path):
    """Build a minimal fake Agent-like object for service tests."""
    agent = SimpleNamespace(
        working_dir=str(tmp_path),
        run_context=SimpleNamespace(
            source_root=tmp_path,
            workspace_root=tmp_path,
            restricted_directories=[],
        ),
        model_key="test",
        model_cfg={"name": "test", "id": "test"},
        mode_policy=SimpleNamespace(allow_shell_command=True, require_os_isolation=False, may_edit=True),
        _enforce_plan_tool_contract=False,
        history=[],
        conversation_id="test-conv",
        planner=SimpleNamespace(get_plan_context=lambda: ""),
        evidence=SimpleNamespace(records=lambda: []),
        hooks=SimpleNamespace(fire=lambda *a, **kw: None),
        reflection=None,
        _tool_capabilities={"read_file": SimpleNamespace(requires=lambda x: False)},
        _external_tool_path_arguments={},
        disallowed_tools=set(),
        allowed_tools=None,
        _pending_confirmations={},
        _execute_tool_with_safety=lambda name, args, **kw: ("ok", True),
        _run_single_turn=lambda messages, emit_ui=True: ("done", []),
        capabilities={"read_file": {}},
        _permissions_used=set(),
        _network_calls=[],
        _active_plan=None,
        policy=SimpleNamespace(decide=lambda *a, **kw: SimpleNamespace(action="allow", reason="")),
        extensions=SimpleNamespace(loaded=lambda x: []),
        additional_dirs=[],
        plugin_loader=SimpleNamespace(plugins={}),
        mcp=SimpleNamespace(is_mcp_tool=lambda x: False),
        context_mgr=SimpleNamespace(
            track_file_access=lambda *a, **kw: None,
            track_file_imports=lambda *a, **kw: None,
            summarize_file=lambda *a, **kw: None,
        ),
    )
    agent._run_finalizer = MagicMock()
    agent._run_finalizer.finish = lambda *a, **kw: {"status": "VERIFIED"}
    return agent


# ─── ToolExecutionController ─────────────────────────────────────────────────


class TestToolExecutionController:
    def test_mutation_tool_detection(self, tmp_path):
        agent = _fake_agent(tmp_path)
        ctrl = ToolExecutionController(agent)
        assert ctrl.is_mutation_tool("write_file") is True
        assert ctrl.is_mutation_tool("edit_file") is True
        assert ctrl.is_mutation_tool("multi_edit") is True
        assert ctrl.is_mutation_tool("read_file") is False

    def test_read_tool_detection(self, tmp_path):
        agent = _fake_agent(tmp_path)
        ctrl = ToolExecutionController(agent)
        assert ctrl.is_read_tool("read_file") is True
        assert ctrl.is_read_tool("list_directory") is True
        assert ctrl.is_read_tool("repo_context") is True
        assert ctrl.is_read_tool("write_file") is False

    def test_needs_network_tag_curl(self, tmp_path):
        agent = _fake_agent(tmp_path)
        ctrl = ToolExecutionController(agent)
        assert ctrl.needs_network_tag("curl https://example.com") is True

    def test_needs_network_tag_git_clone(self, tmp_path):
        agent = _fake_agent(tmp_path)
        ctrl = ToolExecutionController(agent)
        assert ctrl.needs_network_tag("git clone https://github.com/foo/bar") is True

    def test_needs_network_tag_pip_install(self, tmp_path):
        agent = _fake_agent(tmp_path)
        ctrl = ToolExecutionController(agent)
        assert ctrl.needs_network_tag("pip install requests") is True

    def test_no_network_tag_for_local_commands(self, tmp_path):
        agent = _fake_agent(tmp_path)
        ctrl = ToolExecutionController(agent)
        assert ctrl.needs_network_tag("python setup.py build") is False
        assert ctrl.needs_network_tag("pytest tests/") is False
        assert ctrl.needs_network_tag("ls -la") is False

    def test_describe_pipeline_shape(self, tmp_path):
        agent = _fake_agent(tmp_path)
        ctrl = ToolExecutionController(agent)
        desc = ctrl.describe_pipeline()
        assert "stages" in desc
        assert len(desc["stages"]) >= 8
        assert "execute_tool" in desc["stages"]
        assert "mutation_tools" in desc
        assert "read_tools" in desc

    def test_execute_delegates_to_agent(self, tmp_path):
        agent = _fake_agent(tmp_path)
        ctrl = ToolExecutionController(agent)
        
        # Mock the dispatch to avoid actually reading files in this unit test
        from nexus.tools import ToolResult, ToolStatus
        ctrl._dispatch_tool_execution = lambda name, args: ToolResult(status=ToolStatus.SUCCESS, output="ok")
        
        result, ok = ctrl.execute("read_file", {"path": "a.py"})
        assert result == "ok"
        assert ok is True

    def test_make_controller_attaches_to_agent(self, tmp_path):
        agent = _fake_agent(tmp_path)
        ctrl = make_controller(agent)
        assert hasattr(agent, "_tool_controller")
        assert agent._tool_controller is ctrl


# ─── RunFinalizer ─────────────────────────────────────────────────────────────


class TestRunFinalizer:
    def test_classify_evidence_verified_mutation(self):
        records = [
            {"kind": EvidenceClass.MUTATION, "id": "m1", "status": "verified"},
        ]
        summary = RunFinalizer.classify_evidence(records)
        assert len(summary.verified_mutations) == 1
        assert not summary.failed_evidence

    def test_classify_evidence_failed_mutation_superseded_by_success(self):
        """A failed mutation followed by a verified mutation with the same ID
        should not appear in failed_evidence."""
        records = [
            {"kind": EvidenceClass.MUTATION, "id": "m1", "status": "failed"},
            {"kind": EvidenceClass.MUTATION, "id": "m1", "status": "verified"},
        ]
        summary = RunFinalizer.classify_evidence(records)
        assert len(summary.verified_mutations) == 1
        assert not summary.failed_evidence

    def test_classify_evidence_all_failed(self):
        records = [
            {"kind": EvidenceClass.MUTATION, "id": "m1", "status": "failed"},
            {"kind": EvidenceClass.VERIFICATION, "id": "v1", "status": "failed"},
        ]
        summary = RunFinalizer.classify_evidence(records)
        assert summary.has_failures is True
        assert summary.has_any_success is False

    def test_classify_evidence_passing_command(self):
        records = [
            {"kind": EvidenceClass.COMMAND, "status": "verified", "exit_code": 0, "command": "pytest"},
        ]
        summary = RunFinalizer.classify_evidence(records)
        assert len(summary.passing_commands) == 1
        assert not summary.failed_evidence

    def test_classify_evidence_behavioral_superseded(self):
        records = [
            {"kind": EvidenceClass.BEHAVIORAL, "tool": "run_command", "status": "failed"},
            {"kind": EvidenceClass.BEHAVIORAL, "tool": "run_command", "status": "verified"},
        ]
        summary = RunFinalizer.classify_evidence(records)
        assert len(summary.passing_behavioral) == 1
        assert not summary.failed_evidence

    def test_classify_evidence_routing_excluded_from_failures(self):
        records = [
            {"kind": "routing", "status": "failed"},
        ]
        summary = RunFinalizer.classify_evidence(records)
        assert not summary.failed_evidence

    def test_classify_evidence_independent_review_excluded_from_failures(self):
        records = [
            {"kind": EvidenceClass.REVIEW, "status": "failed"},
        ]
        summary = RunFinalizer.classify_evidence(records)
        assert not summary.failed_evidence

    def test_determine_status_verified(self):
        summary = EvidenceSummary(
            verified_mutations=[{"id": "m1"}],
        )
        assert RunFinalizer.determine_status(summary) == "VERIFIED"

    def test_determine_status_failed(self):
        summary = EvidenceSummary(
            failed_evidence=[{"id": "x"}],
        )
        assert RunFinalizer.determine_status(summary) == "FAILED"

    def test_determine_status_partially_verified(self):
        summary = EvidenceSummary(
            verified_mutations=[{"id": "m1"}],
            failed_evidence=[{"id": "f1"}],
        )
        assert RunFinalizer.determine_status(summary) == "PARTIALLY_VERIFIED"

    def test_determine_status_blocked(self):
        summary = EvidenceSummary()
        assert RunFinalizer.determine_status(summary, blocked=True) == "BLOCKED"

    def test_determine_status_awaiting_approval(self):
        summary = EvidenceSummary()
        assert (
            RunFinalizer.determine_status(summary, awaiting_approval=True)
            == "AWAITING_APPROVAL"
        )

    def test_determine_status_unverified_empty(self):
        summary = EvidenceSummary()
        assert RunFinalizer.determine_status(summary) == "UNVERIFIED"


    def test_make_finalizer_attaches_to_agent(self, tmp_path):
        agent = _fake_agent(tmp_path)
        fin = make_finalizer(agent)
        assert hasattr(agent, "_run_finalizer")
        assert agent._run_finalizer is fin


# ─── TurnCoordinator ──────────────────────────────────────────────────────────


class TestTurnCoordinator:
    def test_parse_tool_call_arguments_valid_json(self):
        result = TurnCoordinator.parse_tool_call_arguments('{"path": "a.py", "n": 42}')
        assert result == {"path": "a.py", "n": 42}

    def test_parse_tool_call_arguments_empty_string(self):
        assert TurnCoordinator.parse_tool_call_arguments("") == {}

    def test_parse_tool_call_arguments_none(self):
        assert TurnCoordinator.parse_tool_call_arguments(None) == {}

    def test_parse_tool_call_arguments_invalid_json(self):
        assert TurnCoordinator.parse_tool_call_arguments("{broken") == {}

    def test_parse_tool_call_arguments_non_dict_json(self):
        # If the JSON is valid but not a dict, return empty
        assert TurnCoordinator.parse_tool_call_arguments("[1, 2, 3]") == {}

    def test_validate_tool_call_valid(self):
        call = {"id": "call-1", "name": "read_file", "arguments": "{}"}
        ok, err = TurnCoordinator.validate_tool_call(call)
        assert ok is True
        assert err == ""

    def test_validate_tool_call_missing_name(self):
        call = {"id": "call-1", "arguments": "{}"}
        ok, err = TurnCoordinator.validate_tool_call(call)
        assert ok is False
        assert "name" in err

    def test_validate_tool_call_missing_id(self):
        call = {"name": "read_file", "arguments": "{}"}
        ok, err = TurnCoordinator.validate_tool_call(call)
        assert ok is False
        assert "id" in err

    def test_validate_tool_call_not_a_dict(self):
        ok, err = TurnCoordinator.validate_tool_call("not a dict")  # type: ignore
        assert ok is False
        assert "dict" in err

    def test_turn_result_has_tool_calls_property(self):
        result = TurnResult(tool_calls=[{"id": "c1", "name": "read_file"}])
        assert result.has_tool_calls is True
        empty = TurnResult()
        assert empty.has_tool_calls is False

    def test_turn_result_total_tokens(self):
        result = TurnResult(prompt_tokens=100, completion_tokens=200)
        assert result.total_tokens == 300

    def test_run_turn_delegates_to_agent(self, tmp_path):
        agent = _fake_agent(tmp_path)
        coordinator = TurnCoordinator(agent)
        request = TurnRequest(
            messages=[{"role": "user", "content": "hello"}],
        )
        result = coordinator.run_turn(request)
        assert isinstance(result, TurnResult)
        assert result.content == "done"
        assert result.success is True

    def test_describe_returns_agent_info(self, tmp_path):
        agent = _fake_agent(tmp_path)
        coordinator = TurnCoordinator(agent)
        desc = coordinator.describe()
        assert desc["service"] == "TurnCoordinator"
        assert desc["agent_model"] == "test"

    def test_make_coordinator_attaches_to_agent(self, tmp_path):
        agent = _fake_agent(tmp_path)
        coord = make_coordinator(agent)
        assert hasattr(agent, "_turn_coordinator")
        assert agent._turn_coordinator is coord

    def test_turn_request_defaults(self):
        request = TurnRequest(messages=[])
        assert request.max_tokens == 4096
        assert request.stream is True
        assert request.emit_ui is True
        assert request.turn_index == 0

    def test_turn_result_success_false_on_error(self):
        result = TurnResult(success=False, error="model unavailable")
        assert result.has_tool_calls is False
        assert result.total_tokens == 0
        assert result.error == "model unavailable"
