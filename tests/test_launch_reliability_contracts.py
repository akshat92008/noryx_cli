"""Offline regression tests for launch-critical autonomy contracts."""

from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

from nexus.agent import Agent
from nexus.budget import BudgetController, BudgetExceeded, BudgetLimits
from nexus.evidence import EvidenceTrail
from nexus.pipeline import ExecutionPipeline
from nexus.planner import (
    Difficulty,
    ExecutionPlan,
    IntentType,
    PlanningEngine,
    PlanType,
    TaskStatus,
)
from nexus.policy import get_mode_policy
from nexus.run_state import RunLedger
from nexus.runtime.kernel import ExecutionKernel
from nexus.subagents.orchestrator import SubagentOrchestrator
from nexus.subagents.templates import SecurityAuditor
from nexus.tools import tool_context, tool_process_run, tool_process_status
from nexus.verification import CheckStatus, VerificationEngine, _shell_executable
from nexus.workspace import GitWorktreeSession, WorkspaceManager


def test_noninteractive_tool_handler_emits_no_rich_ui(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path / "state"))
    agent = Agent(api_key="test", working_dir=str(tmp_path), workspace_isolation=False)
    agent._execute_tool_with_safety = lambda *_args, **_kwargs: ("ok", True)

    def unexpected(*_args, **_kwargs):
        raise AssertionError("interactive UI was called in machine-output mode")

    monkeypatch.setattr("nexus.ui.print_tool_call", unexpected)
    monkeypatch.setattr("nexus.ui.print_tool_result", unexpected)
    monkeypatch.setattr("nexus.ui.print_warning", unexpected)
    results, successes = agent._handle_tool_calls_interactive(
        [{"id": "call-1", "name": "read_file", "arguments": '{"path":"a.py"}'}],
        emit_ui=False,
    )

    assert successes == [True]
    assert results[0]["content"] == "ok"


def test_verification_checks_real_syntax_and_imports(tmp_path, monkeypatch):
    monkeypatch.setenv("PYTHON", sys.executable)
    (tmp_path / "local_module.py").write_text("VALUE = 1\n", encoding="utf-8")
    main = tmp_path / "main.py"
    main.write_text("from local_module import VALUE\n", encoding="utf-8")
    engine = VerificationEngine(str(tmp_path))

    assert engine.verify_syntax().status == CheckStatus.PASSED
    assert engine.verify_imports().status == CheckStatus.PASSED

    main.write_text("import definitely_missing_nexus_module\n", encoding="utf-8")
    missing = engine.verify_imports()
    assert missing.status == CheckStatus.FAILED
    assert "definitely_missing_nexus_module" in missing.output

    main.write_text("def broken(:\n", encoding="utf-8")
    assert engine.verify_syntax().status == CheckStatus.FAILED


def test_verification_is_not_applicable_without_python_sources(tmp_path):
    (tmp_path / "index.js").write_text("console.log('ok')\n", encoding="utf-8")
    engine = VerificationEngine(str(tmp_path))
    assert engine.verify_syntax().status == CheckStatus.NOT_APPLICABLE
    assert engine.verify_imports().status == CheckStatus.NOT_APPLICABLE


def test_windows_verification_uses_cmd_compatible_executable_quoting():
    quoted = _shell_executable(r"C:\Program Files\Python\python.exe", platform="nt")
    assert quoted == r'"C:\Program Files\Python\python.exe"'
    assert "'" not in quoted


def test_evidence_recovers_valid_prefix_and_quarantines_corrupt_suffix(tmp_path):
    trail = EvidenceTrail("session", root=tmp_path)
    trail.append(kind="command", claim="first", status="verified")
    with trail.path.open("ab") as handle:
        handle.write(b'{"partial":')

    appended = trail.append(kind="command", claim="second", status="verified")
    records = trail.records()

    assert appended.id == "ev-000003"
    assert [record["kind"] for record in records] == [
        "command",
        "storage_corruption",
        "command",
    ]
    assert list(trail.path.parent.glob("session.jsonl.corrupt-*.bak"))
    for line in trail.path.read_text(encoding="utf-8").splitlines():
        json.loads(line)


