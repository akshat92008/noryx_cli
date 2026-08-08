"""Environment variable control layer for Nexus CLI.

Builds minimal, allowlist-based environments for subprocesses, plugins, workers,
and MCP servers to prevent credential leaks and host environment pollution.
"""

from __future__ import annotations

import os
from typing import Mapping, Sequence

DEFAULT_SAFE_ENV_KEYS = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "TERM",
    "TMPDIR",
    "TEMP",
    "TMP",
    "USER",
    "LOGNAME",
    "PYTHONPATH",
    "VIRTUAL_ENV",
    "NEXUS_MODE",
    "NEXUS_WORKSPACE",
)

FORBIDDEN_ENV_KEYS = (
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN",
    "GITLAB_TOKEN",
    "SLACK_BOT_TOKEN",
    "DATABASE_URL",
)


class EnvironmentControl:
    """Builds clean, isolated environment dictionaries."""

    def __init__(
        self,
        safe_keys: Sequence[str] = DEFAULT_SAFE_ENV_KEYS,
        forbidden_keys: Sequence[str] = FORBIDDEN_ENV_KEYS,
    ):
        self.safe_keys = set(safe_keys)
        self.forbidden_keys = set(forbidden_keys)

    def build_environment(
        self,
        custom_env: Mapping[str, str] | None = None,
        *,
        allowed_sensitive_keys: Sequence[str] = (),
    ) -> dict[str, str]:
        """Build minimal environment dictionary from host environment + custom overrides."""
        built: dict[str, str] = {}
        allowed_sensitives = set(allowed_sensitive_keys)

        # Inherit safe keys from host environment
        for key in self.safe_keys:
            val = os.environ.get(key)
            if val is not None:
                built[key] = val

        # Add custom environment overrides
        if custom_env:
            for k, v in custom_env.items():
                if k in self.forbidden_keys and k not in allowed_sensitives:
                    continue  # Strip forbidden keys unless explicitly allowed
                built[k] = v

        return built
