"""Tests for Sandbox functionality."""



from nexus.sandbox import (
    CommandSpec,
    SandboxBackend,
    SandboxRunner,
    _is_relative_to,
)


def test_is_relative_to(tmp_path):
    root = tmp_path / "root"
    child = root / "child"
    other = tmp_path / "other"
    assert _is_relative_to(child, root) is True
    assert _is_relative_to(other, root) is False


def test_sandbox_backend_detection(tmp_path):
    runner = SandboxRunner(tmp_path)
    backend = runner.backend()
    assert backend in (SandboxBackend.BUBBLEWRAP, SandboxBackend.MACOS, SandboxBackend.RESTRICTED)


def test_sandbox_run_echo(tmp_path):
    runner = SandboxRunner(tmp_path)
    spec = CommandSpec.create(argv=["echo", "sandbox_test"], cwd=str(tmp_path), require_os_isolation=False, allow_unisolated_host_process=True)
    result = runner.run(spec)
    assert result.success
    assert "sandbox_test" in result.stdout


def test_sandbox_run_invalid_command(tmp_path):
    runner = SandboxRunner(tmp_path)
    spec = CommandSpec.create(argv=["invalid_command_that_does_not_exist_123"], cwd=str(tmp_path), require_os_isolation=False, allow_unisolated_host_process=True)
    result = runner.run(spec)
    assert not result.success


def test_sandbox_require_os_isolation_fails_on_restricted(tmp_path, monkeypatch):
    runner = SandboxRunner(tmp_path)
    monkeypatch.setattr(SandboxRunner, "_backend_cache", SandboxBackend.RESTRICTED)
    spec = CommandSpec.create(argv=["echo", "test"], cwd=str(tmp_path), require_os_isolation=True)
    result = runner.run(spec)
    assert not result.success
    assert "No supported OS sandbox is available" in result.blocked_reason

def test_sandbox_run_shell_command(tmp_path):
    runner = SandboxRunner(tmp_path)
    result = runner.run_shell("echo hello", require_os_isolation=False, allow_unisolated_host_process=True)
    assert result.success