def test_run_ledger_concurrent_appends_have_unique_ids(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = RunLedger("parallel", workspace, root=tmp_path / "state")
    ledger.begin("parallel append", analysis={}, plan={"steps": []})

    with ThreadPoolExecutor(max_workers=8) as executor:
        ids = list(
            executor.map(
                lambda index: ledger.append_event("progress", status="verified", detail=str(index)),
                range(40),
            )
        )

    records, corruption = ledger.read_jsonl("events.jsonl")
    assert corruption is None
    assert len(records) == 40
    assert len(ids) == len(set(ids)) == 40


def test_kernel_records_hosted_model_calls_without_double_accounting(tmp_path):
    class Provider:
        id = "fake"
        model_id = "fake/model"
        attempt_telemetry_enabled = False

        def chat(self, **_kwargs):
            delta = SimpleNamespace(content="done", tool_calls=[])
            return iter(
                [
                    SimpleNamespace(
                        id="request-1",
                        choices=[SimpleNamespace(delta=delta)],
                        usage=SimpleNamespace(prompt_tokens=4, completion_tokens=2, total_tokens=6),
                    )
                ]
            )

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = RunLedger("model-ledger", workspace, root=tmp_path / "state")
    ledger.begin("model call", analysis={}, plan={"steps": []})
    kernel = ExecutionKernel(provider=Provider(), model_id="fake/model", ledger=ledger)

    list(kernel.run([{"role": "user", "content": "hello"}]))
    calls, corruption = ledger.read_jsonl("model_calls.jsonl")

    assert corruption is None
    assert len(calls) == 1
    assert calls[0]["request_id"] == "request-1"
    assert calls[0]["usage"]["total_tokens"] == 6


def test_kernel_keeps_logical_role_alongside_physical_attempts(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ledger = RunLedger("attempt-ledger", workspace, root=tmp_path / "state")
    ledger.begin("model call", analysis={}, plan={"steps": []})

    class Provider:
        id = "observed"
        model_id = "observed/model"
        attempt_telemetry_enabled = True

        def chat(self, **_kwargs):
            ledger.append_model_call(
                role="provider_attempt",
                model=self.model_id,
                provider=self.id,
                status="verified",
            )
            delta = SimpleNamespace(content="done", tool_calls=[])
            return iter(
                [
                    SimpleNamespace(
                        id="request-observed",
                        choices=[SimpleNamespace(delta=delta)],
                        usage=SimpleNamespace(prompt_tokens=2, completion_tokens=1, total_tokens=3),
                    )
                ]
            )

    list(ExecutionKernel(provider=Provider(), model_id="observed/model", ledger=ledger).run([]))
    calls, corruption = ledger.read_jsonl("model_calls.jsonl")

    assert corruption is None
    assert [item["role"] for item in calls] == ["provider_attempt", "executor"]


def test_physical_provider_attempt_budget_is_enforced():
    controller = BudgetController(BudgetLimits(max_hosted_calls=10, max_provider_attempts=2))
    controller.before_hosted_call()
    controller.before_provider_attempt("nvidia", "model-a")
    controller.before_provider_attempt("groq", "model-b")

    try:
        controller.before_provider_attempt("openrouter", "model-c")
    except BudgetExceeded as exc:
        assert "Provider-attempt budget exhausted" in str(exc)
    else:  # pragma: no cover - explicit assertion message
        raise AssertionError("third physical provider attempt was not blocked")

    usage = controller.snapshot()["usage"]
    assert usage["logical_agent_calls"] == 1
    assert usage["actual_provider_attempts"] == 2


def test_planned_hosted_execution_advances_every_step(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path / "state"))
    planner = PlanningEngine()
    plan = planner.create_plan(
        "Build a complex service with tests",
        {
            "intent": IntentType.BUILD,
            "difficulty": Difficulty.COMPLEX,
            "plan_type": PlanType.PLANNED,
            "skills_needed": [],
        },
    )
    ledger = SimpleNamespace(
        record_tasks=lambda *_args, **_kwargs: None,
        record_plan=lambda *_args, **_kwargs: None,
        checkpoint=lambda *_args, **_kwargs: None,
    )
    fake_agent = SimpleNamespace(planner=planner, max_turns=100, run_ledger=ledger)

    def run_step(*_args, **_kwargs):
        current = next(step for step in plan.steps if step.status == TaskStatus.IN_PROGRESS)
        planner.advance_step(current.id, TaskStatus.COMPLETED, "done")
        return "done", [{"type": "model_turn"}]

    fake_agent._run_hosted_turn = run_step
    pipeline = ExecutionPipeline(fake_agent)
    response, events = pipeline._run_hosted_execution(
        "Build a complex service with tests",
        {"intent": IntentType.BUILD},
        plan,
        interactive=False,
        emit_ui=False,
    )

    assert response == "done"
    assert plan.is_complete
    assert all(step.status == TaskStatus.COMPLETED for step in plan.steps)
    assert len(events) == len(plan.steps)
    assert all("git_commit" not in step.tools_needed for step in plan.steps)


def test_simple_build_plan_still_implements_and_verifies():
    planner = PlanningEngine()
    plan = planner.create_plan(
        "Build a small command-line formatter",
        {
            "intent": IntentType.BUILD,
            "difficulty": Difficulty.SIMPLE,
            "plan_type": PlanType.PLANNED,
            "skills_needed": [],
        },
    )

    tool_contracts = [set(step.tools_needed) for step in plan.steps]
    assert any({"write_file", "edit_file"} & tools for tools in tool_contracts)
    assert any("run_process" in tools for tools in tool_contracts)
    assert all("run_command" not in tools for tools in tool_contracts)
    assert all("git_commit" not in tools for tools in tool_contracts)


def test_reviewer_json_parser_fails_closed():
    approved = Agent._parse_review_payload(
        '```json\n{"approved": true, "summary": "looks good", "findings": []}\n```'
    )
    assert approved["approved"] is True

    for invalid in ("not json", '{"approved": "yes", "summary": "x", "findings": []}'):
        try:
            Agent._parse_review_payload(invalid)
        except (ValueError, json.JSONDecodeError):
            pass
        else:  # pragma: no cover - explicit assertion message
            raise AssertionError("malformed review output was accepted")


def test_build_evidence_cannot_satisfy_lint_and_type_contract(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path / "state"))
    agent = Agent(api_key="test", working_dir=str(tmp_path), workspace_isolation=False)
    plan = ExecutionPlan(
        id="strict-evidence",
        goal="check evidence types",
        intent=IntentType.BUILD,
        difficulty=Difficulty.SIMPLE,
        plan_type=PlanType.PLANNED,
        steps=[],
        acceptance_criteria=["Verification completed: Check for lint/type errors"],
    )
    analysis = {
        "intent": IntentType.BUILD,
        "difficulty": Difficulty.SIMPLE,
        "plan_type": PlanType.PLANNED,
        "skills_needed": [],
    }
    agent._begin_managed_run("check evidence types", analysis, plan)
    agent.evidence.append(
        kind="verification_check",
        claim="build passed",
        status="verified",
        metadata={"check_type": "build"},
    )

    report = agent._run_finalizer.finish("done", [])
    assert report["acceptance_criteria"][0]["status"] == "UNVERIFIED"
    assert report["status"] != "VERIFIED"


