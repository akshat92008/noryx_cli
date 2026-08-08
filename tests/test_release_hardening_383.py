"""Regression coverage for Nexus CLI 3.8.3 autonomous hardening."""
from __future__ import annotations
import os, sys, time
from pathlib import Path
from types import SimpleNamespace
import pytest
from nexus.language_intelligence import LSPClient
from nexus.process_gateway import ProcessExecutionGateway, ProcessRequest
from nexus.sandbox import CommandSpec, SandboxBackend, SandboxRunner
from nexus.security.command_policy import CommandPolicy, CommandRisk
from nexus.tools import tool_context, tool_process_run, tool_process_status, tool_process_stop


def test_macos_profile_does_not_grant_host_temp_tree(tmp_path: Path):
    runner = SandboxRunner(tmp_path)
    command, profile = runner._macos_command(CommandSpec.create([sys.executable, "-V"], tmp_path), tmp_path)
    try: content = profile.read_text(encoding="utf-8")
    finally: profile.unlink(missing_ok=True)
    host_tmp = str(Path(__import__("tempfile").gettempdir()).resolve())
    assert command[0] == "sandbox-exec"
    assert f'(subpath "{host_tmp}")' not in content
    assert f'(allow file-write* (subpath "{tmp_path.resolve()}"))' in content
    assert '(subpath "/opt")' not in content


def test_macos_prepare_redirects_temp_environment_into_workspace(tmp_path: Path, monkeypatch):
    runner = SandboxRunner(tmp_path); monkeypatch.setattr(runner, "backend", lambda: SandboxBackend.MACOS)
    prepared = runner.prepare(CommandSpec.create([sys.executable, "-V"], tmp_path, require_os_isolation=True))
    try:
        expected = tmp_path.resolve() / ".nexus" / "sandbox-tmp"
        assert Path(prepared.env["TMPDIR"]) == expected
        assert Path(prepared.env["TMP"]) == expected
        assert Path(prepared.env["TEMP"]) == expected
        assert expected.is_dir()
    finally:
        if prepared.cleanup_path: Path(prepared.cleanup_path).unlink(missing_ok=True)


def test_optional_process_isolation_really_allows_fallback(tmp_path: Path):
    spec = ProcessExecutionGateway._build_sandbox_spec(ProcessRequest.create("test", [sys.executable, "-V"], tmp_path, isolation_policy="optional"))
    assert spec.require_os_isolation is False and spec.allow_unisolated_host_process is True


def test_required_process_isolation_still_fails_closed(tmp_path: Path):
    spec = ProcessExecutionGateway._build_sandbox_spec(ProcessRequest.create("test", [sys.executable, "-V"], tmp_path, isolation_policy="required"))
    assert spec.require_os_isolation is True and spec.allow_unisolated_host_process is False


@pytest.mark.parametrize(("argv", "expected"), [
    (["git", "status"], CommandRisk.READ_ONLY), (["git", "log", "-1"], CommandRisk.READ_ONLY),
    (["npm", "test"], CommandRisk.VALIDATION), (["go", "test", "./..."], CommandRisk.VALIDATION),
    (["npm", "run", "build"], CommandRisk.BUILD), (["pip", "install", "rich"], CommandRisk.PACKAGE_INSTALL),
    ([sys.executable, "-m", "pip", "install", "rich"], CommandRisk.PACKAGE_INSTALL),
    (["git", "commit", "-m", "x"], CommandRisk.GIT_MUTATION), (["git", "push"], CommandRisk.NETWORK_REQUEST),
])
def test_command_policy_understands_multi_token_commands(tmp_path: Path, argv, expected):
    assert CommandPolicy(tmp_path).classify(argv) == expected


def test_lsp_network_is_denied_by_default_and_explicitly_opt_in(tmp_path: Path, monkeypatch):
    captured=[]
    class StopStart(RuntimeError): pass
    def fake_popen(request, **_kwargs): captured.append(request.network_policy); raise StopStart
    monkeypatch.setattr(ProcessExecutionGateway, "popen", fake_popen)
    with pytest.raises(StopStart): LSPClient(tmp_path, "python", command=(sys.executable, "-V")).start()
    with pytest.raises(StopStart): LSPClient(tmp_path, "python", command=(sys.executable, "-V"), network=True).start()
    assert captured == ["deny", "allow"]


