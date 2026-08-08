"""Regression coverage for the public-launch hardening layer."""

from __future__ import annotations

import json
import sys
import urllib.error
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexus.agent import Agent
from nexus.benchmark import BenchmarkRunner, BenchmarkSuite
from nexus.budget import BudgetController, BudgetedClient, BudgetLimits
from nexus.nova_backend import NovaBackendResult, NovaToolProposal
from nexus.pipeline import ExecutionPipeline
from nexus.planner import IntentType
from nexus.policy import get_mode_policy
from nexus.preflight import BackendProbe
from nexus.tools import tool_web_fetch


def test_web_fetch_blocks_non_http_and_loopback_urls():
    assert "blocked" in tool_web_fetch("file:///etc/passwd").lower()
    assert "blocked" in tool_web_fetch("http://127.0.0.1:8080/admin").lower()
    assert "loopback" in tool_web_fetch("http://127.0.0.1:8080/admin").lower()


def test_custom_model_requires_explicit_provider_model_id(tmp_path, monkeypatch):
    monkeypatch.delenv("NEXUS_MODEL_ID", raising=False)
    with pytest.raises(ValueError, match="require --model-id"):
        Agent(model_key="custom", working_dir=str(tmp_path))


def test_hosted_agent_does_not_require_nova(tmp_path):
    agent = Agent(
        api_key="test",
        model_key="glm-5.2",
        working_dir=str(tmp_path),
        local_intern_mode="off",
    )
    assert agent.local_intern_enabled is False
    assert agent._should_use_two_node({"intent": IntentType.FIX}) is False


def test_auto_local_intern_activates_only_after_successful_probe(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "nexus.preflight.probe_ollama",
        lambda *_args, **_kwargs: BackendProbe(True, "ollama", "ready", "available"),
    )
    agent = Agent(
        api_key="test",
        model_key="glm-5.2",
        working_dir=str(tmp_path),
        local_intern_mode="auto",
    )
    assert agent.local_intern_enabled is True
    assert agent._should_use_two_node({"intent": IntentType.FIX}) is True


def test_plugin_activation_is_explicit(tmp_path):
    disabled = Agent(api_key="test", working_dir=str(tmp_path), plugins_enabled=False)
    enabled = Agent(api_key="test", working_dir=str(tmp_path), plugins_enabled=True)
    assert disabled._plugins_enabled is False
    assert enabled._plugins_enabled is True


def test_budget_estimates_usage_when_provider_omits_it():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="implemented successfully", tool_calls=[])
            )
        ]
    )

    class Client:
        def chat(self, *args, **kwargs):
            return response

    controller = BudgetController(BudgetLimits(max_hosted_calls=1))
    client = BudgetedClient(Client(), controller)
    client.chat(
        model_id="test",
        messages=[{"role": "user", "content": "fix the test"}],
        stream=False,
    )
    usage = controller.snapshot()["usage"]
    assert usage["prompt_tokens"] > 0
    assert usage["completion_tokens"] > 0


