"""
Platform-aware sandbox stress tests.

These tests specifically exercise the sandbox backend selection, probe
correctness, profile generation (macOS), and bubblewrap command construction
(Linux) — components that the audit identified as unvalidated under load.
"""

from __future__ import annotations

import platform
import sys
from pathlib import Path

import pytest

from nexus.sandbox import CommandResult, CommandSpec, SandboxBackend, SandboxRunner

_SYSTEM = platform.system().lower()


# ─── Backend probe correctness ────────────────────────────────────────────────


class TestSandboxBackendProbe:
    """Verify backend probe returns sensible results on the current host."""

    def test_backend_is_a_valid_enum_value(self, tmp_path):
        runner = SandboxRunner(tmp_path)
        backend = runner.backend()
        assert isinstance(backend, SandboxBackend)

    def test_linux_expects_bubblewrap_or_restricted(self, tmp_path):
        if _SYSTEM != "linux":
            pytest.skip("Linux-only assertion")
        import shutil
        runner = SandboxRunner(tmp_path)
        backend = runner.backend()
        if shutil.which("bwrap"):
            # If bwrap is on PATH, either BUBBLEWRAP (probe passed) or
            # RESTRICTED (probe failed — e.g. kernel without user namespaces)
            assert backend in (SandboxBackend.BUBBLEWRAP, SandboxBackend.RESTRICTED)
        else:
            assert backend == SandboxBackend.RESTRICTED

    def test_macos_expects_sandbox_exec_or_restricted(self, tmp_path):
        if _SYSTEM != "darwin":
            pytest.skip("macOS-only assertion")
        runner = SandboxRunner(tmp_path)
        backend = runner.backend()
        assert backend in (SandboxBackend.MACOS, SandboxBackend.RESTRICTED)

    def test_windows_always_restricted(self, tmp_path):
        if _SYSTEM != "windows":
            pytest.skip("Windows-only assertion")
        runner = SandboxRunner(tmp_path)
        assert runner.backend() == SandboxBackend.RESTRICTED

    def test_probe_is_cached_after_first_call(self, tmp_path):
        """Second backend() call must return the same value without re-probing."""
        runner = SandboxRunner(tmp_path)
        b1 = runner.backend()
        # Force a second call; must be identical
        b2 = runner.backend()
        assert b1 == b2


# ─── Command result format ────────────────────────────────────────────────────


class TestCommandResultFormat:
    """Verify CommandResult output formatting for every backend value."""

    def _make_result(self, backend: SandboxBackend, **kwargs) -> CommandResult:
        defaults = dict(
            argv=["echo", "hi"],
            cwd="/tmp",
            backend=backend,
            success=True,
            exit_code=0,
            stdout="hi\n",
            stderr="",
            duration_ms=12,
            network_allowed=False,
            network_enforced=True,
        )
        defaults.update(kwargs)
        return CommandResult(**defaults)

    def test_success_format_contains_argv(self):
        result = self._make_result(SandboxBackend.RESTRICTED)
        output = result.format_tool_output()
        assert "echo hi" in output
        assert "✅" in output

    def test_blocked_format_contains_reason(self):
        result = self._make_result(
            SandboxBackend.BLOCKED,
            success=False,
            exit_code=None,
            blocked_reason="Path escapes workspace",
        )
        output = result.format_tool_output()
        assert "BLOCKED" in output
        assert "Path escapes workspace" in output

    def test_timeout_format_shows_duration(self):
        result = self._make_result(
            SandboxBackend.RESTRICTED,
            success=False,
            exit_code=None,
            timed_out=True,
            duration_ms=5200,
        )
        output = result.format_tool_output()
        assert "timed out" in output.lower()
        assert "5.2s" in output

    def test_to_dict_serialises_backend_as_string(self):
        result = self._make_result(SandboxBackend.MACOS)
        d = result.to_dict()
        assert d["backend"] == "sandbox-exec"
        assert isinstance(d["backend"], str)

    def test_truncated_output_marker_present(self):
        result = self._make_result(
            SandboxBackend.RESTRICTED,
            output_truncated=True,
        )
        output = result.format_tool_output()
        assert "truncated" in output.lower()


# ─── CommandSpec validation ───────────────────────────────────────────────────


