"""Filesystem locations for Nexus user state."""

import os
import tempfile
from pathlib import Path


def _usable_state_dir(path: Path) -> bool:
    """Return True only when *path* is actually writable, not merely present."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".write-probe-{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def nexus_home() -> Path:
    """Return the configurable Nexus state directory with safe fallback."""
    configured = os.environ.get("NEXUS_HOME", "").strip()
    if configured:
        p = Path(configured).expanduser().resolve()
        if _usable_state_dir(p):
            return p
    home_dir = Path.home() / ".nexusai"
    if _usable_state_dir(home_dir):
        return home_dir
    fallback = Path(os.getcwd()) / ".nexusai"
    if _usable_state_dir(fallback):
        return fallback
    tmp = Path(tempfile.gettempdir()) / f".nexusai-{os.getuid()}"
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp
