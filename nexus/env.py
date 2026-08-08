"""Canonical Noryx environment lookup with Noryx 3.x compatibility."""

from __future__ import annotations

import os


def noryx_env(suffix: str, default: str | None = None) -> str | None:
    """Read ``NORYX_<suffix>`` first, then legacy ``NEXUS_<suffix>``."""
    canonical = f"NORYX_{suffix}"
    if canonical in os.environ:
        return os.environ[canonical]
    return os.environ.get(f"NEXUS_{suffix}", default)


def noryx_env_flag(suffix: str, *, default: bool = False) -> bool:
    value = noryx_env(suffix)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