def test_model_tool_schema_honors_agent_allowlist_and_plan_step(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path / "state"))
    agent = Agent(
        api_key="test",
        working_dir=str(tmp_path),
        allowed_tools=["read_file", "search_code"],
        mode_policy=get_mode_policy("plan"),
    )
    plan = ExecutionPlan(
        id="tools",
        goal="implement",
        intent=IntentType.BUILD,
        difficulty=Difficulty.SIMPLE,
        plan_type=PlanType.PLANNED,
        steps=[],
    )
    step = SimpleNamespace(
        status=TaskStatus.IN_PROGRESS,
        tools_needed=["read_file", "write_file"],
    )
    plan.steps = [step]
    agent._active_plan = plan

    names = {item["function"]["name"] for item in agent._get_tools()}
    assert names == {"read_file", "search_code"}
    assert "write_file" not in names


def test_non_git_apply_rolls_back_partial_copy_failure(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    session = GitWorktreeSession(
        source,
        "rollback-copy",
        state_root=tmp_path / "state",
        force_copy=True,
    )
    isolated = session.create()
    (Path(isolated.path) / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    (Path(isolated.path) / "new.py").write_text("NEW = True\n", encoding="utf-8")

    import nexus.workspace as workspace_module

    original_copy = workspace_module.shutil.copy2
    failed = False

    def fail_first_apply(src, dst, *args, **kwargs):
        nonlocal failed
        if not failed and Path(dst).resolve().is_relative_to(source.resolve()):
            failed = True
            raise OSError("injected copy failure")
        return original_copy(src, dst, *args, **kwargs)

    monkeypatch.setattr(workspace_module.shutil, "copy2", fail_first_apply)
    try:
        session.apply()
    except Exception as exc:
        assert "restored" in str(exc).lower()
    else:  # pragma: no cover - explicit assertion message
        raise AssertionError("injected apply failure did not propagate")

    assert (source / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (source / "new.py").exists()


def test_git_workspace_recovery_diff_includes_untracked_files(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.email", "nexus@example.test"], cwd=source, check=True)
    subprocess.run(["git", "config", "user.name", "Nexus Test"], cwd=source, check=True)
    (source / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=source, check=True)
    session = GitWorktreeSession(source, "untracked-diff", state_root=tmp_path / "state")
    isolated = Path(session.create().path)
    (isolated / "new_module.py").write_text("NEW = True\n", encoding="utf-8")

    diff = session.diff()

    assert "new file mode" in diff
    assert "b/new_module.py" in diff
    assert "+NEW = True" in diff
    session.discard()


def test_subagent_runtime_enforces_template_tools_and_turn_limit(tmp_path, monkeypatch):
    captured = {}

    class FakeAgent:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def set_system_prompt(self, _prompt):
            return None

        def run_non_interactive(self, _prompt):
            return "review complete", []

        def close(self):
            return None

    monkeypatch.setattr("nexus.agent.Agent", FakeAgent)
    subagent = SecurityAuditor("audit auth", working_dir=str(tmp_path))
    result = SubagentOrchestrator(
        api_key="test",
        model_id="glm-5.2",
        working_dir=str(tmp_path),
    ).run_single(subagent)

    assert result.succeeded
    assert captured["allowed_tools"] == subagent.allowed_tools
    assert captured["max_turns"] == subagent.max_iterations
    assert captured["permission_mode"] == "plan"


def test_hosted_write_test_review_reaches_verified_without_stdout_noise(
    tmp_path, monkeypatch, capsys
):
    test_policy = get_mode_policy("autonomous")
    test_policy.require_os_isolation = False
    test_policy.allow_shell_command = True
    state_root = tmp_path.parent / f"{tmp_path.name}-state"
    monkeypatch.setenv("NEXUS_HOME", str(state_root))
    monkeypatch.setenv("PYTHON", sys.executable)
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='hosted-contract'\nversion='0.0.0'\n"
        "[tool.pytest.ini_options]\ntestpaths=['tests']\n",
        encoding="utf-8",
    )
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_hello.py").write_text(
        "from hello import message\n\n\ndef test_message():\n    assert message() == 'hello-nexus'\n",
        encoding="utf-8",
    )

    class HostedFake:
        id = "hosted-fake"
        model_id = "fake/model"
        attempt_telemetry_enabled = False

        def __init__(self):
            self.calls = 0

        @staticmethod
        def _chunk(*, content=None, tool_name=None, arguments=None, request_id="request"):
            tool_calls = []
            if tool_name:
                tool_calls = [
                    SimpleNamespace(
                        index=0,
                        id=f"call-{tool_name}",
                        function=SimpleNamespace(
                            name=tool_name,
                            arguments=json.dumps(arguments or {}),
                        ),
                    )
                ]
            return SimpleNamespace(
                id=request_id,
                choices=[
                    SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=tool_calls))
                ],
                usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            )

        def chat(self, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                chunk = self._chunk(
                    tool_name="write_file",
                    arguments={
                        "path": "hello.py",
                        "content": "def message() -> str:\n    return 'hello-nexus'\n",
                    },
                    request_id="request-write",
                )
            elif self.calls == 2:
                chunk = self._chunk(
                    tool_name="run_command",
                    arguments={
                        "command": f"{sys.executable} -m pytest -q",
                        "cwd": str(tmp_path),
                    },
                    request_id="request-test",
                )
            else:
                chunk = self._chunk(
                    content="Implemented and tested hello.py.",
                    request_id="request-final",
                )
            return iter([chunk])

        def chat_sync(self, **_kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content=(
                                '{"approved": true, "summary": "implementation and tests '
                                'match the objective", "findings": []}'
                            )
                        )
                    )
                ]
            )

    agent = Agent(
        api_key="test",
        working_dir=str(tmp_path),
        permission_mode="acceptEdits",
        mode_policy=test_policy,
        workspace_isolation=False,
        allow_unisolated_host_process=True,
    )
    agent.client = HostedFake()
    agent.planner.analyze = lambda _prompt: {
        "intent": IntentType.BUILD,
        "difficulty": Difficulty.SIMPLE,
        "plan_type": "direct",
        "skills_needed": [],
    }

    result = ExecutionPipeline(agent).run(
        "Create hello.py that returns hello-nexus and make the test pass",
        interactive=False,
        emit_ui=False,
    )
    captured = capsys.readouterr()
    report = agent.run_ledger.resume_summary()["final_report"]
    model_calls, corruption = agent.run_ledger.read_jsonl("model_calls.jsonl")

    assert captured.out == ""
    assert result.success is True
    assert report["status"] == "VERIFIED"
    assert corruption is None
    assert len(model_calls) == 4
    assert {item["role"] for item in model_calls} == {"executor", "independent_reviewer"}
    assert json.loads(json.dumps({"success": result.success, "run": report}))["success"] is True


