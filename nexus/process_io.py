"""Cross-platform, timeout-bounded helpers for subprocess pipes."""

from __future__ import annotations

import os
import queue
import threading
from collections.abc import Iterable, Mapping
from typing import TextIO

_SAFE_SUBPROCESS_ENV_KEYS = frozenset(
    {
        "PATH",
        "USER",
        "USERNAME",
        "HOME",
        "USERPROFILE",
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
        "NODE_ENV",
    }
)


def filtered_subprocess_env(
    *,
    overrides: Mapping[str, str] | None = None,
    allowed_names: Iterable[str] = (),
) -> dict[str, str]:
    """Return a minimal environment that can still launch children on Windows.

    Windows requires ``SystemRoot`` for some executables and Python versions.
    Keep platform bootstrap variables while deliberately excluding injection
    vectors such as ``PYTHONPATH`` unless a caller explicitly supplies them.
    """

    allowed = _SAFE_SUBPROCESS_ENV_KEYS | {name.upper() for name in allowed_names}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    if overrides:
        env.update({str(key): str(value) for key, value in overrides.items()})
    return env


def readline_with_timeout(stream: TextIO, timeout: float) -> str | None:
    """Read one line without relying on POSIX ``select`` for pipe handles.

    Windows cannot use ``select.select`` with anonymous subprocess pipes.  A
    daemon reader lets both platforms enforce the same wall-clock timeout.  A
    timed-out caller must terminate the owning process so the reader is
    released and no second reader is started on the same stream.
    """

    output: queue.Queue[str | BaseException] = queue.Queue(maxsize=1)

    def read() -> None:
        try:
            output.put(stream.readline())
        except BaseException as exc:  # pragma: no cover - platform pipe failures
            output.put(exc)

    thread = threading.Thread(target=read, name="nexus-pipe-reader", daemon=True)
    thread.start()
    try:
        value = output.get(timeout=max(0.01, float(timeout)))
    except queue.Empty:
        return None
    if isinstance(value, BaseException):
        raise OSError(str(value)) from value
    return value
