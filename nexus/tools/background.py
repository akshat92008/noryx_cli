"""Bounded, lifecycle-safe supervision for Nexus background processes."""
from __future__ import annotations

import os
import shlex
import signal
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

from nexus.paths import nexus_home
from nexus.sandbox import CommandSpec, SandboxRunner

_bg_processes: dict[int, dict] = {}
_bg_processes_lock = threading.RLock()


def _signal_background_group(record: dict, *, force: bool = False) -> None:
    process = record["process"]
    if os.name == "nt":  # pragma: no cover - Windows CI
        if process.poll() is None:
            process.kill() if force else process.terminate()
        return
    try:
        os.killpg(
            int(record.get("process_group", process.pid)),
            signal.SIGKILL if force else signal.SIGTERM,
        )
    except ProcessLookupError:
        pass


def _pump_background_stream(record: dict, stream_name: str) -> None:
    """Copy one pipe to disk without ever exceeding the configured ceiling."""
    stream = record.get(f"{stream_name}_pipe")
    if stream is None:
        return
    path = Path(record[f"{stream_name}_log"])
    limit = int(record["max_output_bytes"])
    written = 0
    limit_signaled = False
    try:
        with path.open("wb", buffering=0) as handle:
            while True:
                chunk = stream.read(65_536)
                if not chunk:
                    break
                remaining = max(0, limit - written)
                if remaining:
                    selected = chunk[:remaining]
                    handle.write(selected)
                    written += len(selected)
                if len(chunk) > remaining and not limit_signaled:
                    limit_signaled = True
                    record["output_truncated"] = True
                    record["output_limit_exceeded"] = True
                    _signal_background_group(record)
    except (OSError, ValueError) as exc:
        record.setdefault("stream_errors", []).append(f"{stream_name}: {exc}")
        _signal_background_group(record)
    finally:
        try:
            stream.close()
        except (OSError, ValueError):
            pass


def start_background_process(
    command: str,
    work_dir: str,
    *,
    owner: str = "",
    network: bool = False,
    require_os_isolation: bool = True,
    allow_unisolated_host_process: bool = False,
    timeout: float | int | str = 3600,
    max_output_bytes: int = 1_000_000,
) -> str:
    """Start a sandboxed process with strict lifetime and per-stream log caps."""
    try:
        argv = shlex.split(command, posix=True)
        if not argv:
            return "❌ Background command is empty"
        timeout_seconds = float(timeout)
        if timeout_seconds <= 0 or timeout_seconds > 86_400:
            return "❌ Background timeout must be between 0 and 86400 seconds"
        max_output_bytes = max(1_024, min(int(max_output_bytes), 20_000_000))
        log_dir = nexus_home() / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        run_id = uuid.uuid4().hex
        stdout_log = log_dir / f"bg_{run_id}_stdout.log"
        stderr_log = log_dir / f"bg_{run_id}_stderr.log"
        stdout_log.touch(); stderr_log.touch()

        sandbox = SandboxRunner(Path(work_dir))
        spec = CommandSpec.create(
            argv, work_dir, timeout_seconds=timeout_seconds, network=network,
            require_os_isolation=require_os_isolation,
            allow_unisolated_host_process=allow_unisolated_host_process,
            max_output_bytes=max_output_bytes,
        )
        try:
            prepared = sandbox.prepare(spec)
        except PermissionError as exc:
            return f"❌ BLOCKED: {exc}"

        proc = subprocess.Popen(
            list(prepared.argv), shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            cwd=prepared.cwd, env=dict(prepared.env), start_new_session=os.name != "nt",
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
        )
        SandboxRunner.apply_resource_limits(proc.pid, spec)
        record = {
            "command": command, "argv": list(prepared.argv), "pid": proc.pid,
            "stdout_log": str(stdout_log), "stderr_log": str(stderr_log),
            "stdout_pipe": proc.stdout, "stderr_pipe": proc.stderr,
            "started": datetime.now().isoformat(), "started_monotonic": time.monotonic(),
            "timeout_seconds": timeout_seconds, "max_output_bytes": max_output_bytes,
            "process": proc, "process_group": proc.pid, "owner": owner,
            "backend": prepared.backend.value, "network_allowed": prepared.network_allowed,
            "network_enforced": prepared.network_enforced, "cleanup_path": prepared.cleanup_path,
            "timed_out": False, "output_truncated": False, "output_limit_exceeded": False,
            "stream_errors": [],
        }
        with _bg_processes_lock:
            _bg_processes[proc.pid] = record
        pumps = [
            threading.Thread(target=_pump_background_stream, args=(record, name),
                             name=f"nexus-bg-{proc.pid}-{name}", daemon=True)
            for name in ("stdout", "stderr")
        ]
        record["pump_threads"] = pumps
        for thread in pumps: thread.start()
        threading.Thread(target=_watch_background_process, args=(proc.pid,),
                         name=f"nexus-bg-{proc.pid}-watch", daemon=True).start()
        return (
            f"✅ Background process started\n  PID:    {proc.pid}\n  CMD:    {command}\n"
            f"  Sandbox: {prepared.backend.value}\n  Network: {'on' if network else 'off'} "
            f"({'enforced' if prepared.network_enforced else 'policy-only'})\n"
            f"  Timeout: {timeout_seconds:.1f}s\n  Output ceiling: {max_output_bytes} bytes per stream\n"
            f"  Stdout: {stdout_log}\n  Stderr: {stderr_log}"
        )
    except (LookupError, OSError, RuntimeError, TypeError, ValueError) as exc:
        return f"❌ Error starting background process: {exc}"


