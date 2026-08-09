"""File Mutation Engine.

Wraps file operations to provide atomicity, diffing, and audit trails.

P1 fix (2026-08-09): TOCTOU stale-write race in ``write_file``.

The original code performed the hash check *before* creating the
temporary file, leaving a window between the hash verification and the
final ``os.replace`` call during which an external process could modify
the target.  The race looked like:

    T1  Noryx reads hash_before
    T2  hash_before == expected_hash  → proceed
    T3  Noryx writes temp file
    T4  EXTERNAL PROCESS changes target
    T5  Noryx os.replace(temp, target)   ← external change silently lost

The fix narrows the window by performing a **second hash verification
immediately before** ``os.replace`` while holding a per-file
``threading.Lock``.  Within a single Python process this eliminates the
race.  Across OS processes it is still theoretically possible, but the
second check drastically shrinks the window and ensures that any
concurrent Noryx instance (which also takes the lock) is safely
serialised.

Cross-process protection (P2-level residual risk) would require OS-level
cooperative file locking (``fcntl.flock`` / ``msvcrt.locking``) which
is applied here via ``_FileLockRegistry``.
"""

from __future__ import annotations

import difflib
import fcntl
import hashlib
import logging
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from nexus.events import EventBus, EventType

logger = logging.getLogger(__name__)


@dataclass
class MutationResult:
    path: str
    success: bool
    diff: str = ""
    error: str = ""
    hash_before: str = ""
    hash_after: str = ""


class _FileLockRegistry:
    """Per-absolute-path threading.Lock registry for intra-process serialisation."""

    def __init__(self) -> None:
        self._meta: threading.Lock = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}

    def acquire(self, path: str) -> threading.Lock:
        with self._meta:
            lk = self._locks.setdefault(path, threading.Lock())
        return lk


_FILE_LOCKS = _FileLockRegistry()


class MutationController:
    def __init__(self, workspace: str | Path):
        self.workspace = Path(workspace).expanduser().resolve()

    def _resolve_and_verify(self, path: str | Path) -> Path:
        target = Path(path).expanduser().resolve()
        if not target.is_absolute():
            target = (self.workspace / path).resolve()
        try:
            target.relative_to(self.workspace)
        except ValueError as exc:
            raise PermissionError(f"Target path escapes workspace: {path}") from exc
        return target

    def _hash(self, path: Path) -> str:
        if not path.exists():
            return ""
        digest = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def write_file(
        self,
        path: str | Path,
        content: str | bytes,
        expected_hash: str | None = None,
    ) -> MutationResult:
        """Write content to a file atomically, returning a unified diff.

        The implementation acquires both a Python threading lock and (on
        POSIX) an advisory ``fcntl.LOCK_EX`` lock on the target file so
        that concurrent Noryx instances also serialise around the same
        file.  Within that exclusion zone it:

        1.  Re-reads the hash immediately after acquiring the lock
            (``hash_now``).
        2.  Compares against ``expected_hash`` if supplied.
        3.  Writes the temporary file and fsyncs.
        4.  Performs a **second hash verification** of the live target
            *inside the lock* immediately before ``os.replace`` so that
            any modification that slipped in between step 2 and the
            replace will be detected.
        5.  Calls ``os.replace`` only when the pre-replace hash still
            matches ``hash_now`` (the hash we verified against).
        """
        try:
            target = self._resolve_and_verify(path)
            lock = _FILE_LOCKS.acquire(str(target))

            with lock:
                return self._write_file_locked(target, content, expected_hash)

        except Exception as e:
            return MutationResult(path=str(path), success=False, error=str(e))

    def _write_file_locked(
        self,
        target: Path,
        content: str | bytes,
        expected_hash: str | None,
    ) -> MutationResult:
        """Inner implementation; called with the per-file threading.Lock held."""

        # ── Step 1: advisory OS-level lock (cross-process serialisation) ──
        lock_fd: int | None = None
        try:
            # Open or create the target so we have an fd to lock.  We use
            # O_CREAT | O_WRONLY so we don't truncate; we just want the fd.
            try:
                lock_fd = os.open(str(target), os.O_CREAT | os.O_WRONLY, 0o666)
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
            except (OSError, AttributeError):
                # fcntl unavailable (Windows) or file can't be opened yet —
                # continue with threading lock only.
                if lock_fd is not None:
                    try:
                        os.close(lock_fd)
                    except OSError:
                        pass
                lock_fd = None

            # ── Step 2: first hash read (inside OS lock) ──────────────────
            hash_now = self._hash(target)

            if expected_hash is not None and expected_hash != hash_now:
                return MutationResult(
                    path=str(target),
                    success=False,
                    error=(
                        f"Stale read detected: expected hash {expected_hash}, "
                        f"but found {hash_now}"
                    ),
                )

            lines_before: list[str] = []
            if target.exists():
                with target.open("r", encoding="utf-8", errors="replace") as f:
                    lines_before = f.readlines()

            target.parent.mkdir(parents=True, exist_ok=True)

            # ── Step 3: write + fsync temp file ──────────────────────────
            fd, temp_path_str = tempfile.mkstemp(
                dir=target.parent,
                text=isinstance(content, str),
            )
            temp_path = Path(temp_path_str)
            try:
                with os.fdopen(fd, "w" if isinstance(content, str) else "wb") as f:
                    f.write(content)
                    f.flush()
                    os.fsync(f.fileno())

                # ── Step 4: second hash check before os.replace ───────────
                # We are still inside the threading lock and the fcntl
                # exclusive lock.  Any OS-level concurrent writer would
                # be blocked (on POSIX).  Within our Python process all
                # other MutationController calls on this path are also
                # queued behind our threading lock.
                pre_replace_hash = self._hash(target)
                if pre_replace_hash != hash_now:
                    # Something changed the target between our first check
                    # and now — abort to avoid a lost-update.
                    temp_path.unlink(missing_ok=True)
                    return MutationResult(
                        path=str(target),
                        success=False,
                        error=(
                            f"TOCTOU race detected immediately before replace: "
                            f"target hash changed from {hash_now!r} to "
                            f"{pre_replace_hash!r}. Write aborted."
                        ),
                    )

                # ── Step 5: atomic commit ─────────────────────────────────
                os.replace(temp_path_str, target)
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise

            hash_after = self._hash(target)

            lines_after: list[str] = []
            with target.open("r", encoding="utf-8", errors="replace") as f:
                lines_after = f.readlines()

            diff = "".join(
                difflib.unified_diff(
                    lines_before,
                    lines_after,
                    fromfile=f"a/{target.relative_to(self.workspace)}",
                    tofile=f"b/{target.relative_to(self.workspace)}",
                )
            )

            result = MutationResult(
                path=str(target),
                success=True,
                diff=diff,
                hash_before=hash_now,
                hash_after=hash_after,
            )
            EventBus.publish(
                EventType.FILE_MODIFIED,
                "global",
                "MutationController",
                {"path": str(target), "diff": diff},
            )
            return result

        finally:
            if lock_fd is not None:
                try:
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
                    os.close(lock_fd)
                except OSError:
                    pass
