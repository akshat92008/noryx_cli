"""Installation and runtime diagnostics for Noryx.

The doctor is intentionally side-effect free: it validates the current workspace,
provider configuration, local Nova availability, and sandbox capabilities without
executing arbitrary repository commands or making hosted model calls.

Pass ``ping=True`` to ``run_doctor`` / ``doctor_report`` to enable the optional
"Hosted provider (live)" check that makes a real minimal completion request and
reports the round-trip latency.  This check is **not** included by default so
that the standard ``noryx --doctor`` remains side-effect free.
"""

from __future__ import annotations

import os
import platform
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from nexus import __version__
from nexus.preflight import BackendProbe, probe_hosted, probe_ollama
from nexus.sandbox import SandboxBackend, SandboxRunner

# Modes whose advertised workflow requires native filesystem and network isolation.
_ISOLATION_REQUIRED_MODES = frozenset(
    {
        "review",
        "workspace",
        "autonomous",
        "local-only",
        "quality",
        "budget",
        "ci",
    }
)


@dataclass(frozen=True)
class DoctorCheck:
    name: str
    status: str  # pass | warn | fail
    detail: str
    remediation: tuple[str, ...] = ()

    @property
    def marker(self) -> str:
        return {"pass": "[✓]", "warn": "[!]", "fail": "[✗]"}[self.status]

    def render(self) -> str:
        lines = [f"{self.marker} {self.name}: {self.detail}"]
        lines.extend(f"    {item}" for item in self.remediation)
        return "\n".join(lines)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _provider_check(name: str, probe: BackendProbe, *, optional: bool) -> DoctorCheck:
    if probe.ready:
        return DoctorCheck(name, "pass", probe.detail)
    return DoctorCheck(
        name,
        "warn" if optional else "fail",
        probe.detail,
        tuple(probe.remediation),
    )


def _sandbox_check(workspace: Path, mode: str | None) -> DoctorCheck:
    try:
        backend = SandboxRunner(workspace).backend()
    except (OSError, ValueError) as exc:
        return DoctorCheck("Sandbox", "fail", f"Sandbox probe failed: {exc}")

    mode_name = (mode or "auto").strip().lower()
    native = backend in {SandboxBackend.BUBBLEWRAP, SandboxBackend.MACOS}
    if native:
        return DoctorCheck("Sandbox", "pass", f"Native isolation available via {backend.value}.")

    # Review is allowed to start, but commands that require isolation will still
    # be blocked.  Strong autonomous modes are not allowed to start at all.
    requires_native = mode_name in _ISOLATION_REQUIRED_MODES
    detail = (
        "Only unisolated host-process execution is available; commands requiring native "
        "filesystem/network isolation will be blocked."
    )
    remediation: tuple[str, ...] = ()
    system = platform.system().lower()
    if system == "linux":
        remediation = (
            "Install bubblewrap (Debian/Ubuntu: sudo apt-get install bubblewrap).",
            "Re-run: noryx --doctor --mode autonomous",
        )
    elif system == "darwin":
        remediation = ("Ensure /usr/bin/sandbox-exec is available and permitted by system policy.",)
    else:
        remediation = (
            "Use plan/review mode, or run Noryx inside a trusted VM/container with OS isolation.",
        )
    return DoctorCheck("Sandbox", "fail" if requires_native else "warn", detail, remediation)


def _workspace_check(workspace: Path) -> DoctorCheck:
    if not workspace.exists():
        return DoctorCheck("Workspace", "fail", f"Path does not exist: {workspace}")
    if not workspace.is_dir():
        return DoctorCheck("Workspace", "fail", f"Path is not a directory: {workspace}")
    readable = os.access(workspace, os.R_OK)
    writable = os.access(workspace, os.W_OK)
    if readable and writable:
        return DoctorCheck("Workspace", "pass", f"Readable and writable: {workspace}")
    if readable:
        return DoctorCheck("Workspace", "warn", f"Readable but not writable: {workspace}")
    return DoctorCheck("Workspace", "fail", f"Workspace is not readable: {workspace}")


def _capability_status(checks: Iterable[DoctorCheck], mode: str | None) -> str:
    rows = list(checks)
    failures = {item.name for item in rows if item.status == "fail"}
    normalized = (mode or "auto").strip().lower()
    if not failures:
        if normalized == "plan":
            return "READY_FOR_PLAN_ONLY"
        if normalized in _ISOLATION_REQUIRED_MODES:
            return "READY_FOR_VERIFIED_REPAIR"
        return "READY_FOR_ANALYSIS"
    if failures == {"Sandbox"}:
        return "READY_FOR_ANALYSIS_ONLY"
    return "NOT_READY"


