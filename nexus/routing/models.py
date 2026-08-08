"""Shared routing model primitives."""

from __future__ import annotations

from enum import Enum


class ModelTier(Enum):
    """Coarse execution tiers used by routing and collaboration policy."""

    LOCAL_SMALL = "local_small"
    LOCAL_MEDIUM = "local_medium"
    LOCAL_LARGE = "local_large"
    CLOUD_LIGHT = "cloud_light"
    CLOUD_STANDARD = "cloud_standard"
    CLOUD_FRONTIER = "cloud_frontier"