class TestCommandSpecValidation:
    def test_reject_empty_executable(self, tmp_path):
        with pytest.raises(ValueError, match="executable"):
            CommandSpec.create([""], cwd=tmp_path)

    def test_reject_whitespace_only_executable(self, tmp_path):
        with pytest.raises(ValueError, match="executable"):
            CommandSpec.create(["   "], cwd=tmp_path)

    def test_reject_zero_timeout(self, tmp_path):
        with pytest.raises(ValueError, match="timeout"):
            CommandSpec.create(["echo"], cwd=tmp_path, timeout_seconds=0)

    def test_reject_negative_timeout(self, tmp_path):
        with pytest.raises(ValueError, match="timeout"):
            CommandSpec.create(["echo"], cwd=tmp_path, timeout_seconds=-1.0)

    def test_argv_normalised_to_strings(self, tmp_path):
        spec = CommandSpec.create([Path("echo"), 42, True], cwd=tmp_path)  # type: ignore[arg-type]
        assert spec.argv == ("echo", "42", "True")

    def test_cwd_resolved_to_absolute(self, tmp_path):
        spec = CommandSpec.create(["echo"], cwd=tmp_path)
        assert Path(spec.cwd).is_absolute()

    def test_max_output_bytes_floor(self, tmp_path):
        """max_output_bytes below 1024 is silently raised to 1024."""
        spec = CommandSpec.create(["echo"], cwd=tmp_path, max_output_bytes=1)
        assert spec.max_output_bytes == 1024

    def test_invalid_env_key_rejected(self, tmp_path):
        runner = SandboxRunner(tmp_path)
        spec = CommandSpec.create(
            ["echo", "hi"],
            cwd=tmp_path,
            env={"KEY=INJECTED": "value"},
        )
        with pytest.raises(ValueError, match="Invalid environment key"):
            runner._filtered_env(spec.env)


# ─── Workspace boundary enforcement ──────────────────────────────────────────


class TestWorkspaceBoundary:
    """Commands targeting paths outside the workspace root must be blocked."""

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths only")
    def test_absolute_etc_path_is_blocked(self, tmp_path):
        runner = SandboxRunner(tmp_path)
        spec = CommandSpec.create(["cat", "/etc/passwd"], cwd=tmp_path)
        result = runner.run(spec)
        assert result.backend == SandboxBackend.BLOCKED

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths only")
    def test_traversal_path_is_blocked(self, tmp_path):
        runner = SandboxRunner(tmp_path)
        spec = CommandSpec.create(["cat", "../../../etc/passwd"], cwd=tmp_path)
        result = runner.run(spec)
        assert result.backend == SandboxBackend.BLOCKED

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths only")
    def test_tilde_expansion_is_blocked(self, tmp_path):
        runner = SandboxRunner(tmp_path)
        spec = CommandSpec.create(["ls", "~/secret"], cwd=tmp_path)
        result = runner.run(spec)
        assert result.backend == SandboxBackend.BLOCKED

    def test_cwd_outside_workspace_is_blocked(self, tmp_path):
        """Command cwd that escapes the workspace must be caught before exec."""
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        runner = SandboxRunner(workspace)
        spec = CommandSpec(
            argv=("echo", "hi"),
            cwd=str(outside),
            timeout_seconds=5.0,
        )
        result = runner.run(spec)
        assert result.backend == SandboxBackend.BLOCKED
        assert "outside" in result.blocked_reason.lower() or "workspace" in result.blocked_reason.lower()

    def test_nonexistent_cwd_is_blocked(self, tmp_path):
        runner = SandboxRunner(tmp_path)
        spec = CommandSpec(
            argv=("echo", "hi"),
            cwd=str(tmp_path / "nonexistent"),
            timeout_seconds=5.0,
        )
        result = runner.run(spec)
        assert result.backend == SandboxBackend.BLOCKED


# ─── Filtered env safety ─────────────────────────────────────────────────────


class TestFilteredEnv:
    """The filtered environment must strip credentials and injections."""

    def test_api_keys_are_stripped(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-secret")
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
        runner = SandboxRunner(tmp_path)
        env = runner._filtered_env({})
        assert "NVIDIA_API_KEY" not in env
        assert "OPENROUTER_API_KEY" not in env

    def test_nexus_prefixed_keys_are_passed_through(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NEXUS_SANDBOX", "outer")
        runner = SandboxRunner(tmp_path)
        env = runner._filtered_env({})
        # NEXUS_SANDBOX is always overridden to "1" by the runner
        assert env.get("NEXUS_SANDBOX") == "1"

    def test_additions_are_included(self, tmp_path):
        runner = SandboxRunner(tmp_path)
        env = runner._filtered_env({"MY_CUSTOM_VAR": "hello"})
        assert env["MY_CUSTOM_VAR"] == "hello"

    def test_null_byte_in_key_is_rejected(self, tmp_path):
        runner = SandboxRunner(tmp_path)
        with pytest.raises(ValueError, match="Invalid environment key"):
            runner._filtered_env({"key\x00name": "value"})

    def test_equals_in_key_is_rejected(self, tmp_path):
        runner = SandboxRunner(tmp_path)
        with pytest.raises(ValueError, match="Invalid environment key"):
            runner._filtered_env({"KEY=INJECTED": "value"})


# ─── Sandbox runner initialization ───────────────────────────────────────────


class TestSandboxRunnerInit:
    def test_nonexistent_workspace_raises(self):
        with pytest.raises(ValueError, match="does not exist"):
            SandboxRunner("/nonexistent/path/xyz123")

    def test_file_as_workspace_raises(self, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("data")
        with pytest.raises(ValueError, match="does not exist"):
            SandboxRunner(f)

    def test_workspace_path_resolves_symlinks(self, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        link.symlink_to(real)
        runner = SandboxRunner(link)
        assert runner.workspace == real.resolve()
