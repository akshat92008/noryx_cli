"""Typed, policy-driven command execution for Nexus.

The legacy shell tool remains available for compatibility, but autonomous
workflows use :class:`SandboxRunner` with an argv vector.  The runner selects
the strongest native isolation backend available on the host:

* Bubblewrap on Linux
* ``sandbox-exec`` on macOS
* a restricted subprocess fallback on other hosts

The fallback is deliberately visible in every result.  Callers may set
``require_os_isolation`` to fail closed instead of silently running without a
kernel-enforced filesystem/network boundary.
"""

from __future__ import annotations

import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Mapping, Sequence

try:
    import resource
except ImportError:  # pragma: no cover - unavailable on Windows
    resource = None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


class SandboxBackend(str, Enum):
    """Execution backend selected for a command."""

    BUBBLEWRAP = "bubblewrap"
    MACOS = "sandbox-exec"
    UNISOLATED_HOST = "unisolated-host-process"
    RESTRICTED = "unisolated-host-process"  # compatibility alias; not a security boundary
    BLOCKED = "blocked"


@dataclass(frozen=True)
class CommandSpec:
    """A shell-free command request."""

    argv: tuple[str, ...]
    cwd: str
    timeout_seconds: float = 120.0
    network: bool = False
    env: Mapping[str, str] = field(default_factory=dict)
    max_output_bytes: int = 1_000_000
    require_os_isolation: bool = True
    allow_unisolated_host_process: bool = False
    allowed_sensitive_env_keys: tuple[str, ...] = ()

    @classmethod
    def create(
        cls,
        argv: Sequence[str],
        cwd: str | Path,
        *,
        timeout_seconds: float = 120.0,
        network: bool = False,
        env: Mapping[str, str] | None = None,
        max_output_bytes: int = 1_000_000,
        require_os_isolation: bool = True,
        allow_unisolated_host_process: bool = False,
        allowed_sensitive_env_keys: Sequence[str] = (),
    ) -> "CommandSpec":
        normalized = tuple(str(item) for item in argv)
        if not normalized or not normalized[0].strip():
            raise ValueError("argv must contain an executable")
        timeout = float(timeout_seconds)
        if timeout <= 0:
            raise ValueError("timeout_seconds must be positive")
        return cls(
            argv=normalized,
            cwd=str(Path(cwd).expanduser().resolve()),
            timeout_seconds=timeout,
            network=bool(network),
            env=dict(env or {}),
            max_output_bytes=max(1_024, int(max_output_bytes)),
            require_os_isolation=bool(require_os_isolation),
            allow_unisolated_host_process=bool(allow_unisolated_host_process),
            allowed_sensitive_env_keys=tuple(str(key) for key in allowed_sensitive_env_keys),
        )


@dataclass
class CommandResult:
    """Machine-readable result from a command sandbox."""

    argv: list[str]
    cwd: str
    backend: SandboxBackend
    success: bool
    exit_code: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    blocked_reason: str = ""
    duration_ms: int = 0
    network_allowed: bool = False
    network_enforced: bool = False
    output_truncated: bool = False
    stream_cleanup_failed: bool = False

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["backend"] = self.backend.value
        return payload

    def format_tool_output(self) -> str:
        command = shlex.join(self.argv)
        if self.backend == SandboxBackend.BLOCKED:
            return f"❌ BLOCKED: {self.blocked_reason}\n$ {command}"
        if self.timed_out:
            return (
                f"⏰ Command timed out after {self.duration_ms / 1000:.1f}s "
                f"[sandbox={self.backend.value}]\n$ {command}"
            )
        marker = "✅" if self.success else f"❌ (exit code {self.exit_code})"
        if self.network_enforced:
            network_status = "on" if self.network_allowed else "off"
        else:
            network_status = "policy-only"
        chunks = [
            f"{marker} $ {command}",
            (
                f"[sandbox={self.backend.value} network="
                f"{network_status} duration_ms={self.duration_ms}]"
            ),
        ]
        if self.stdout:
            chunks.append(self.stdout)
        if self.stderr:
            chunks.append(f"[stderr]\n{self.stderr}")
        if not self.stdout and not self.stderr:
            chunks.append("(no output)")
        if self.output_truncated:
            chunks.append("[output truncated by Nexus policy]")
        return "\n".join(chunks)


