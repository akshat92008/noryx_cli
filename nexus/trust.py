"""Content-addressed trust approval for project instructions and executable config.

Architecture
------------
Trust state is stored **outside** the repository so that model-controlled code
cannot modify it through ordinary tool execution:

    ~/.noryx/trust/<repository-id>.json

The repository identity is the SHA-256 of the canonical resolved repository
path.  This means trust records survive repository moves only when the path
changes are explicitly re-approved.

Two classes expose trust operations:

TrustAuthority
    Full read/write access.  Instantiated only in the explicit user-approval
    code path (CLI prompts).  MUST NOT be passed to agent tools.

TrustReader
    Read-only view.  Safe to pass to agent tools, plugins, MCP gateways, and
    verification code.  Has no ``approve``/``revoke`` methods.

The key invariant:

    MODEL → may modify repository
    MODEL ✗ cannot modify trust authority
    USER  → explicit approval → TrustAuthority → external trust store
"""

from __future__ import annotations

import difflib
import hashlib
import json
import os
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nexus.project_files import (
    PROJECT_INSTRUCTION_FILES,
    TRUST_SENSITIVE_CONTROL_PATTERNS,
)

# ---------------------------------------------------------------------------
# Schema version — increment when record format changes in a breaking way
# ---------------------------------------------------------------------------
_SCHEMA_VERSION = 2

# ---------------------------------------------------------------------------
# Trust-scan ignore set — path PARTS (not directories themselves) that are
# runtime-only artefacts.  We never use a bare ".noryx" entry here because
# that would silently swallow security-sensitive control files.
# ---------------------------------------------------------------------------
_TRUST_SCAN_IGNORE_PARTS: frozenset[str] = frozenset(
    {
        ".git",
        ".nexusai",
        "node_modules",
        ".venv",
        "venv",
        "dist",
        "build",
        "__pycache__",
    }
)

# Runtime sub-directories inside .noryx / .nexus that should never appear
# in trust scans.  Checked as path components relative to the control dir.
_CONTROL_DIR_RUNTIME_SUBDIRS: frozenset[str] = frozenset({"cache", "logs", "tmp"})

# Legacy constant kept for external callers that imported TRUSTED_CONFIG_NAMES.
# New code should use PROJECT_INSTRUCTION_FILES from nexus.project_files.
TRUSTED_CONFIG_NAMES: frozenset[str] = frozenset(PROJECT_INSTRUCTION_FILES)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TrustDecision:
    path: str
    digest: str
    approved: bool
    changed: bool
    diff: str = ""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _repo_id(canonical_path: Path) -> str:
    """Stable identifier for a repository — SHA-256 of its canonical path."""
    return hashlib.sha256(str(canonical_path).encode()).hexdigest()[:40]


def _trust_store_dir() -> Path:
    """Return the external trust store directory.

    Uses ``noryx_home()`` from nexus.paths which respects NORYX_HOME / NEXUS_HOME
    environment variables.  The directory is guaranteed to be outside the current
    working directory.
    """
    from nexus.paths import noryx_home  # local import to avoid circularity

    return noryx_home() / "trust"


def trust_store_dir() -> Path:
    """Public API: return the external trust authority directory.

    Used by :class:`~nexus.sandbox.SandboxRunner` to build OS-level deny rules
    that prevent model-controlled processes from accessing the trust store.
    This function itself must only be called from host-side (non-sandboxed) code.
    """
    return _trust_store_dir()


def _trust_file_for(working_dir: Path) -> Path:
    """Return the path of the trust record for *working_dir*."""
    rid = _repo_id(working_dir)
    return _trust_store_dir() / f"{rid}.json"


def _assert_not_inside_repo(trust_path: Path, working_dir: Path) -> None:
    """Raise TrustStoreError if trust_path is inside working_dir.

    This is the key security check: the trust authority must live outside the
    model-writable repository.
    """
    try:
        trust_path.relative_to(working_dir)
        raise TrustStoreError(
            f"Trust store location {trust_path} is inside the repository "
            f"{working_dir}. This would allow model-controlled code to modify "
            "the trust authority. Set NORYX_HOME to a location outside all "
            "repositories, or use a dedicated machine account."
        )
    except ValueError:
        pass  # trust_path is not inside working_dir — correct


