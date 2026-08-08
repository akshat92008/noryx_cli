"""Universal gateway for all external process execution.

Enforces network policies, environment filtering, isolation, output limits, and cleanup.
"""

from __future__ import annotations

import os
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from nexus.runtime.process_state import ProcessStateRegistry
from nexus.sandbox import CommandResult, CommandSpec, PreparedCommand, SandboxRunner


@dataclass(frozen=True)
class ProcessRequest:
    """A high-level request to execute an external process."""

    purpose: str
    command: tuple[str, ...]
    workspace: str | Path
    trust_level: str = "repository_controlled"
    network_policy: str = "deny"
    isolation_policy: str = "required"
    environment_policy: str = "filtered"
    timeout_seconds: float = 120.0
    output_limit_bytes: int = 1_000_000
    allowed_sensitive_env_keys: tuple[str, ...] = ()
    env_additions: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        purpose: str,
        command: list[str] | tuple[str, ...],
        workspace: str | Path,
        *,
        trust_level: str = "repository_controlled",
        network_policy: str = "deny",
        isolation_policy: str = "required",
        environment_policy: str = "filtered",
        timeout_seconds: float = 120.0,
        output_limit_bytes: int = 1_000_000,
        allowed_sensitive_env_keys: tuple[str, ...] = (),
        env_additions: Mapping[str, str] | None = None,
    ) -> "ProcessRequest":
        return cls(
            purpose=purpose,
            command=tuple(command),
            workspace=workspace,
            trust_level=trust_level,
            network_policy=network_policy,
            isolation_policy=isolation_policy,
            environment_policy=environment_policy,
            timeout_seconds=timeout_seconds,
            output_limit_bytes=output_limit_bytes,
            allowed_sensitive_env_keys=tuple(allowed_sensitive_env_keys),
            env_additions=dict(env_additions or {}),
        )


class ManagedProcess:
    """A background process wrapped for safe termination and group cleanup."""

    def __init__(self, process: subprocess.Popen, prepared: PreparedCommand):
        self._process = process
        self.prepared = prepared
        self.pid = process.pid
        self._lifecycle_lock = threading.RLock()
        self._finalized = False
        ProcessStateRegistry.register_process(process)

    @property
    def stdout(self):
        return self._process.stdout

    @property
    def stderr(self):
        return self._process.stderr

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    def poll(self) -> int | None:
        return self._process.poll()

    def wait(self, timeout: float | None = None) -> int:
        code = self._process.wait(timeout=timeout)
        self._finalize_if_reaped()
        return code

    def _cleanup_profile(self) -> None:
        cleanup = str(self.prepared.cleanup_path or "").strip()
        if cleanup:
            try:
                Path(cleanup).unlink(missing_ok=True)
            except OSError:
                pass

    def _finalize_if_reaped(self) -> bool:
        """Cleanup and unregister only after the parent has actually exited."""
        with self._lifecycle_lock:
            if self._finalized:
                return True
            if self._process.poll() is None:
                return False
            self._cleanup_profile()
            ProcessStateRegistry.unregister_process(self._process)
            self._finalized = True
            return True

    def _signal_group(self, *, force: bool) -> None:
        if os.name == "posix":
            import signal

            os.killpg(
                self._process.pid,
                signal.SIGKILL if force else signal.SIGTERM,
            )
            return
        command = ["taskkill", "/T", "/PID", str(self._process.pid)]
        if force:
            command.insert(1, "/F")
        subprocess.run(command, capture_output=True, timeout=5, check=False)

    def terminate(self, *, grace_seconds: float = 1.5) -> None:
        """SIGTERM, wait, SIGKILL if needed, reap, cleanup, then unregister."""
        with self._lifecycle_lock:
            if self._finalize_if_reaped():
                return
            try:
                self._signal_group(force=False)
            except (OSError, subprocess.SubprocessError):
                try:
                    self._process.terminate()
                except OSError:
                    pass
            try:
                self._process.wait(timeout=max(0.05, float(grace_seconds)))
            except subprocess.TimeoutExpired:
                try:
                    self._signal_group(force=True)
                except (OSError, subprocess.SubprocessError):
                    try:
                        self._process.kill()
                    except OSError:
                        pass
                try:
                    self._process.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    # Keep the still-live process registered for a later cleanup pass.
                    return
            self._finalize_if_reaped()

    def kill(self) -> None:
        """Force kill the process and its process group."""
        if self._finalize_if_reaped():
            return
        try:
            self._signal_group(force=True)
        except (OSError, subprocess.SubprocessError):
            try:
                self._process.kill()
            except OSError:
                pass
        try:
            self._process.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            return
        self._finalize_if_reaped()


class ProcessExecutionGateway:
    """The central authority for executing processes in Noryx."""

    @classmethod
    def _build_sandbox_spec(cls, request: ProcessRequest) -> CommandSpec:
        policy = request.isolation_policy.strip().lower().replace("-", "_")
        if policy not in {"required", "optional", "trusted_host"}:
            raise ValueError(f"Unknown isolation policy: {request.isolation_policy!r}")
        require_os_isolation = policy == "required"
        allow_unisolated_host_process = policy in {"optional", "trusted_host"}
        network = request.network_policy == "allow"

        env = dict(request.env_additions)
        if request.environment_policy == "forward_all":
            env = {**os.environ, **env}

        return CommandSpec.create(
            argv=request.command,
            cwd=request.workspace,
            timeout_seconds=request.timeout_seconds,
            network=network,
            env=env,
            max_output_bytes=request.output_limit_bytes,
            require_os_isolation=require_os_isolation,
            allow_unisolated_host_process=allow_unisolated_host_process,
            allowed_sensitive_env_keys=request.allowed_sensitive_env_keys,
        )

    @classmethod
    def run(cls, request: ProcessRequest) -> CommandResult:
        """Run a process synchronously and return the result."""
        spec = cls._build_sandbox_spec(request)
        runner = SandboxRunner(request.workspace)
        return runner.run(spec)

    @classmethod
    def popen(cls, request: ProcessRequest, **kwargs) -> ManagedProcess:
        """Start a long-running process (e.g. LSP) in the sandbox."""
        spec = cls._build_sandbox_spec(request)
        runner = SandboxRunner(request.workspace)
        prepared = runner.prepare(spec)

        popen_kwargs = {
            "cwd": prepared.cwd,
            "env": prepared.env,
        }
        popen_kwargs.update(kwargs)

        if os.name == "posix":
            popen_kwargs.setdefault("start_new_session", True)
        elif os.name == "nt":
            popen_kwargs.setdefault(
                "creationflags", getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 512)
            )

        process = subprocess.Popen(list(prepared.argv), **popen_kwargs)
        SandboxRunner.apply_resource_limits(process.pid, spec)
        return ManagedProcess(process, prepared)