def test_custom_openai_compatible_model_initializes(tmp_path, monkeypatch):
    monkeypatch.setenv("NEXUS_OPENAI_BASE_URL", "https://provider.example/v1")
    monkeypatch.setenv("NEXUS_OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("NEXUS_MODEL_ID", "vendor/frontier-model")
    agent = Agent(model_key="custom", working_dir=str(tmp_path))
    assert agent.model_cfg["id"] == "vendor/frontier-model"
    assert agent.client.model_id == "hosted"


def test_no_tools_is_applied_inside_agent_configuration(tmp_path):
    agent = Agent(
        api_key="test",
        working_dir=str(tmp_path),
        tools_enabled=False,
    )
    assert agent.model_cfg["supports_tools"] is False
    assert agent._get_tools() is None


def test_benchmark_reports_local_backend_preflight_failure(
    tmp_path: Path,
    monkeypatch,
):
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / "main.py").write_text("print('ok')\n", encoding="utf-8")
    manifest = tmp_path / "suite.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "nexus.benchmark.v1",
                "name": "preflight",
                "tasks": [
                    {
                        "id": "one",
                        "category": "single-file-edit",
                        "prompt": "Change the output",
                        "repository": str(repository),
                        "verification": [["python", "main.py"]],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("NEXUS_MODEL", "nova")

    def offline(*_args, **_kwargs):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr("urllib.request.urlopen", offline)
    report = BenchmarkRunner(BenchmarkSuite.load(manifest)).run()
    result = report.results[0]
    assert result.status == "INVALID_CONFIGURATION"
    assert result.failure_phase == "provider_preflight"
    assert result.environment_failure is True
    assert "ollama" in result.detail.lower()


def test_web_app_propagates_runtime_options(tmp_path):
    from nexus.webapp import server

    server.create_app(
        api_key="test",
        model="glm-5.2",
        working_dir=str(tmp_path),
        model_id_override="provider/frontier",
        local_intern_mode="off",
        enable_nova_fallback=False,
        plugins_enabled=False,
        tools_enabled=False,
    )
    agent = server._get_agent("launch-test")
    assert agent.model_cfg["id"] == "provider/frontier"
    assert agent.local_intern_mode == "off"
    assert agent.local_intern_enabled is False
    assert agent.model_cfg["supports_tools"] is False
    assert "launch-test" in server._agent_busy


def test_custom_endpoint_preflight_rejects_unsafe_scheme(monkeypatch):
    from nexus.preflight import probe_hosted

    monkeypatch.setenv("NEXUS_OPENAI_BASE_URL", "file:///tmp/provider")
    monkeypatch.setenv("NEXUS_OPENAI_API_KEY", "test")
    result = probe_hosted()
    assert result.ready is False
    assert result.code == "custom_url_invalid"


def test_direct_nova_executes_declared_test_and_reaches_verified_status(
    tmp_path, monkeypatch
):
    test_policy = get_mode_policy("autonomous")
    test_policy.require_os_isolation = False
    test_policy.allow_shell_command = True
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path / "state"))
    (tmp_path / "verify.py").write_text(
        "from answer import ANSWER\nassert ANSWER == 42\n",
        encoding="utf-8",
    )
    result = NovaBackendResult(
        raw_output="guarded model response",
        assistant_text="Implemented answer.py and declared its acceptance test.",
        guardrail_output="all deterministic gates passed",
        test_command=f"{sys.executable} verify.py",
        proposals=[
            NovaToolProposal(
                name="write_file",
                args={
                    "path": "answer.py",
                    "content": "ANSWER = 42\n",
                    "_nova_guardrail": {"passed": True, "summary": "validated"},
                },
                source_path="answer.py",
                guardrail_summary="validated",
            )
        ],
    )
    monkeypatch.setattr("nexus.nova_backend.NovaPipelineBackend.run", lambda *_args: result)
    agent = Agent(
        model_key="nova3b",
        working_dir=str(tmp_path),
        permission_mode="acceptEdits",
        mode_policy=test_policy,
        workspace_isolation=False,
        allow_unisolated_host_process=True,
    )

    pipeline_result = ExecutionPipeline(agent).run("Create answer.py with ANSWER = 42")
    report = agent.run_ledger.resume_summary()["final_report"]
    test_checks = [
        item for item in agent.evidence.records() if item.get("tool") == "model_declared_test"
    ]

    assert (tmp_path / "answer.py").read_text(encoding="utf-8") == "ANSWER = 42\n"
    assert test_checks[-1]["status"] == "verified"
    assert test_checks[-1]["exit_code"] == 0
    assert pipeline_result.success is True
    assert report["status"] == "VERIFIED"
    assert report["outcome"] == "COMPLETED_VERIFIED"
    assert report["metadata"]["model_calls"] == 1
    assert report["metadata"]["tests_executed"] >= 2


def test_default_command_policy_requires_approval_and_blocks_host_read(tmp_path):
    agent = Agent(api_key="test", working_dir=str(tmp_path), permission_mode="default")
    pending, success = agent._execute_tool_with_safety(
        "run_process", {"argv": ["cat", "/etc/hostname"], "cwd": "."}
    )
    assert success is False
    assert "PENDING_CONFIRMATION" in pending
    assert "not executed" in pending

    blocked, success = agent.confirm_pending_operation()
    assert success is False
    assert "escapes the authorized workspace" in blocked


