"""Immutable execution context for every Nexus operation.

An Agent must never depend on process-wide ``os.getcwd()``.  Every file,
Git, command, verification, hook, plugin, MCP, and history operation must
receive or derive its workspace from a :class:`RunContext`.
"""

from __future__ import annotations

import contextvars
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping


@dataclass(frozen=True)
class PermissionPolicy:
    """Describes the permission boundaries for a run."""

    mode: str = "default"  # "default", "permissive", "locked", "ci"
    allowed_tools: frozenset[str] = frozenset()
    disallowed_tools: frozenset[str] = frozenset()
    auto_approve_reads: bool = True
    auto_approve_writes: bool = False
    max_file_size_bytes: int = 10 * 1024 * 1024  # 10 MB


@dataclass(frozen=True)
class BudgetLimitsSnapshot:
    """Immutable snapshot of budget constraints for a run."""

    max_hosted_calls: int | None = None
    max_prompt_tokens: int | None = None
    max_completion_tokens: int | None = None
    max_cost_usd: float | None = None


@dataclass(frozen=True)
class RunContext:
    """Immutable execution context for a Nexus run.

    Every tool, hook, plugin, and subagent operation derives its workspace,
    permissions, and identity from this object.  It is set as a ContextVar
    so concurrent runs in different threads are isolated.

    **Thread safety**: ``contextvars.ContextVar`` provides per-task / per-thread
    scoping automatically.  Parallel Agents running in different threads will
    each see their own ``RunContext``.
    """

    run_id: str
    session_id: str
    source_root: Path
    workspace_root: Path
    additional_roots: tuple[Path, ...] = ()
    permission_policy: PermissionPolicy = field(default_factory=PermissionPolicy)
    budget: BudgetLimitsSnapshot = field(default_factory=BudgetLimitsSnapshot)
    environment_grants: Mapping[str, str] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    model_key: str = ""
    workspace_isolated: bool = False

    # ── Path helpers ─────────────────────────────────────────────────────

    def resolve_path(self, path: str | Path) -> Path:
        """Resolve a path relative to the workspace root.

        Always returns an absolute path anchored at ``workspace_root``.
        Raises ``ValueError`` if the result escapes the workspace.
        """
        p = Path(path)
        if p.is_absolute():
            resolved = p.resolve()
        else:
            resolved = (self.workspace_root / p).resolve()
        self._assert_within_authorized_roots(resolved)
        return resolved

    def is_within_workspace(self, path: str | Path) -> bool:
        """Check whether *path* is inside the workspace."""
        try:
            self._assert_within_authorized_roots(Path(path).resolve())
            return True
        except ValueError:
            return False

    def relative_to_workspace(self, path: str | Path) -> Path:
        """Return the workspace-relative form of *path*."""
        resolved = Path(path).resolve()
        return resolved.relative_to(self.workspace_root)

    def _assert_within_workspace(self, resolved: Path) -> None:
        try:
            resolved.relative_to(self.workspace_root)
        except ValueError:
            raise ValueError(
                f"Path {resolved} is outside the workspace {self.workspace_root}"
            ) from None

    def _assert_within_authorized_roots(self, resolved: Path) -> None:
        roots = (self.workspace_root, *self.additional_roots)
        for root in roots:
            try:
                resolved.relative_to(root)
                return
            except ValueError:
                continue
        rendered = ", ".join(str(root) for root in roots)
        raise ValueError(f"Path {resolved} is outside authorized roots: {rendered}")

    # ── Factory ──────────────────────────────────────────────────────────

    @classmethod
    def create(
        cls,
        *,
        source_root: str | Path,
        workspace_root: str | Path | None = None,
        additional_roots: tuple[str | Path, ...] | list[str | Path] | None = None,
        session_id: str | None = None,
        permission_mode: str = "default",
        allowed_tools: frozenset[str] | None = None,
        disallowed_tools: frozenset[str] | None = None,
        model_key: str = "",
        workspace_isolated: bool = False,
        max_hosted_calls: int | None = None,
        max_cost_usd: float | None = None,
    ) -> "RunContext":
        """Convenience factory from Agent-like parameters."""
        src = Path(source_root).resolve()
        ws = Path(workspace_root).resolve() if workspace_root else src
        return cls(
            run_id=uuid.uuid4().hex[:16],
            session_id=session_id or datetime.now().strftime("%Y%m%d_%H%M%S_%f"),
            source_root=src,
            workspace_root=ws,
            additional_roots=tuple(Path(item).expanduser().resolve() for item in (additional_roots or ())),
            permission_policy=PermissionPolicy(
                mode=permission_mode,
                allowed_tools=allowed_tools or frozenset(),
                disallowed_tools=disallowed_tools or frozenset(),
            ),
            budget=BudgetLimitsSnapshot(
                max_hosted_calls=max_hosted_calls,
                max_cost_usd=max_cost_usd,
            ),
            model_key=model_key,
            workspace_isolated=workspace_isolated,
        )


# ── ContextVar for thread-safe run isolation ────────────────────────────────

_current_run_context: contextvars.ContextVar[RunContext | None] = contextvars.ContextVar(
    "_current_run_context", default=None
)


def get_run_context() -> RunContext | None:
    """Return the RunContext for the current execution thread/task."""
    return _current_run_context.get()


def require_run_context() -> RunContext:
    """Return the RunContext or raise if none is set."""
    ctx = _current_run_context.get()
    if ctx is None:
        raise RuntimeError(
            "No RunContext is set.  All tool and runtime operations require "
            "an active RunContext.  Use `set_run_context()` or the "
            "`run_context()` context manager."
        )
    return ctx


def set_run_context(ctx: RunContext) -> contextvars.Token:
    """Set the RunContext for the current thread/task."""
    return _current_run_context.set(ctx)


@contextmanager
def run_context_scope(ctx: RunContext):
    """Context manager that sets and resets the RunContext."""
    token = _current_run_context.set(ctx)
    try:
        yield ctx
    finally:
        _current_run_context.reset(token)