def _render(checks: Iterable[DoctorCheck], mode: str | None) -> str:
    rows = list(checks)
    overall = _capability_status(rows, mode)
    return "\n".join(
        [
            f"Noryx doctor — Noryx {__version__}",
            f"Status: {overall}",
            f"Mode: {(mode or 'auto')}",
            f"Python: {sys.version.split()[0]} ({platform.system()} {platform.machine()})",
            "",
            *(check.render() for check in rows),
        ]
    )


def run_doctor(
    working_dir: str | os.PathLike[str] | None = None, mode: str | None = None
) -> tuple[bool, str]:
    """Run deterministic diagnostics and return ``(ready, human_report)``.

    At least one model backend must be configured.  Local Nova is optional when a
    hosted provider is configured, and hosted credentials are optional when Nova
    is available.  Native sandbox absence blocks only modes that require it.
    """

    ready, payload = doctor_report(working_dir, mode)
    checks = [DoctorCheck(**item) for item in payload["checks"]]
    return ready, _render(checks, mode)


def doctor_report(
    working_dir: str | os.PathLike[str] | None = None,
    mode: str | None = None,
) -> tuple[bool, dict[str, object]]:
    """Return diagnostics as a machine-readable payload.

    This is the canonical doctor implementation; ``run_doctor`` renders the
    same checks for humans so JSON and text output cannot drift.
    """

    workspace = Path(working_dir or os.getcwd()).expanduser().resolve()
    hosted = probe_hosted()
    local = probe_ollama(use_cache=False)
    has_backend = hosted.ready or local.ready

    checks = [
        _workspace_check(workspace),
        _sandbox_check(workspace, mode),
        # Fix #13: Label clarified — this checks credentials only, not live completion.
        _provider_check("Hosted provider (credentials)", hosted, optional=local.ready),
        _provider_check("Local Nova", local, optional=hosted.ready),
    ]
    if not has_backend:
        checks.append(
            DoctorCheck(
                "Model backend",
                "fail",
                "Neither a hosted provider nor local Nova is ready.",
                ("Configure one hosted API key or start Ollama with the Nova model.",),
            )
        )

    ready = not any(check.status == "fail" for check in checks)
    payload: dict[str, object] = {
        "schema_version": "nexus.doctor.v1",
        "version": __version__,
        "ready": ready,
        "status": _capability_status(checks, mode),
        "mode": mode or "auto",
        "python": sys.version.split()[0],
        "platform": platform.system(),
        "machine": platform.machine(),
        "workspace": str(workspace),
        "checks": [check.to_dict() for check in checks],
    }
    return ready, payload


def ping_live_provider(
    timeout: float = 10.0,
) -> DoctorCheck:
    """Fix #13: Make a real minimal completion request to the hosted provider.

    This check is intentionally NOT run by ``run_doctor`` / ``doctor_report``
    unless ``ping=True`` is passed, because it has side effects (network I/O,
    token spend) and may take up to ``timeout`` seconds.

    Returns a ``DoctorCheck`` with round-trip latency in the detail string.
    """
    import time

    try:
        from nexus.api import NvidiaClient
    except ImportError:
        return DoctorCheck(
            "Hosted provider (live)",
            "fail",
            "Cannot import NvidiaClient — Noryx API module not available.",
        )

    try:
        client = NvidiaClient()  # Uses existing env-var credentials.
        start = time.monotonic()
        # Minimal non-tool request: single user turn, 1-token max response.
        client.chat(
            model_id=getattr(client, "custom_model", "") or "meta/llama-3.3-70b-instruct",
            messages=[{"role": "user", "content": "Reply with the word pong and nothing else."}],
            max_tokens=8,
            temperature=0.0,
            stream=False,
        )
        elapsed = time.monotonic() - start
        return DoctorCheck(
            "Hosted provider (live)",
            "pass",
            f"Live completion succeeded in {elapsed:.2f}s.",
        )
    except Exception as exc:  # noqa: BLE001  — broad catch intentional in diagnostics
        return DoctorCheck(
            "Hosted provider (live)",
            "fail",
            f"Live completion failed: {exc}",
            ("Check network connectivity and API key validity.",),
        )
