"""Small cross-platform advisory file-lock helpers.

The mutation engine only needs descriptor-level lock/unlock primitives.  Keep
those primitives in one module so POSIX ``flock`` and Windows
``msvcrt.locking`` semantics do not leak into mutation code.
"""

from __future__ import annotations

import os


def lock_file_descriptor(fd: int, *, exclusive: bool = True, blocking: bool = True) -> bool:
    """Acquire an advisory lock for *fd*.

    Returns ``True`` when the lock is held and ``False`` when a non-blocking
    acquisition cannot be satisfied.  Unexpected OS errors are propagated so
    callers can fail closed instead of silently assuming synchronization.
    """

    if fd < 0:
        raise ValueError("file descriptor must be non-negative")

    if os.name == "nt":  # pragma: no cover - exercised by Windows CI
        import msvcrt

        # msvcrt.locking locks byte ranges from the current file position.  A
        # one-byte sentinel keeps the behavior stable for empty files.
        try:
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
        except OSError:
            pass
        os.lseek(fd, 0, os.SEEK_SET)
        if exclusive:
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        else:
            # Windows CRT has no shared-lock equivalent.  Use an exclusive
            # lock to preserve safety rather than pretending shared locking.
            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
        try:
            msvcrt.locking(fd, mode, 1)
            return True
        except OSError:
            if not blocking:
                return False
            raise

    import fcntl

    operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    if not blocking:
        operation |= fcntl.LOCK_NB
    try:
        fcntl.flock(fd, operation)
        return True
    except BlockingIOError:
        return False


def unlock_file_descriptor(fd: int) -> None:
    """Release an advisory lock previously acquired for *fd*."""

    if fd < 0:
        raise ValueError("file descriptor must be non-negative")

    if os.name == "nt":  # pragma: no cover - exercised by Windows CI
        import msvcrt

        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(fd, fcntl.LOCK_UN)