def _read_bounded_log(path: Path, limit: int) -> tuple[str, bool]:
    try:
        with path.open("rb") as handle: data = handle.read(limit + 1)
    except OSError as exc:
        return f"<log unavailable: {exc}>", False
    return data[:limit].decode("utf-8", errors="replace").rstrip(), len(data) > limit


def background_process_status(pid: int) -> str:
    try: pid = int(pid)
    except (TypeError, ValueError): return "❌ PID must be an integer"
    with _bg_processes_lock: record = _bg_processes.get(pid)
    if not record: return f"❌ PID {pid} is not a Nexus-managed background process"
    exit_code = record["process"].poll()
    stdout, stdout_cut = _read_bounded_log(Path(record["stdout_log"]), record["max_output_bytes"])
    stderr, stderr_cut = _read_bounded_log(Path(record["stderr_log"]), record["max_output_bytes"])
    truncated = bool(record.get("output_truncated") or stdout_cut or stderr_cut)
    if record.get("timed_out"): state, marker = f"timed out after {record['timeout_seconds']:.1f}s", "⏰"
    elif record.get("output_limit_exceeded"): state, marker = "terminated after exceeding output ceiling", "❌"
    elif exit_code is None: state, marker = "running", "✅"
    else: state, marker = f"exited ({exit_code})", "✅" if exit_code == 0 else "❌"
    return (f"{marker} PID {pid} {state}\nCommand: {record['command']}\nSandbox: {record['backend']}\n"
            f"[stdout]\n{stdout}\n[stderr]\n{stderr}" +
            ("\n[output truncated by Nexus policy]" if truncated else ""))


def _cleanup_background_record(record: dict) -> None:
    cleanup = record.get("cleanup_path")
    if cleanup: Path(cleanup).unlink(missing_ok=True)


def _terminate_background_record(record: dict) -> None:
    process = record["process"]
    _signal_background_group(record)
    if process.poll() is None:
        try: process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _signal_background_group(record, force=True); process.wait(timeout=5)
    _signal_background_group(record)


def stop_background_process(pid: int) -> str:
    try: pid = int(pid)
    except (TypeError, ValueError): return "❌ PID must be an integer"
    with _bg_processes_lock: record = _bg_processes.get(pid)
    if not record: return f"❌ PID {pid} is not a Nexus-managed background process"
    try:
        _terminate_background_record(record); _cleanup_background_record(record)
        with _bg_processes_lock: _bg_processes.pop(pid, None)
        return f"✅ Terminated Nexus-managed PID {pid}"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"❌ PID {pid} could not be terminated: {exc}"


def _watch_background_process(pid: int) -> None:
    with _bg_processes_lock: record = _bg_processes.get(pid)
    if not record: return
    process = record["process"]
    try: process.wait(timeout=float(record["timeout_seconds"]))
    except subprocess.TimeoutExpired:
        record["timed_out"] = True
        try: _terminate_background_record(record)
        except (OSError, subprocess.TimeoutExpired): pass
    finally:
        pumps = list(record.get("pump_threads") or ())
        for thread in pumps: thread.join(timeout=0.75)
        if any(thread.is_alive() for thread in pumps):
            _signal_background_group(record)
            for stream_name in ("stdout_pipe", "stderr_pipe"):
                stream = record.get(stream_name)
                if stream is not None:
                    try: stream.close()
                    except (OSError, ValueError): pass
            for thread in pumps: thread.join(timeout=0.5)
        if any(thread.is_alive() for thread in pumps):
            _signal_background_group(record, force=True)
            for thread in pumps: thread.join(timeout=0.5)
        _cleanup_background_record(record)


def stop_owned_processes(owner: str) -> dict[str, object]:
    stopped, errors = [], []
    with _bg_processes_lock: records = list(_bg_processes.items())
    for pid, record in records:
        if record.get("owner") != owner: continue
        try:
            _terminate_background_record(record); stopped.append(pid); _cleanup_background_record(record)
            with _bg_processes_lock: _bg_processes.pop(pid, None)
        except (OSError, subprocess.TimeoutExpired) as exc: errors.append(f"PID {pid}: {exc}")
    return {"stopped": stopped, "errors": errors}


def stop_all_background_processes() -> dict[str, object]:
    stopped, errors = [], []
    with _bg_processes_lock: records = list(_bg_processes.items())
    for pid, record in records:
        try:
            _terminate_background_record(record); stopped.append(pid); _cleanup_background_record(record)
            with _bg_processes_lock: _bg_processes.pop(pid, None)
        except (OSError, subprocess.TimeoutExpired) as exc: errors.append(f"PID {pid}: {exc}")
    return {"stopped": stopped, "errors": errors}