def test_run_context_blocks_absolute_and_symlink_escape(tmp_path):
    from nexus.run_context import RunContext, run_context_scope
    from nexus.tools import tool_read_file

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "inside.txt").write_text("inside", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret-outside", encoding="utf-8")
    link = workspace / "escape.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")

    context = RunContext.create(source_root=workspace, workspace_root=workspace)
    with run_context_scope(context):
        assert "inside" in tool_read_file("inside.txt").output
        absolute = tool_read_file(str(outside)).output
        symlinked = tool_read_file("escape.txt").output

    assert "outside authorized roots" in absolute
    assert "outside authorized roots" in symlinked


def test_sandbox_preflight_rejects_host_paths_without_spawning(tmp_path, monkeypatch):
    from nexus.sandbox import CommandSpec, SandboxRunner

    runner = SandboxRunner(tmp_path)
    spawned = False

    def fail_if_spawned(*_args, **_kwargs):
        nonlocal spawned
        spawned = True
        raise AssertionError("subprocess must not start for a host-path escape")

    monkeypatch.setattr("nexus.sandbox.subprocess.run", fail_if_spawned)
    result = runner.run(
        CommandSpec.create(["cat", "/etc/hostname"], tmp_path, require_os_isolation=False)
    )

    assert result.success is False
    assert result.backend.value == "blocked"
    assert "escapes the authorized workspace" in result.blocked_reason
    assert spawned is False


def test_quality_mode_rejects_local_nova_without_independent_reviewer(tmp_path):
    from nexus.pipeline import ExecutionPipeline

    policy = get_mode_policy("quality")
    agent = Agent(
        model_key="nova3b",
        working_dir=str(tmp_path),
        mode_policy=policy,
        permission_mode="acceptEdits",
    )
    stage = ExecutionPipeline(agent)._stage_review("nova", verification_succeeded=True)

    assert stage.success is False
    assert stage.metadata["review_assurance"] == "deterministic_only"
    assert "independent semantic reviewer" in stage.error


def test_local_only_nova_labels_assurance_as_deterministic_only(tmp_path):
    from nexus.pipeline import ExecutionPipeline

    policy = get_mode_policy("local-only")
    agent = Agent(
        model_key="nova3b",
        working_dir=str(tmp_path),
        mode_policy=policy,
        permission_mode="acceptEdits",
    )
    stage = ExecutionPipeline(agent)._stage_review("nova", verification_succeeded=True)

    assert stage.success is True
    assert stage.metadata["review_assurance"] == "deterministic_only"
    assert stage.metadata["independent_semantic_review"] is False


@pytest.mark.parametrize(
    "argv",
    [
        ["bash", "-c", "cat ~/.ssh/id_rsa"],
        ["bash", "-c", "cat $HOME/.config/token"],
        [sys.executable, "-c", "print(open('/etc/passwd').read())"],
        ["cat", "/home/example/.aws/credentials"],
        ["cat", "../outside-secret"],
    ],
)
def test_sandbox_blocks_common_credential_exfiltration_paths_before_spawn(
    tmp_path, monkeypatch, argv
):
    from nexus.sandbox import CommandSpec, SandboxRunner

    def fail_if_spawned(*_args, **_kwargs):
        raise AssertionError("unsafe command reached subprocess.run")

    monkeypatch.setattr("nexus.sandbox.subprocess.run", fail_if_spawned)
    result = SandboxRunner(tmp_path).run(
        CommandSpec.create(argv, tmp_path, require_os_isolation=False)
    )

    assert result.success is False
    assert result.backend.value == "blocked"
    assert result.blocked_reason


