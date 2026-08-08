"""Filesystem locations for Noryx user state."""

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


def noryx_home() -> Path:
    """Return the configurable Noryx state directory with safe fallback."""
    configured = (
        os.environ.get("NORYX_HOME", "").strip() or os.environ.get("NEXUS_HOME", "").strip()
    )
    if configured:
        p = Path(configured).expanduser().resolve()
        if _usable_state_dir(p):
            return p
    home_dir = Path.home() / ".noryx"
    if _usable_state_dir(home_dir):
        return home_dir
    # Existing installations remain readable during the 3.x rename window.
    legacy = Path.home() / ".nexusai"
    if legacy.exists() and _usable_state_dir(legacy):
        return legacy
    fallback = Path(os.getcwd()) / ".noryx"
    if _usable_state_dir(fallback):
        return fallback
    uid = getattr(os, "getuid", lambda: 0)()
    tmp = Path(tempfile.gettempdir()) / f".noryx-{uid}"
    tmp.mkdir(parents=True, exist_ok=True)
    return tmp


def nexus_home() -> Path:
    """Compatibility alias for integrations written before the Noryx rename."""
    return noryx_home()