def test_agent_close_archives_and_removes_isolated_workspace(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    state = tmp_path / "state"
    monkeypatch.setenv("NEXUS_HOME", str(state))
    agent = Agent(
        api_key="test",
        working_dir=str(source),
        workspace_isolation=True,
    )
    isolated = Path(agent.working_dir)
    (isolated / "app.py").write_text("VALUE = 2\n", encoding="utf-8")

    report = agent.close(discard_workspace=True)

    assert report["workspace_discarded"] is True
    assert not isolated.exists()
    assert Path(report["recovery_patch"]).is_file()
    assert "VALUE = 2" in Path(report["recovery_patch"]).read_text(encoding="utf-8")


def test_agent_close_stops_owned_background_processes(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path / "state"))
    agent = Agent(api_key="test", working_dir=str(tmp_path), workspace_isolation=False)
    command = f'{sys.executable} -c "import time; time.sleep(60)"'
    with tool_context(agent.working_dir, agent.history, agent.conversation_id):
        result = tool_process_run(
            command,
            require_os_isolation=False,
            allow_unisolated_host_process=True,
        )
    pid = int(next(line for line in result.splitlines() if "PID:" in line).split(":", 1)[1])

    report = agent.close()

    assert report["background_processes_stopped"] == [pid]
    assert "not a Nexus-managed background process" in tool_process_status(pid)


def test_workspace_manager_resolves_custom_state_root(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    state = tmp_path / "custom-state"
    session = GitWorktreeSession(source, "custom-root", state_root=state)
    info = session.create()

    resolved = WorkspaceManager(state).resolve_worktree("custom-root")
    assert resolved is not None
    assert resolved.info == info
    assert resolved.info_path == state / "worktrees" / "custom-root.json"