def test_macos_profile_has_no_global_file_read_grant(tmp_path):
    import platform

    from nexus.sandbox import CommandSpec, SandboxRunner

    runner = SandboxRunner(tmp_path)
    command, profile = runner._macos_command(
        CommandSpec.create(["python", "-V"], tmp_path), tmp_path
    )
    try:
        content = profile.read_text(encoding="utf-8")
    finally:
        profile.unlink(missing_ok=True)

    assert command[0] == "sandbox-exec"
    assert "(allow file-read*)" not in content
    assert "(allow file-read* (subpath" in content
    assert "(allow file-read-data)" not in content
    assert '(subpath "/etc")' not in content

    if platform.system().lower() == "darwin":
        # Create a readable file inside the workspace
        inside_file = tmp_path / "inside.txt"
        inside_file.write_text("hello", encoding="utf-8")
        
        # Create a secret file outside the workspace (the host-wide temp tree is not permitted)
        outside_file = Path.home() / ".nexus_test_sandbox_escape.txt"
        outside_file.write_text("secret", encoding="utf-8")
        
        try:
            import subprocess

            def _run_raw_sandbox(argv, network=True):
                # Bypass the path inspector to directly test the OS sandbox
                cmd_spec = CommandSpec.create(argv, tmp_path, network=network)
                cmd, profile_path = runner._macos_command(cmd_spec, tmp_path)
                try:
                    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(tmp_path))
                    return result.returncode, result.stdout, result.stderr
                finally:
                    profile_path.unlink(missing_ok=True)

            # 1. Sandboxed process that successfully reads the first
            code, stdout, stderr = _run_raw_sandbox(["cat", "inside.txt"])
            assert code == 0
            assert "hello" in stdout

            # 2. Sandboxed process that is denied access to the second
            code, stdout, stderr = _run_raw_sandbox(["cat", str(outside_file)])
            assert code != 0
            assert "Operation not permitted" in stderr or "No such file" in stderr

            # 3. Sandboxed process that cannot write outside the workspace
            code, stdout, stderr = _run_raw_sandbox(["sh", "-c", f"echo 'test' > {outside_file}"])
            assert code != 0
            assert "Operation not permitted" in stderr or "No such file" in stderr

            # 4. Network check for network-disabled execution
            code, stdout, stderr = _run_raw_sandbox(["curl", "-s", "--max-time", "1", "http://1.1.1.1"], network=False)
            assert code != 0
            # Network drops often result in timeout or specific curl errors
            assert "Operation not permitted" in stderr or code in (6, 7, 28)

        finally:
            outside_file.unlink(missing_ok=True)


def test_sandbox_runner_blocks_relative_symlink_escape(tmp_path):
    from nexus.sandbox import CommandSpec, SandboxRunner

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    
    link = workspace / "escape.txt"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are unavailable on this platform")
        
    runner = SandboxRunner(workspace)
    # Using relative path to the symlink
    result = runner.run(CommandSpec.create(["cat", "escape.txt"], workspace))
    
    assert result.success is False
    assert result.backend.value == "blocked"
    assert "escapes the authorized workspace" in result.blocked_reason


def test_run_context_is_isolated_between_concurrent_agent_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    from nexus.run_context import RunContext, run_context_scope
    from nexus.tools import tool_read_file

    roots = []
    for index in range(2):
        root = tmp_path / f"workspace-{index}"
        root.mkdir()
        (root / "value.txt").write_text(f"workspace-{index}", encoding="utf-8")
        roots.append(root)

    def read(root):
        context = RunContext.create(source_root=root, workspace_root=root)
        with run_context_scope(context):
            return tool_read_file("value.txt").output

    with ThreadPoolExecutor(max_workers=2) as executor:
        outputs = list(executor.map(read, roots))

    assert "workspace-0" in outputs[0]
    assert "workspace-1" in outputs[1]
    assert "workspace-1" not in outputs[0]
    assert "workspace-0" not in outputs[1]


def test_dynamic_capability_registry_is_agent_scoped(tmp_path):
    from nexus.capabilities import ToolCapability

    first = Agent(api_key="test", working_dir=str(tmp_path / "first"))
    second = Agent(api_key="test", working_dir=str(tmp_path / "second"))
    first._register_tool_capability("session_only", frozenset({ToolCapability.PURE}))

    assert "session_only" in first._tool_capabilities
    assert "session_only" not in second._tool_capabilities
