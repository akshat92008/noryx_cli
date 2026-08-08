"""Behavioral qualification for the host's native sandbox boundary."""
from __future__ import annotations

import hashlib
import json
import os
import platform
import socket
import sys
import tempfile
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from nexus.provenance import resolve_source_identity
from nexus.sandbox import CommandSpec, SandboxBackend, SandboxRunner


@dataclass(frozen=True)
class SandboxProbeResult:
    name: str
    passed: bool
    expected: str
    observed: str
    backend: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class SandboxQualification:
    schema_version: str
    os: str
    os_release: str
    python: str
    backend: str
    probes: list[SandboxProbeResult]
    process_containment: bool
    filesystem_isolation: bool
    network_isolation: bool
    autonomous_ready: bool
    supported_mode: str
    workspace_tree_hash: str = ""
    source_revision: str = ""
    qualification_hash: str = ""
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["probes"] = [asdict(item) for item in self.probes]
        return payload

    def seal(self) -> "SandboxQualification":
        payload = self.to_dict()
        payload["qualification_hash"] = ""
        self.qualification_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return self


class NativeSandboxQualifier:
    def __init__(self, workspace: str | Path, *, source_revision: str = ""):
        self.workspace = Path(workspace).expanduser().resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.runner = SandboxRunner(self.workspace)
        if source_revision:
            self.source_revision = source_revision
        elif os.environ.get("GITHUB_SHA"):
            self.source_revision = f"git:{os.environ['GITHUB_SHA'].strip()}"
        else:
            repository = Path(__file__).resolve().parents[2]
            self.source_revision = resolve_source_identity(repository).revision

    def qualify(self) -> SandboxQualification:
        backend = self.runner.backend()
        probes = [
            self._workspace_write(backend),
            self._timeout(backend),
            self._outside_read(backend),
            self._outside_write(backend),
            self._network_denied(backend),
        ]
        lookup = {item.name: item.passed for item in probes}
        process_containment = lookup["timeout_terminates_process_group"]
        strong_backend = backend in {SandboxBackend.BUBBLEWRAP, SandboxBackend.MACOS}
        filesystem_isolation = strong_backend and lookup["outside_read_denied"] and lookup["outside_write_denied"]
        network_isolation = strong_backend and lookup["network_denied"]
        autonomous = strong_backend and all(
            (process_containment, filesystem_isolation, network_isolation, lookup["workspace_write_allowed"])
        )
        mode = "autonomous" if autonomous else "analysis-only"
        qualification = SandboxQualification(
            schema_version="nexus.sandbox-qualification.v2",
            os=platform.system(),
            os_release=platform.release(),
            python=sys.version.split()[0],
            backend=backend.value,
            probes=probes,
            process_containment=process_containment,
            filesystem_isolation=filesystem_isolation,
            network_isolation=network_isolation,
            autonomous_ready=autonomous,
            supported_mode=mode,
            workspace_tree_hash=self._workspace_tree_hash(),
            source_revision=getattr(self, "source_revision", ""),
        )
        return qualification.seal()

    def _run(
        self,
        code: str,
        *,
        timeout: float = 5.0,
        network: bool = False,
        env: Mapping[str, str] | None = None,
    ):
        return self.runner.run(
            CommandSpec.create(
                [sys.executable, "-c", code],
                self.workspace,
                timeout_seconds=timeout,
                network=network,
                env=env,
                require_os_isolation=False,
                allow_unisolated_host_process=True,
            )
        )

    def _workspace_write(self, backend: SandboxBackend) -> SandboxProbeResult:
        target = self.workspace / ".nexus-sandbox-probe"
        target.unlink(missing_ok=True)
        result = self._run("from pathlib import Path; Path('.nexus-sandbox-probe').write_text('ok')")
        passed = result.success and target.exists() and target.read_text() == "ok"
        target.unlink(missing_ok=True)
        return self._probe("workspace_write_allowed", passed, "write succeeds inside workspace", result, backend)

    def _timeout(self, backend: SandboxBackend) -> SandboxProbeResult:
        result = self._run("import time; time.sleep(30)", timeout=0.2)
        return self._probe("timeout_terminates_process_group", result.timed_out, "process group is terminated at deadline", result, backend)

    def _outside_read(self, backend: SandboxBackend) -> SandboxProbeResult:
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write("outside-secret")
            outside = Path(handle.name)
        try:
            code = "import os; from pathlib import Path; print(Path(os.environ['NEXUS_PROBE_PATH']).read_text())"
            result = self._run(code, env={"NEXUS_PROBE_PATH": str(outside)})
            passed = not result.success and "outside-secret" not in result.stdout
            return self._probe("outside_read_denied", passed, "runtime-derived read outside workspace is denied", result, backend)
        finally:
            outside.unlink(missing_ok=True)

    def _outside_write(self, backend: SandboxBackend) -> SandboxProbeResult:
        outside = Path(tempfile.gettempdir()) / f"nexus-outside-{os.getpid()}-{os.urandom(4).hex()}.txt"
        outside.unlink(missing_ok=True)
        try:
            code = "import os; from pathlib import Path; Path(os.environ['NEXUS_PROBE_PATH']).write_text('escape')"
            result = self._run(code, env={"NEXUS_PROBE_PATH": str(outside)})
            passed = not outside.exists()
            return self._probe("outside_write_denied", passed, "runtime-derived write outside workspace is denied", result, backend)
        finally:
            outside.unlink(missing_ok=True)

    def _network_denied(self, backend: SandboxBackend) -> SandboxProbeResult:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(2.0)
        port = listener.getsockname()[1]
        accepted = {"value": False}

        def accept_once() -> None:
            try:
                conn, _ = listener.accept()
                accepted["value"] = True
                conn.close()
            except OSError:
                pass

        thread = threading.Thread(target=accept_once, daemon=True)
        thread.start()
        code = (
            "import os,socket; s=socket.socket(); s.settimeout(1); "
            "s.connect(('127.0.0.1', int(os.environ['NEXUS_PROBE_PORT'])))"
        )
        try:
            result = self._run(code, timeout=2.0, network=False, env={"NEXUS_PROBE_PORT": str(port)})
            thread.join(timeout=2.2)
            passed = result.network_enforced and not result.success and not accepted["value"]
            return self._probe("network_denied", passed, "controlled loopback connection is denied when network=False", result, backend)
        finally:
            listener.close()

    def _workspace_tree_hash(self) -> str:
        digest = hashlib.sha256()
        for path in sorted(item for item in self.workspace.rglob("*") if item.is_file()):
            relative = path.relative_to(self.workspace).as_posix()
            if relative.startswith(".git/") or relative.startswith(".nexus/"):
                continue
            digest.update(relative.encode())
            try:
                digest.update(hashlib.sha256(path.read_bytes()).digest())
            except OSError:
                continue
        return digest.hexdigest()

    @staticmethod
    def _probe(name: str, passed: bool, expected: str, result: Any, backend: SandboxBackend) -> SandboxProbeResult:
        observed = (
            f"success={result.success} exit={result.exit_code} timed_out={result.timed_out} "
            f"network_enforced={result.network_enforced}"
        )
        return SandboxProbeResult(name, bool(passed), expected, observed, backend.value, result.to_dict())


def qualify_native_sandbox(
    workspace: str | Path,
    output: str | Path | None = None,
    *,
    source_revision: str = "",
) -> SandboxQualification:
    qualification = NativeSandboxQualifier(workspace, source_revision=source_revision).qualify()
    if output:
        Path(output).write_text(json.dumps(qualification.to_dict(), indent=2, sort_keys=True))
    return qualification