def test_autonomous_admission_fails_closed_on_failed_behavioral_qualification(tmp_path: Path, monkeypatch):
    from nexus.cli import cli_impl
    q=SimpleNamespace(autonomous_ready=False, backend="unisolated-host-process", probes=[SimpleNamespace(name="outside_write_denied", passed=False)])
    monkeypatch.setattr("nexus.platform.sandbox_qualification.qualify_native_sandbox", lambda _root: q)
    with pytest.raises(RuntimeError, match="Autonomous mode blocked"): cli_impl._require_autonomous_host_qualification(tmp_path)


def test_autonomous_admission_accepts_passing_behavioral_qualification(tmp_path: Path, monkeypatch):
    from nexus.cli import cli_impl
    q=SimpleNamespace(autonomous_ready=True, backend="sandbox-exec", probes=[])
    monkeypatch.setattr("nexus.platform.sandbox_qualification.qualify_native_sandbox", lambda _root: q)
    assert cli_impl._require_autonomous_host_qualification(tmp_path) is q


@pytest.mark.skipif(os.name != "posix", reason="process-group hard ceiling regression is POSIX-specific")
def test_background_output_is_bounded_during_execution(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path / "state")); ceiling=4096
    command=f'{sys.executable} -c "import sys,time; sys.stdout.write(\'x\'*250000); sys.stdout.flush(); time.sleep(30)"'
    with tool_context(str(tmp_path), owner="output-ceiling-test"):
        result=tool_process_run(command, require_os_isolation=False, allow_unisolated_host_process=True, timeout=30, max_output_bytes=ceiling)
    assert "Background process started" in result
    pid=int(next(line for line in result.splitlines() if "PID:" in line).split(":",1)[1])
    deadline=time.monotonic()+8
    while time.monotonic()<deadline:
        status=tool_process_status(pid)
        if "terminated after exceeding output ceiling" in status or "exited (" in status: break
        time.sleep(.05)
    from nexus.tools.background import _bg_processes
    record=_bg_processes[pid]; assert Path(record["stdout_log"]).stat().st_size <= ceiling
    assert record["output_limit_exceeded"] is True
    assert "output truncated by Nexus policy" in tool_process_status(pid)
    tool_process_stop(pid)


def test_release_binding_accepts_autonomy_only_with_sandbox_and_competitive_evidence(tmp_path: Path):
    import hashlib
    import json
    from datetime import datetime, timezone
    from nexus.release.qualification import ChannelPolicy, ReleaseQualification

    deploy = tmp_path / "deploy.json"
    deploy.write_text(
        json.dumps(
            {
                "supervised_production_ready": True,
                "autonomous_production_ready": True,
                "sandbox_qualification": {"autonomous_ready": True},
                "competitive_superiority": {"qualified": True},
            }
        ),
        encoding="utf-8",
    )
    digest = hashlib.sha256(deploy.read_bytes()).hexdigest()
    binding = {
        "schema_version": "nexus.release-evidence.v1",
        "version": "3.8.3",
        "source_tree_sha256": "source",
        "source_archive_sha256": "",
        "artifacts": {},
        "test_command": "pytest -q",
        "runner": {"os": "darwin", "python": "3.13"},
        "test_summary": {"collected": 1, "passed": 1, "failed": 0, "skipped": 0},
        "reports": {"deploy_check": {"path": deploy.name, "sha256": digest}},
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    qualification = ReleaseQualification(
        version="3.8.3",
        channel_policy=ChannelPolicy(
            require_bound_evidence=True,
            require_rollback_plan=False,
            require_secret_scan=False,
            required_report_names=("deploy_check",),
        ),
        evidence_binding=binding,
        expected_source_sha256="source",
        evidence_root=str(tmp_path),
    )
    assert qualification._validate_bound_evidence() == []
