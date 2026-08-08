"""Crash-tolerant helpers for append-only Nexus state.

The run ledger and evidence trail are shared by interactive, web, and
subagent execution paths.  Keeping the locking and recovery rules here avoids
each caller implementing subtly different JSONL semantics.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


def _process_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    with _PROCESS_LOCKS_GUARD:
        return _PROCESS_LOCKS.setdefault(key, threading.RLock())


@contextmanager
def exclusive_file_lock(path: str | Path) -> Iterator[None]:
    """Hold a process and OS-level lock associated with *path*.

    A separate lock file is used so replacing the protected JSON file with
    ``os.replace`` cannot invalidate the lock.  The implementation supports
    Unix ``flock`` and Windows ``msvcrt.locking`` without an extra dependency.
    """

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.with_name(f".{target.name}.lock")
    local_lock = _process_lock(lock_path)
    with local_lock:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            if os.name == "nt":  # pragma: no cover - exercised in Windows CI
                import msvcrt

                if os.path.getsize(lock_path) == 0:
                    os.write(descriptor, b"0")
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            try:
                if os.name == "nt":  # pragma: no cover - exercised in Windows CI
                    import msvcrt

                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)


def read_jsonl_prefix(path: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """Return every valid JSON object before the first corrupt JSONL record.

    A process killed halfway through the final write must not erase all prior
    evidence.  The returned corruption descriptor lets callers surface the
    damaged suffix without treating valid preceding records as unusable.
    """

    source = Path(path)
    if not source.is_file():
        return [], None
    records: list[dict[str, Any]] = []
    try:
        with source.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, 1):
                if not line.strip():
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as exc:
                    return records, {
                        "path": str(source),
                        "line": line_number,
                        "error": str(exc),
                        "valid_records": len(records),
                    }
                if not isinstance(value, dict):
                    return records, {
                        "path": str(source),
                        "line": line_number,
                        "error": "JSONL record is not an object",
                        "valid_records": len(records),
                    }
                records.append(value)
    except OSError as exc:
        return records, {
            "path": str(source),
            "line": len(records) + 1,
            "error": str(exc),
            "valid_records": len(records),
        }
    return records, None


def append_jsonl_locked(path: str | Path, record: dict[str, Any]) -> None:
    """Append one fsynced JSON object while holding the path lock."""

    destination = Path(path)
    payload = (json.dumps(record, ensure_ascii=False, default=str) + "\n").encode("utf-8")
    with exclusive_file_lock(destination):
        descriptor = os.open(destination, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def recover_jsonl_suffix(
    path: str | Path,
    valid_records: list[dict[str, Any]],
) -> Path:
    """Quarantine a corrupt JSONL file and atomically restore its valid prefix.

    The caller must hold :func:`exclusive_file_lock` for *path*.
    """

    destination = Path(path)
    backup = destination.with_name(f"{destination.name}.corrupt-{time.time_ns()}.bak")
    if destination.exists():
        shutil.copy2(destination, backup)
    temporary = destination.with_name(f".{destination.name}.{os.getpid()}.recover")
    payload = "".join(
        json.dumps(record, ensure_ascii=False, default=str) + "\n" for record in valid_records
    )
    try:
        temporary.write_text(payload, encoding="utf-8")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return backup
