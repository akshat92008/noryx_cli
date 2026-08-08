"""Shared-process lifecycle registry and deterministic reset boundary."""
from __future__ import annotations

import atexit
import os
import signal
import subprocess
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator


@dataclass
class CleanupFailure:
    name: str
    error: str


class ProcessStateRegistry:
    """Own cleanup callbacks and child processes created by Nexus subsystems."""

    _lock = threading.RLock()
    _callbacks: dict[str, Callable[[], Any]] = {}
    _processes: dict[int, subprocess.Popen[Any]] = {}

    @classmethod
    def register_cleanup(cls, name: str, callback: Callable[[], Any]) -> None:
        with cls._lock:
            cls._callbacks[str(name)] = callback

    @classmethod
    def unregister_cleanup(cls, name: str) -> None:
        with cls._lock:
            cls._callbacks.pop(str(name), None)

    @classmethod
    def register_process(cls, process: subprocess.Popen[Any]) -> None:
        with cls._lock:
            cls._processes[int(process.pid)] = process

    @classmethod
    def unregister_process(cls, process: subprocess.Popen[Any] | int) -> None:
        pid = int(process if isinstance(process, int) else process.pid)
        with cls._lock:
            cls._processes.pop(pid, None)

    @classmethod
    def cleanup(cls, *, strict: bool = True) -> list[CleanupFailure]:
        failures: list[CleanupFailure] = []
        with cls._lock:
            processes = list(cls._processes.values())
            callbacks = list(reversed(list(cls._callbacks.items())))
            cls._processes.clear()
            cls._callbacks.clear()
        for process in processes:
            try:
                if process.poll() is None:
                    if os.name == "posix":
                        try:
                            os.killpg(process.pid, signal.SIGTERM)
                        except OSError:
                            process.terminate()
                    else:
                        process.terminate()
                    try:
                        process.wait(timeout=1.0)
                    except subprocess.TimeoutExpired:
                        if os.name == "posix":
                            try:
                                os.killpg(process.pid, signal.SIGKILL)
                            except OSError:
                                process.kill()
                        else:
                            process.kill()
                        process.wait(timeout=1.0)
            except Exception as exc:  # cleanup must aggregate all failures
                failures.append(CleanupFailure(f"process:{getattr(process, 'pid', '?')}", repr(exc)))
        for name, callback in callbacks:
            try:
                callback()
            except Exception as exc:
                failures.append(CleanupFailure(name, repr(exc)))
        cls._builtin_reset(failures)
        if strict and failures:
            detail = "; ".join(f"{item.name}: {item.error}" for item in failures)
            raise RuntimeError(f"Process-state reset failed: {detail}")
        return failures

    @staticmethod
    def _builtin_reset(failures: list[CleanupFailure]) -> None:
        resets: list[tuple[str, Callable[[], Any]]] = []
        try:
            from nexus.sandbox import SandboxRunner
            resets.append(("sandbox-backend-cache", lambda: setattr(SandboxRunner, "_backend_cache", None)))
        except ImportError:
            pass
        try:
            from nexus.events import EventBus, EventType
            resets.append(("event-bus", lambda: setattr(EventBus, "_subscribers", {item: [] for item in EventType})))
        except ImportError:
            pass
        try:
            import nexus.config.core as config_core
            resets.append(("config-singleton", lambda: setattr(config_core, "_config_instance", None)))
        except ImportError:
            pass
        try:
            from nexus.routine import RoutineOrchestrator
            def stop_routines() -> None:
                instance = RoutineOrchestrator._instance
                if instance is not None:
                    instance.running = False
                    thread = getattr(instance, "thread", None)
                    if thread and thread.is_alive():
                        thread.join(timeout=1.5)
                    instance.routines.clear()
                    instance.peers.clear()
                RoutineOrchestrator._instance = None
            resets.append(("routine-orchestrator", stop_routines))
        except ImportError:
            pass
        try:
            import nexus.tools as tools
            def reset_tools() -> None:
                for pool in list(getattr(tools, "_language_service_pools", {}).values()):
                    close = getattr(pool, "close", None) or getattr(pool, "shutdown", None)
                    if callable(close):
                        close()
                getattr(tools, "_language_service_pools", {}).clear()
                for var, value in (("_tool_working_dir", None), ("_tool_history", None), ("_tool_owner", "")):
                    context_var = getattr(tools, var, None)
                    if context_var is not None:
                        context_var.set(value)
            resets.append(("tool-runtime", reset_tools))
        except ImportError:
            pass
        for name, callback in resets:
            try:
                callback()
            except Exception as exc:
                failures.append(CleanupFailure(name, repr(exc)))


def reset_process_state(*, strict: bool = True) -> list[CleanupFailure]:
    return ProcessStateRegistry.cleanup(strict=strict)


@contextmanager
def isolated_process_state(*, strict: bool = True) -> Iterator[None]:
    reset_process_state(strict=strict)
    try:
        yield
    finally:
        reset_process_state(strict=strict)


atexit.register(lambda: reset_process_state(strict=False))
