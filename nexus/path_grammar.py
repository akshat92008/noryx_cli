"""Canonical repository-path grammar used by planning and policy layers."""

from __future__ import annotations

import re

# Deliberately extension-agnostic: repository files such as ``notes.txt`` or
# future language/config extensions must not disappear from planning scope.
# A bounded extension length avoids treating ordinary dotted prose as a path.
PATH_PATTERN = (
    r"(?<![A-Za-z0-9_.-])"
    r"(?:[A-Za-z0-9_.-]+/)*"
    r"[A-Za-z0-9_.-]+\.[A-Za-z0-9][A-Za-z0-9_.-]{0,15}"
    r"(?![A-Za-z0-9_.-])"
)

_PATH_RE = re.compile(PATH_PATTERN, re.IGNORECASE)

_TECHNOLOGY_NAMES = {
    "next.js",
    "node.js",
    "react.js",
    "vue.js",
    "angular.js",
    "three.js",
}


def normalize_repository_path(value: str) -> str:
    """Normalize a user-authored repository path without resolving on disk."""

    return value.strip().strip("`'\"").replace("\\", "/").lstrip("./")


def extract_repository_paths(text: str) -> list[str]:
    """Extract explicit repository file paths from user text.

    The parser is intentionally shared by the planner, Engineering Brain, and
    constraint compiler so one request cannot acquire different mutation scopes
    in different subsystems.
    """

    values: list[str] = []
    for match in _PATH_RE.finditer(text or ""):
        value = normalize_repository_path(match.group(0))
        if value and value.lower() not in _TECHNOLOGY_NAMES:
            values.append(value)
    return list(dict.fromkeys(values))
