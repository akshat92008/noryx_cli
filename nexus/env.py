"""Canonical Noryx environment lookup with Noryx 3.x compatibility."""

from __future__ import annotations

import os


def noryx_env(suffix: str, default: str | None = None) -> str | None:
    """Read ``NORYX_<suffix>`` first, then legacy ``NEXUS_<suffix>``."""
    canonical = f"NORYX_{suffix}"
    if canonical in os.environ:
        return os.environ[canonical]
    legacy = f"NEXUS_{suffix}"
    if legacy in os.environ:
        import warnings
        warnings.warn(f"Environment variable {legacy} is deprecated, use {canonical} instead.", DeprecationWarning, stacklevel=2)
        return os.environ[legacy]
    return default


def noryx_env_flag(suffix: str, *, default: bool = False) -> bool:
    value = noryx_env(suffix)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