@dataclass(frozen=True)
class PreparedCommand:
    """Validated command ready for synchronous or background execution."""

    argv: tuple[str, ...]
    cwd: str
    env: Mapping[str, str]
    backend: SandboxBackend
    cleanup_path: str = ""
    network_allowed: bool = False
    network_enforced: bool = False


class SandboxRunner:
    """Execute typed commands inside the strongest available host sandbox."""

    _backend_cache: SandboxBackend | None = None

    SAFE_ENV_KEYS = {
        "PATH",
        "LANG",
        "LC_ALL",
        "TZ",
        "TMPDIR",
        "TEMP",
        "TMP",
        "SYSTEMROOT",
        "WINDIR",
        "COMSPEC",
        "PATHEXT",
        "VIRTUAL_ENV",
        "CONDA_PREFIX",
        "JAVA_HOME",
        "GOROOT",
        "GOPATH",
        "CARGO_HOME",
        "RUSTUP_HOME",
    }
    SENSITIVE_ENV_MARKERS = (
        "KEY",
        "TOKEN",
        "SECRET",
        "PASSWORD",
        "PASSWD",
        "CREDENTIAL",
        "AUTH",
        "COOKIE",
        "SESSION",
        "PRIVATE",
    )

    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace).expanduser().resolve()
        if not self.workspace.is_dir():
            raise ValueError(f"Sandbox workspace does not exist: {self.workspace}")

    def backend(self) -> SandboxBackend:
        if self._backend_cache is not None:
            return self._backend_cache
        system = platform.system().lower()
        selected = SandboxBackend.RESTRICTED
        if system == "linux" and shutil.which("bwrap") and self._probe_bubblewrap():
            selected = SandboxBackend.BUBBLEWRAP
        elif system == "darwin" and shutil.which("sandbox-exec") and self._probe_macos_sandbox():
            selected = SandboxBackend.MACOS
        type(self)._backend_cache = selected
        return selected

    def run(self, spec: CommandSpec) -> CommandResult:
        try:
            prepared = self.prepare(spec)
        except PermissionError as exc:
            return self._blocked(spec, str(exc))
        cwd = Path(prepared.cwd)
        env = dict(prepared.env)
        command = list(prepared.argv)
        cleanup_path = Path(prepared.cleanup_path) if prepared.cleanup_path else None
        backend = prepared.backend

        started = time.monotonic()
        try:
            kwargs = {}
            if os.name == "posix":
                # ``preexec_fn`` is unsafe when Python has multiple threads and
                # can deadlock or corrupt child startup.  Apply limits with
                # prlimit after spawn instead.
                kwargs["start_new_session"] = True
            elif os.name == "nt":
                kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 512)
            
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                **kwargs
            )
            self.apply_resource_limits(process.pid, spec)
            import threading
            stdout_chunks = []
            stderr_chunks = []
            stdout_truncated = [False]
            stderr_truncated = [False]

            def read_stream(stream, chunks, truncated_flag, limit):
                bytes_read = 0
                while True:
                    chunk = stream.read(8192)
                    if not chunk:
                        break
                    bytes_read += len(chunk)
                    if bytes_read > limit:
                        truncated_flag[0] = True
                        allowed = limit - (bytes_read - len(chunk))
                        if allowed > 0:
                            chunks.append(chunk[:allowed])
                        try:
                            if os.name == "posix":
                                os.killpg(process.pid, signal.SIGTERM)
                            else:
                                process.terminate()
                        except OSError:
                            pass
                        # drain
                        while stream.read(65536):
                            pass
                        break
                    chunks.append(chunk)

            stdout_thread = threading.Thread(target=read_stream, args=(process.stdout, stdout_chunks, stdout_truncated, spec.max_output_bytes), daemon=True)
            stderr_thread = threading.Thread(target=read_stream, args=(process.stderr, stderr_chunks, stderr_truncated, spec.max_output_bytes), daemon=True)
            
            stdout_thread.start()
            stderr_thread.start()

            try:
                process.wait(timeout=spec.timeout_seconds)
                completed_returncode = process.returncode
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except OSError:
                        pass
                    try:
                        process.wait(timeout=0.5)
                    except subprocess.TimeoutExpired:
                        try:
                            os.killpg(process.pid, signal.SIGKILL)
                        except OSError:
                            pass
                else:
                    # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True)
                process.wait()
                completed_returncode = None
            
            # A descendant can inherit stdout/stderr and keep the pipe open even
            # after the direct child exits.  Never let a reader-thread join hang
            # the entire Nexus process.  Give streams a short grace period, then
            # terminate the process group and close the pipe handles.
            reader_grace = min(1.0, max(0.1, spec.timeout_seconds * 0.05))
            stdout_thread.join(timeout=reader_grace)
            stderr_thread.join(timeout=reader_grace)
            stream_cleanup_failed = stdout_thread.is_alive() or stderr_thread.is_alive()
            if stream_cleanup_failed:
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGTERM)
                    except OSError:
                        pass
                else:
                    try:
                        process.terminate()
                    except OSError:
                        pass
                stdout_thread.join(timeout=0.5)
                stderr_thread.join(timeout=0.5)
            for stream in (process.stdout, process.stderr):
                try:
                    stream.close()
                except (OSError, ValueError):
                    pass
            if stdout_thread.is_alive() or stderr_thread.is_alive():
                stream_cleanup_failed = True
                if os.name == "posix":
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except OSError:
                        pass
                stdout_thread.join(timeout=0.5)
                stderr_thread.join(timeout=0.5)
            
            duration = int((time.monotonic() - started) * 1000)
            
            stdout = b"".join(stdout_chunks).decode("utf-8", errors="replace").rstrip()
            stderr = b"".join(stderr_chunks).decode("utf-8", errors="replace").rstrip()
            stdout_cut = stdout_truncated[0]
            stderr_cut = stderr_truncated[0]
            
            if timed_out:
                return CommandResult(
                    argv=list(spec.argv),
                    cwd=str(cwd),
                    backend=backend,
                    success=False,
                    exit_code=None,
                    stdout=stdout,
                    stderr=stderr,
                    timed_out=True,
                    duration_ms=duration,
                    network_allowed=spec.network,
                    network_enforced=backend != SandboxBackend.RESTRICTED,
                    output_truncated=stdout_cut or stderr_cut,
                    stream_cleanup_failed=stream_cleanup_failed,
                )
            
            return CommandResult(
                argv=list(spec.argv),
                cwd=str(cwd),
                backend=backend,
                success=completed_returncode == 0 and not stream_cleanup_failed,
                exit_code=completed_returncode,
                stdout=stdout,
                stderr=stderr,
                duration_ms=duration,
                network_allowed=spec.network,
                network_enforced=backend != SandboxBackend.RESTRICTED,
                output_truncated=stdout_cut or stderr_cut,
                stream_cleanup_failed=stream_cleanup_failed,
            )
        except OSError as exc:
            return CommandResult(
                argv=list(spec.argv),
                cwd=str(cwd),
                backend=backend,
                success=False,
                exit_code=None,
                stderr=str(exc),
                duration_ms=int((time.monotonic() - started) * 1000),
                network_allowed=spec.network,
                network_enforced=backend != SandboxBackend.RESTRICTED,
            )
        finally:
            if cleanup_path is not None:
                cleanup_path.unlink(missing_ok=True)

    def prepare(self, spec: CommandSpec) -> PreparedCommand:
        """Validate and wrap a command without starting it.

        Background-process support uses this method so it cannot bypass the
        same workspace, path, environment, and native-isolation policy applied
        by :meth:`run`.
        """

        cwd = Path(spec.cwd).expanduser().resolve()
        try:
            cwd.relative_to(self.workspace)
        except ValueError as exc:
            raise PermissionError("Command cwd is outside the authorized workspace") from exc
        if not cwd.is_dir():
            raise PermissionError(f"Command cwd does not exist: {cwd}")

        path_violation = self._command_path_violation(spec, cwd)
        if path_violation:
            raise PermissionError(path_violation)

        backend = self.backend()
        if backend == SandboxBackend.RESTRICTED and (
            spec.require_os_isolation or not spec.allow_unisolated_host_process
        ):
            raise PermissionError(
                "No supported OS sandbox is available. Install bubblewrap on Linux or use "
                "sandbox-exec on macOS. Trusted host execution requires the explicit "
                "allow_unisolated_host_process=True capability with require_os_isolation=False."
            )

        env = self._filtered_env(
            spec.env, allowed_sensitive_keys=spec.allowed_sensitive_env_keys
        )
        if backend == SandboxBackend.MACOS:
            # Never expose the host-wide temporary directory to generated code.
            # macOS sandboxed commands receive a workspace-private temporary
            # root so temporary-file use remains functional without granting
            # read/write access to unrelated host process artifacts.
            private_tmp = self.workspace / ".nexus" / "sandbox-tmp"
            private_tmp.mkdir(parents=True, exist_ok=True)
            for key in ("TMPDIR", "TMP", "TEMP"):
                env[key] = str(private_tmp)
        command, cleanup_path = self.build_command(spec, cwd)
        return PreparedCommand(
            argv=tuple(command),
            cwd=str(cwd),
            env=env,
            backend=backend,
            cleanup_path=str(cleanup_path) if cleanup_path else "",
            network_allowed=spec.network,
            network_enforced=backend != SandboxBackend.RESTRICTED,
        )

    def run_shell(
        self,
        command: str,
        *,
        cwd: str | Path | None = None,
        timeout_seconds: float = 120.0,
        network: bool = False,
        require_os_isolation: bool = True,
        allow_unisolated_host_process: bool = False,
    ) -> CommandResult:
        """Compatibility path for a reviewed shell string."""
        shell = "/bin/sh" if os.name != "nt" else "cmd.exe"
        argv = [shell, "-c", command] if os.name != "nt" else [shell, "/d", "/s", "/c", command]
        result = self.run(
            CommandSpec.create(
                argv,
                cwd or self.workspace,
                timeout_seconds=timeout_seconds,
                network=network,
                require_os_isolation=require_os_isolation,
                allow_unisolated_host_process=allow_unisolated_host_process,
            )
        )
        try:
            result.argv = shlex.split(command, posix=True)
        except ValueError:
            result.argv = [command]
        return result

    def _bubblewrap_command(self, spec: CommandSpec, cwd: Path) -> list[str]:
        command = [
            "bwrap",
            "--die-with-parent",
            "--new-session",
            "--unshare-user",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--ro-bind",
            "/usr",
            "/usr",
            "--ro-bind",
            "/bin",
            "/bin",
            "--ro-bind",
            "/lib",
            "/lib",
            "--ro-bind",
            "/lib64",
            "/lib64",
        ]
        
        import sys
        for root in {sys.prefix, sys.base_prefix}:
            if root and Path(root).exists():
                command.extend(["--ro-bind", root, root])

        command.extend(
            [
            "--ro-bind",
            "/etc/alternatives",
            "/etc/alternatives",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
        ])
        try:
            self.workspace.relative_to(Path(tempfile.gettempdir()).resolve())
        except ValueError:
            command.extend(["--tmpfs", "/tmp"])
        command.extend(
            [
                "--bind",
                str(self.workspace),
                str(self.workspace),
                "--chdir",
                str(cwd),
                "--setenv",
                "HOME",
                str(self.workspace),
            ]
        )
        if not spec.network:
            command.append("--unshare-net")
        return [*command, "--", *spec.argv]

    def _macos_command(self, spec: CommandSpec, cwd: Path) -> tuple[list[str], Path]:
        import sys
        workspace = str(self.workspace.resolve()).replace('"', '\\"')
        read_roots = [
            workspace,
            "/System",
            "/usr",
            "/bin",
            "/sbin",
            # Common package-manager roots needed by developer tools without
            # granting arbitrary reads across the entire /opt hierarchy.
            "/opt/homebrew",
            "/opt/local",
            "/Library/Developer",
            "/Library/Frameworks",
            "/private/var/db/dyld",
            "/System/Volumes/Preboot",
            sys.prefix,
            sys.base_prefix,
        ]
        read_rules = " ".join(f'(subpath "{item}")' for item in read_roots)
        rules = [
            "(version 1)",
            "(deny default)",
            "(allow process*)",
            "(allow sysctl-read)",
            "(allow mach-lookup)",
            "(allow ipc-posix-shm)",
            "(allow file-read-metadata)",
            f"(allow file-read* {read_rules} "
            '(literal "/") '
            '(literal "/dev/null") (literal "/dev/zero") '
            '(literal "/dev/random") (literal "/dev/urandom"))',
            f'(allow file-write* (subpath "{workspace}"))',
            '(allow file-write-data (literal "/dev/null") (literal "/dev/zero"))',
        ]
        if spec.network:
            rules.append("(allow network*)")
        fd, raw_path = tempfile.mkstemp(prefix="nexus-sandbox-", suffix=".sb")
        os.close(fd)
        profile = Path(raw_path)
        profile.write_text("\n".join(rules) + "\n", encoding="utf-8")
        return ["sandbox-exec", "-f", str(profile), *spec.argv], profile

    def build_command(self, spec: CommandSpec, cwd: Path) -> tuple[list[str], Path | None]:
        """Build the raw command list wrapped in the OS sandbox."""
        backend = self.backend()
        if backend == SandboxBackend.BUBBLEWRAP:
            return self._bubblewrap_command(spec, cwd), None
        if backend == SandboxBackend.MACOS:
            return self._macos_command(spec, cwd)
        return list(spec.argv), None

    def _command_path_violation(self, spec: CommandSpec, cwd: Path) -> str | None:
        """Reject command arguments that can address sensitive host paths.

        This is defense in depth for the restricted-process backend.  Strong
        modes still require a kernel sandbox; this guard blocks common absolute,
        traversal, home-directory, redirection, and interpreter-literal escapes
        before any backend starts.
        """
        argv = list(spec.argv)
        shell_command = ""
        if len(argv) >= 3 and Path(argv[0]).name in {"sh", "bash", "zsh", "dash", "cmd.exe"}:
            if argv[1] in {"-c", "/c", "/d"}:
                shell_command = argv[-1]

        raw_values = [shell_command] if shell_command else argv[1:]
        forbidden_home_markers = (
            "~/",
            "$HOME",
            "${HOME}",
            "%USERPROFILE%",
            "$USERPROFILE",
            "${USERPROFILE}",
        )
        allowed_device_paths = {Path("/dev/null"), Path("/dev/zero"), Path("/dev/random"), Path("/dev/urandom")}
        import sys
        safe_system_roots = tuple(
            Path(item)
            for item in (
                "/usr",
                "/bin",
                "/sbin",
                "/lib",
                "/lib64",
                "/System",
                "/opt",
                "/Library/Developer",
                "/Library/Frameworks",
                "/private/var/db/dyld",
                "/System/Volumes/Preboot",
                sys.prefix,
                sys.base_prefix,
            )
        )

        def authorized(candidate: str, *, executable: bool = False) -> bool:
            candidate = candidate.strip().strip("'\"()[]{};,|&<>")
            if not candidate:
                return True
            if any(marker in candidate for marker in forbidden_home_markers):
                return False
            if candidate.startswith("~"):
                return False
            path = Path(candidate).expanduser()
            resolved = path.resolve() if path.is_absolute() else (cwd / path).resolve(strict=False)
            try:
                resolved.relative_to(self.workspace)
                return True
            except ValueError:
                pass
            if resolved in allowed_device_paths:
                return True
            if executable:
                return any(_is_relative_to(resolved, root) for root in safe_system_roots) and not _is_relative_to(resolved, Path("/System/Volumes/Data"))
            # Runtime/toolchain reads are safe; user, home, root and /etc reads are not.
            return any(_is_relative_to(resolved, root) for root in safe_system_roots) and not _is_relative_to(resolved, Path("/System/Volumes/Data"))

        def _remove_safe_urls(text: str) -> str:
            from urllib.parse import urlparse
            for match in re.finditer(r"\b([a-zA-Z][a-zA-Z0-9+.-]*://\S+)", text):
                candidate_url = match.group(1).strip("'\"()[]{};,|&<>")
                try:
                    parsed = urlparse(candidate_url)
                    if parsed.scheme in ("http", "https", "ftp") and parsed.netloc:
                        text = text.replace(match.group(1), "")
                except ValueError:
                    continue
            return text

        def inspect_value(value: str, *, executable: bool = False) -> str:
            value_no_urls = _remove_safe_urls(value)
            if any(marker in value_no_urls for marker in forbidden_home_markers):
                return f"Command references a home-directory expansion outside the workspace: {value[:160]}"
            # Catch absolute paths and parent traversal even when embedded in
            # --flag=/path, redirections, or interpreter source strings.
            candidates = re.findall(
                r"(?<![A-Za-z0-9_])(?:/[A-Za-z0-9_./@+%:=~-]+|\.\.?/[A-Za-z0-9_./@+%:=~-]+)",
                value_no_urls,
            )
            if not candidates:
                candidates = [value_no_urls]
            for candidate in candidates:
                if not authorized(candidate, executable=executable and candidate == value_no_urls):
                    return f"Command path escapes the authorized workspace: {candidate}"
            return ""

        if shell_command:
            normalized = re.sub(r"(&&|\|\||;)", r" \1 ", shell_command)
            try:
                tokens = shlex.split(normalized, posix=True)
            except ValueError:
                return "Shell command could not be safely parsed"
            command_position = True
            for token in tokens:
                if token in {"&&", "||", ";", "|"}:
                    command_position = True
                    continue
                violation = inspect_value(token, executable=command_position)
                if violation:
                    return violation
                command_position = False
            # Interpreter code may contain quoted paths that shlex removes into
            # a single token; inspect the raw command as a second line of defense.
            violation = inspect_value(shell_command)
            if violation:
                # Permit only system executable paths used at command positions.
                absolute_candidates = re.findall(r"/[A-Za-z0-9_./@+%:=~-]+", shell_command)
                unsafe = [item for item in absolute_candidates if not authorized(item)]
                if unsafe:
                    return f"Command path escapes the authorized workspace: {unsafe[0]}"
            return ""

        for _index, value in enumerate(raw_values):
            violation = inspect_value(str(value), executable=False)
            if violation:
                return violation
        return ""

    def _filtered_env(
        self,
        additions: Mapping[str, str],
        *,
        allowed_sensitive_keys: Sequence[str] = (),
    ) -> dict[str, str]:
        env = {
            key: value
            for key, value in os.environ.items()
            if key in self.SAFE_ENV_KEYS
            or (key.startswith("NEXUS_") and not self._is_sensitive_env_key(key))
        }
        allowed = {str(key).upper() for key in allowed_sensitive_keys}
        for key, value in additions.items():
            normalized = str(key)
            if not normalized or "=" in normalized or "\x00" in normalized:
                raise ValueError(f"Invalid environment key: {key!r}")
            if self._is_sensitive_env_key(normalized) and normalized.upper() not in allowed:
                raise ValueError(
                    f"Sensitive environment variables cannot be forwarded to tools: {normalized}"
                )
            env[normalized] = str(value)
        env.update(
            {
                "CI": "true",
                "PAGER": "cat",
                "DEBIAN_FRONTEND": "noninteractive",
                "TERM": "dumb",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
                "NEXUS_SANDBOX": "1",
            }
        )
        return env

    @classmethod
    def _is_sensitive_env_key(cls, key: str) -> bool:
        normalized = str(key).upper()
        return any(marker in normalized for marker in cls.SENSITIVE_ENV_MARKERS)

    @staticmethod
    def _probe_bubblewrap() -> bool:
        """Reject installed-but-unusable bubblewrap binaries before a real task."""
        try:
            # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
            result = subprocess.run(
                [
                    "bwrap",
                    "--die-with-parent",
                    "--new-session",
                    "--unshare-user",
                    "--unshare-pid",
                    "--ro-bind",
                    "/",
                    "/",
                    "--proc",
                    "/proc",
                    "--dev",
                    "/dev",
                    "--",
                    "/bin/true",
                ],
                capture_output=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    @staticmethod
    def _probe_macos_sandbox() -> bool:
        try:
            # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
            result = subprocess.run(
                ["sandbox-exec", "-p", "(version 1) (allow default)", "/usr/bin/true"],
                capture_output=True,
                timeout=2,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    @staticmethod
    def resource_limit_values(spec: CommandSpec) -> tuple[tuple[int, tuple[int, int]], ...]:
        """Return the bounded POSIX limits applied to a child process."""
        if resource is None:
            return ()
        cpu_limit = int(spec.timeout_seconds) + 5
        return (
            (resource.RLIMIT_CORE, (0, 0)),
            (resource.RLIMIT_NOFILE, (1024, 1024)),
            (resource.RLIMIT_FSIZE, (512 * 1024 * 1024, 512 * 1024 * 1024)),
            (resource.RLIMIT_CPU, (cpu_limit, cpu_limit)),
            (resource.RLIMIT_AS, (4 * 1024 * 1024 * 1024, 4 * 1024 * 1024 * 1024)),
            (resource.RLIMIT_NPROC, (512, 512)),
        )

    @staticmethod
    def apply_resource_limits(pid: int, spec: CommandSpec) -> None:
        """Apply limits without using thread-unsafe ``preexec_fn``.

        Linux exposes ``resource.prlimit`` for setting a child process's limits
        after spawn.  Hosts without that API retain timeout, output, workspace,
        and native-sandbox controls rather than risking a fork-time deadlock.
        """
        if resource is None or not hasattr(resource, "prlimit"):
            return
        for limit, values in SandboxRunner.resource_limit_values(spec):
            try:
                resource.prlimit(pid, limit, values)
            except (ProcessLookupError, PermissionError, ValueError, OSError):
                # Short-lived commands can exit before limits are applied.
                continue

    @staticmethod
    def _decode_bounded(value: bytes, limit: int) -> tuple[str, bool]:
        truncated = len(value) > limit
        chosen = value[:limit]
        return chosen.decode("utf-8", errors="replace").rstrip(), truncated

    @staticmethod
    def _as_bytes(value: bytes | str | None) -> bytes:
        if value is None:
            return b""
        return value if isinstance(value, bytes) else value.encode("utf-8", errors="replace")

    @staticmethod
    def _blocked(spec: CommandSpec, reason: str) -> CommandResult:
        return CommandResult(
            argv=list(spec.argv),
            cwd=spec.cwd,
            backend=SandboxBackend.BLOCKED,
            success=False,
            exit_code=None,
            blocked_reason=reason,
            network_allowed=spec.network,
        )