def _load_trust_file(path: Path) -> dict[str, Any]:
    """Load a trust record file, failing closed on any error."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return {}
        if raw.get("schema_version", 0) != _SCHEMA_VERSION:
            warnings.warn(
                f"Trust record at {path} has schema_version "
                f"{raw.get('schema_version')!r} (expected {_SCHEMA_VERSION}). "
                "Treating as empty — re-approval required.",
                stacklevel=3,
            )
            return {}
        return raw
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return {}


def _sha256_of(path: Path) -> tuple[str, str]:
    """Return (hex-digest, decoded-text) for *path*.  Raises OSError on failure."""
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    text = raw.decode("utf-8", errors="replace")
    return digest, text


# ---------------------------------------------------------------------------
# Public exceptions
# ---------------------------------------------------------------------------


class TrustStoreError(RuntimeError):
    """Raised when the trust store cannot be accessed safely."""


# ---------------------------------------------------------------------------
# TrustAuthority — WRITE access.  Must not be passed to agent tools.
# ---------------------------------------------------------------------------


class TrustAuthority:
    """Read/write trust authority stored outside the repository.

    This class should only be instantiated in the explicit user-approval
    code path.  Agent tools, plugins, MCP gateways, and verification code
    must receive a :class:`TrustReader` instead.
    """

    def __init__(self, working_dir: str | Path) -> None:
        self.working_dir = Path(working_dir).resolve()
        self._trust_path = _trust_file_for(self.working_dir)
        _assert_not_inside_repo(self._trust_path, self.working_dir)
        self._trust_path.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load()
        self._migrate_legacy_if_present()

    # ── Public write API ────────────────────────────────────────────────────

    def approve(
        self,
        path: str | Path,
        expected_digest: str | None = None,
        expected_content: str | None = None,
    ) -> TrustDecision:
        """Record user approval for *path* at its current digest."""
        decision = self.inspect(path, expected_digest, expected_content)
        if not decision.digest:
            return decision
        p = Path(decision.path)
        approvals = self._state.setdefault("approvals", {})
        approvals[decision.path] = {
            "digest": decision.digest,
            "approved": True,
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "content_snapshot": (
                expected_content
                if expected_content is not None
                else (p.read_text(encoding="utf-8", errors="replace") if p.is_file() else "")
            ),
        }
        self._save()
        decision.approved = True
        return decision

    def revoke(self, path: str | Path) -> None:
        """Remove any existing approval for *path*."""
        p = str(Path(path).expanduser().resolve())
        approvals = self._state.get("approvals", {})
        if p in approvals:
            approvals.pop(p)
            self._save()

    def reject(
        self,
        path: str | Path,
        expected_digest: str | None = None,
        expected_content: str | None = None,
    ) -> TrustDecision:
        """Record an explicit rejection for *path*."""
        decision = self.inspect(path, expected_digest, expected_content)
        p = Path(decision.path)
        approvals = self._state.setdefault("approvals", {})
        approvals[decision.path] = {
            "digest": decision.digest,
            "approved": False,
            "approved_at": datetime.now(timezone.utc).isoformat(),
            "content_snapshot": (
                expected_content
                if expected_content is not None
                else (p.read_text(encoding="utf-8", errors="replace") if p.is_file() else "")
            ),
        }
        self._save()
        return decision

    # ── Shared read API (also on TrustReader) ───────────────────────────────

    def inspect(
        self,
        path: str | Path,
        expected_digest: str | None = None,
        expected_content: str | None = None,
    ) -> TrustDecision:
        return _inspect_impl(
            self._state, path, expected_digest, expected_content
        )

    def is_approved(
        self,
        path: str | Path,
        expected_digest: str | None = None,
        expected_content: str | None = None,
    ) -> bool:
        return self.inspect(path, expected_digest, expected_content).approved

    def status(self, path: str | Path) -> dict[str, Any]:
        """Return the raw stored approval record for *path*, or {}."""
        p = str(Path(path).expanduser().resolve())
        return dict(self._state.get("approvals", {}).get(p, {}))

    def verify_digest(self, path: str | Path) -> bool:
        """Return True if *path* exists and matches its approved digest."""
        decision = self.inspect(path)
        return decision.approved

    def scan_project(self) -> list[TrustDecision]:
        return _scan_project_impl(self._state, self.working_dir)

    def as_reader(self) -> "TrustReader":
        """Return a read-only view of this authority."""
        return TrustReader(str(self.working_dir))

    # ── Private ─────────────────────────────────────────────────────────────

    def _load(self) -> dict[str, Any]:
        return _load_trust_file(self._trust_path)

    def _save(self) -> None:
        self._state["schema_version"] = _SCHEMA_VERSION
        self._state["repository_id"] = _repo_id(self.working_dir)
        self._state["canonical_path"] = str(self.working_dir)
        data = json.dumps(self._state, indent=2)
        # Atomic write via rename
        tmp = self._trust_path.with_suffix(".tmp")
        tmp.write_text(data, encoding="utf-8")
        os.replace(tmp, self._trust_path)

    def _migrate_legacy_if_present(self) -> None:
        """Warn if old in-repo trust store exists; never migrate automatically."""
        legacy = self.working_dir / ".noryx" / "trusted-config.json"
        if legacy.is_file():
            warnings.warn(
                f"Found legacy in-repo trust store at {legacy}. "
                "This file is inside the model-writable workspace and is "
                "no longer authoritative. Re-approve project instruction files "
                "to record them in the secure external trust store "
                f"({self._trust_path}). The legacy file has NOT been migrated "
                "automatically because it could have been modified by an "
                "untrusted process.",
                UserWarning,
                stacklevel=2,
            )


# ---------------------------------------------------------------------------
# TrustReader — READ-ONLY.  Safe to pass to agent tools.
# ---------------------------------------------------------------------------


class TrustReader:
    """Read-only view of the external trust store.

    Intentionally has no ``approve``, ``revoke``, or ``reject`` methods.
    Any attempt to assign such attributes will be blocked by ``__setattr__``.

    Pass this class — not TrustAuthority — to agent tools, plugins, MCP
    gateways, verification code, and subagents.
    """

    _FORBIDDEN_ATTRS = frozenset({"approve", "revoke", "reject", "_save", "_state"})

    def __init__(self, working_dir: str | Path) -> None:
        object.__setattr__(self, "_working_dir", Path(working_dir).resolve())
        trust_path = _trust_file_for(object.__getattribute__(self, "_working_dir"))
        state = _load_trust_file(trust_path)
        object.__setattr__(self, "_ro_state", state)

    def __setattr__(self, name: str, value: object) -> None:
        if name in self._FORBIDDEN_ATTRS:
            raise AttributeError(
                f"TrustReader is read-only: cannot set '{name}'. "
                "Use TrustAuthority for write operations."
            )
        object.__setattr__(self, name, value)

    def inspect(
        self,
        path: str | Path,
        expected_digest: str | None = None,
        expected_content: str | None = None,
    ) -> TrustDecision:
        return _inspect_impl(
            object.__getattribute__(self, "_ro_state"),
            path,
            expected_digest,
            expected_content,
        )

    def is_approved(
        self,
        path: str | Path,
        expected_digest: str | None = None,
        expected_content: str | None = None,
    ) -> bool:
        return self.inspect(path, expected_digest, expected_content).approved

    def scan_project(self) -> list[TrustDecision]:
        return _scan_project_impl(
            object.__getattribute__(self, "_ro_state"),
            object.__getattribute__(self, "_working_dir"),
        )


# ---------------------------------------------------------------------------
# TrustStore — backward-compatible alias.
#
# Existing callers (agent/core.py, platform/mcp_gateway.py, verification.py)
# use TrustStore for read operations only.  This alias maintains compatibility
# while the codebase migrates to TrustReader for read paths and TrustAuthority
# for write paths.
#
# DEPRECATION: TrustStore will be removed in a future release.  New code must
# use TrustReader (for agent tools) or TrustAuthority (for user-approval paths).
# ---------------------------------------------------------------------------


class TrustStore(TrustReader):
    """Backward-compatible read-only alias for TrustReader.

    .. deprecated::
        Use :class:`TrustReader` for agent-facing code or
        :class:`TrustAuthority` for user-approval paths.
    """

    def __init__(self, working_dir: str | Path) -> None:
        warnings.warn(
            "TrustStore is deprecated. Use TrustReader for read-only access "
            "or TrustAuthority for the user-approval code path.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(working_dir)


# ---------------------------------------------------------------------------
# Shared implementation functions (not part of the public API)
# ---------------------------------------------------------------------------


def _inspect_impl(
    state: dict[str, Any],
    path: str | Path,
    expected_digest: str | None,
    expected_content: str | None,
) -> TrustDecision:
    p = Path(path).expanduser().resolve()
    if not p.is_file() and expected_digest is None:
        return TrustDecision(str(p), "", False, False, "file does not exist")

    if expected_digest is not None:
        digest = expected_digest
        new_text = expected_content or ""
    else:
        try:
            digest, new_text = _sha256_of(p)
        except OSError as exc:
            return TrustDecision(str(p), "", False, False, f"read error: {exc}")

    approvals: dict[str, Any] = state.get("approvals", {})
    previous = approvals.get(str(p), {})
    approved = previous.get("digest") == digest and bool(previous.get("approved"))
    old_text = previous.get("content_snapshot", "")
    changed = bool(previous) and previous.get("digest") != digest
    diff = "".join(
        difflib.unified_diff(
            old_text.splitlines(keepends=True),
            new_text.splitlines(keepends=True),
            fromfile=f"approved/{p.name}",
            tofile=f"current/{p.name}",
        )
    )
    return TrustDecision(str(p), digest, approved, changed, diff)


def _is_control_dir_runtime_path(path: Path, working_dir: Path) -> bool:
    """Return True if *path* is inside a runtime sub-directory of .noryx/.nexus.

    Examples of runtime paths (excluded from trust scans):
        .noryx/cache/...
        .noryx/logs/...
        .noryx/tmp/...

    Examples of sensitive paths (included in trust scans):
        .noryx/skills/myrule.md
        .noryx/policies.yml
    """
    try:
        rel = path.relative_to(working_dir)
    except ValueError:
        return False

    parts = rel.parts
    if len(parts) < 2:
        return False
    if parts[0] not in {".noryx", ".nexus"}:
        return False
    # If the second path component is a known runtime sub-directory, exclude.
    return parts[1] in _CONTROL_DIR_RUNTIME_SUBDIRS


def _scan_project_impl(
    state: dict[str, Any],
    working_dir: Path,
) -> list[TrustDecision]:
    """Collect all trust-sensitive files and return their inspection results.

    Two categories of candidates:
    1. Files whose name matches PROJECT_INSTRUCTION_FILES (rglob across repo).
    2. Files inside .noryx / .nexus control directories that match
       TRUST_SENSITIVE_CONTROL_PATTERNS.

    Filtering rules:
    - Drop any path whose parts intersect _TRUST_SCAN_IGNORE_PARTS (broad
      noise: node_modules, venv, __pycache__, etc.).
    - Drop any path that is a runtime sub-directory of .noryx/.nexus
      (cache/, logs/, tmp/).
    - NEVER use a bare ".noryx" or ".nexus" entry as a drop rule — that
      would silently discard security-sensitive control files.
    """
    candidates: set[Path] = set()

    # Category 1: named instruction files anywhere in the repo
    for name in PROJECT_INSTRUCTION_FILES:
        candidates.update(working_dir.rglob(name))

    # Category 2: trust-sensitive control-dir files
    for control_dir, pattern in TRUST_SENSITIVE_CONTROL_PATTERNS:
        control_path = working_dir / control_dir
        if control_path.is_dir():
            candidates.update(control_path.glob(pattern))

    results = []
    for p in sorted(candidates):
        # Drop broad runtime noise (but not ".noryx" as a top-level part)
        if _TRUST_SCAN_IGNORE_PARTS.intersection(p.parts):
            continue
        # Drop runtime sub-paths inside .noryx / .nexus
        if _is_control_dir_runtime_path(p, working_dir):
            continue
        results.append(_inspect_impl(state, p, None, None))

    return results
