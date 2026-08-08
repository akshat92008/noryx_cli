"""Executable offline reliability benchmark for the installed Nexus runtime.

This suite proves deterministic orchestration and truth-integrity behavior without
claiming model intelligence.  It performs a real repository repair through the
canonical Agent pipeline and executes adversarial policy scenarios against fresh
workspaces.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

from nexus import __version__
from nexus.agent import Agent
from nexus.intelligence.engineering import (
    EngineeringBrain,
    ScopeEvidenceType,
    ScopeExpansionEvidence,
    SemanticVerifier,
)
from nexus.intelligence.repository.snapshot import workspace_revision
from nexus.policy import get_mode_policy
from nexus.providers.base import Provider


SCHEMA_VERSION = "nexus.offline-reliability.v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class ScenarioResult:
    id: str
    category: str
    passed: bool
    duration_ms: int
    detail: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class OfflineReliabilityReport:
    started_at: str
    completed_at: str
    nexus_version: str
    scenarios: list[ScenarioResult]
    schema_version: str = SCHEMA_VERSION
    profile: str = "deterministic-offline-orchestration"

    def to_dict(self) -> dict[str, Any]:
        passed = sum(item.passed for item in self.scenarios)
        return {
            "schema_version": self.schema_version,
            "profile": self.profile,
            "nexus_version": self.nexus_version,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "summary": {
                "executed_scenarios": len(self.scenarios),
                "passed": passed,
                "failed": len(self.scenarios) - passed,
                "pass_rate": round(passed / len(self.scenarios), 4)
                if self.scenarios
                else 0.0,
                "real_repository_repairs": sum(
                    1
                    for item in self.scenarios
                    if item.category == "repository-repair" and item.passed
                ),
                "model_calls": 0,
                "intelligence_claim": "none",
            },
            "scenarios": [asdict(item) for item in self.scenarios],
        }

    def write(self, path: str | Path) -> Path:
        target = Path(path).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return target


class _ToolCall:
    def __init__(self, name: str, arguments: dict[str, Any], index: int = 0):
        self.id = f"offline-{index}-{name}"
        self.index = index
        self.function = SimpleNamespace(name=name, arguments=json.dumps(arguments))


class _ScriptedProvider(Provider):
    """Deterministic provider used only to exercise the canonical runtime."""

    def __init__(self, responses: list[dict[str, Any]]):
        self.responses = responses
        self.calls = 0

    @property
    def id(self) -> str:
        return "offline-reliability-script"

    @property
    def name(self) -> str:
        return "Offline Reliability Script"

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    @staticmethod
    def _stream_chunk(response: dict[str, Any]):
        delta = SimpleNamespace(
            content=response.get("content", ""),
            tool_calls=response.get("tool_calls", []),
        )
        return SimpleNamespace(
            id="offline-request",
            choices=[SimpleNamespace(delta=delta)],
            usage=SimpleNamespace(prompt_tokens=0, completion_tokens=0, total_tokens=0),
        )

    def chat(self, *, stream: bool = False, **_kwargs):
        response = (
            self.responses[self.calls]
            if self.calls < len(self.responses)
            else {"content": "Offline scripted execution complete."}
        )
        self.calls += 1
        if stream:
            return iter([self._stream_chunk(response)])
        message = SimpleNamespace(
            content=response.get("content", ""),
            tool_calls=response.get("tool_calls", []),
        )
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    def chat_sync(self, *, tools=None, **kwargs):
        if tools is None:
            message = SimpleNamespace(
                content=json.dumps(
                    {
                        "approved": True,
                        "summary": "Pre-existing acceptance tests prove the bounded repair.",
                        "findings": [],
                    }
                ),
                tool_calls=[],
            )
            return SimpleNamespace(choices=[SimpleNamespace(message=message)])
        return self.chat(stream=False, tools=tools, **kwargs)


@contextmanager
def _temporary_environment(values: dict[str, str]) -> Iterator[None]:
    original = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "config", "user.email", "offline-benchmark@nexus.local"],
        cwd=root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Nexus Offline Benchmark"],
        cwd=root,
        check=True,
    )
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "fixture"], cwd=root, check=True)


def _scenario_repository_repair(base: Path) -> ScenarioResult:
    started = time.monotonic()
    root = base / "calculator-repair"
    root.mkdir()
    (root / "calculator.py").write_text(
        "def add(a, b):\n    return a + b\n\n"
        "def multiply(a, b):\n    return a + b\n",
        encoding="utf-8",
    )
    (root / "test_calculator.py").write_text(
        "from calculator import add, multiply\n\n"
        "def test_add():\n    assert add(2, 3) == 5\n\n"
        "def test_multiply():\n    assert multiply(2, 3) == 6\n",
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        "[project]\nname='nexus-offline-calculator'\nversion='0.0.0'\n"
        "[tool.pytest.ini_options]\ntestpaths=['.']\n",
        encoding="utf-8",
    )
    _git_init(root)

    test_argv = [sys.executable, "-m", "pytest", "-q"]
    responses = [
        {"tool_calls": [_ToolCall("read_file", {"path": "calculator.py"}, 0)]},
        {
            "tool_calls": [
                _ToolCall(
                    "run_process",
                    {"argv": test_argv, "cwd": "."},
                    1,
                )
            ]
        },
        {
            "tool_calls": [
                _ToolCall(
                    "edit_file",
                    {
                        "path": "calculator.py",
                        "old_text": "def multiply(a, b):\n    return a + b\n",
                        "new_text": "def multiply(a, b):\n    return a * b\n",
                    },
                    2,
                )
            ]
        },
        {
            "tool_calls": [
                _ToolCall(
                    "run_process",
                    {"argv": test_argv, "cwd": "."},
                    3,
                )
            ]
        },
        {"content": "The multiplication regression is repaired and acceptance tests pass."},
    ]
    policy = get_mode_policy("autonomous")
    policy.require_os_isolation = False
    policy.allow_shell_command = False
    state = base / "state-repair"
    agent: Agent | None = None
    try:
        with _temporary_environment(
            {
                "NEXUS_HOME": str(state),
                "NEXUS_DISABLE_NETWORK": "1",
                "NEXUS_OFFLINE": "1",
            }
        ):
            agent = Agent(
                api_key="offline-benchmark",
                working_dir=str(root),
                mode_policy=policy,
                permission_mode="acceptEdits",
                workspace_isolation=False,
                allow_unisolated_host_process=True,
                max_turns=10,
            )
            agent._should_use_two_node = lambda _analysis: False
            agent.client = _ScriptedProvider(responses)
            content, events = agent.run_non_interactive(
                "Fix calculator.py multiplication without changing add behavior or adding dependencies. "
                "Use the pre-existing tests as acceptance evidence."
            )
            report = agent.export_final_report()
            external = subprocess.run(
                test_argv,
                cwd=root,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            changed = subprocess.run(
                ["git", "diff", "--name-only"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            ).stdout.splitlines()
            passed = (
                report.get("status") == "VERIFIED"
                and external.returncode == 0
                and changed == ["calculator.py"]
                and "return a * b" in (root / "calculator.py").read_text(encoding="utf-8")
            )
            return ScenarioResult(
                id="offline-calculator-regression",
                category="repository-repair",
                passed=passed,
                duration_ms=int((time.monotonic() - started) * 1000),
                detail=("verified bounded repair" if passed else f"status={report.get('status')}"),
                evidence={
                    "run_status": report.get("status"),
                    "changed_files": changed,
                    "external_test_exit_code": external.returncode,
                    "tool_calls": sum(1 for event in events if event.get("type") == "tool_call"),
                    "response": content[:300],
                },
            )
    except Exception as exc:  # benchmark must report, not crash the command
        return ScenarioResult(
            id="offline-calculator-regression",
            category="repository-repair",
            passed=False,
            duration_ms=int((time.monotonic() - started) * 1000),
            detail=f"{exc.__class__.__name__}: {exc}",
        )
    finally:
        if agent is not None:
            agent.close(discard_workspace=True)


def _fixture_repository(root: Path) -> None:
    root.mkdir()
    (root / "calculator.py").write_text(
        "def multiply(a, b):\n    return a + b\n", encoding="utf-8"
    )
    (root / "verify.py").write_text(
        "from calculator import multiply\nassert multiply(2, 3) == 6\n", encoding="utf-8"
    )
    (root / "helper.py").write_text("VALUE = 1\n", encoding="utf-8")


def _scenario_prohibition(base: Path) -> ScenarioResult:
    started = time.monotonic()
    root = base / "prohibition"
    _fixture_repository(root)
    brain = EngineeringBrain(root)
    brain.prepare(
        "Fix calculator.py multiplication without changing verify.py",
        task_id="offline-prohibition",
        strict=True,
    )
    decision = brain.authorize_mutation(["verify.py"])
    passed = not decision.allowed
    return ScenarioResult(
        "typed-prohibition",
        "policy-adversarial",
        passed,
        int((time.monotonic() - started) * 1000),
        decision.reason,
    )


def _scenario_prose_evidence(base: Path) -> ScenarioResult:
    started = time.monotonic()
    del base
    result = SemanticVerifier().verify(
        objective="Users can reset passwords",
        task_type="feature_implementation",
        evidence=[
            {"id": "m1", "kind": "file_mutation", "status": "verified"},
            {
                "id": "v1",
                "kind": "verification_check",
                "status": "verified",
                "tool": "pytest",
                "command": "pytest -q",
                "exit_code": 0,
                "raw_output": "Users can reset passwords",
                "metadata": {
                    "check_type": "unrelated",
                    "producer_type": "deterministic_tool",
                    "independently_validated": True,
                },
            },
            {"id": "r1", "kind": "independent_review", "status": "verified"},
        ],
        changed_files=["auth.py"],
        allowed_files=["auth.py"],
        prohibited_patterns=[],
        acceptance_criteria=["Users can reset passwords"],
    )
    passed = not result.satisfied and result.requirement_results.get(
        "Users can reset passwords"
    ) == "UNVERIFIED"
    return ScenarioResult(
        "model-prose-is-not-proof",
        "verification-adversarial",
        passed,
        int((time.monotonic() - started) * 1000),
        result.status,
        {"findings": [item.code for item in result.findings]},
    )


def _scenario_fabricated_scope(base: Path) -> ScenarioResult:
    started = time.monotonic()
    root = base / "fabricated-scope"
    _fixture_repository(root)
    brain = EngineeringBrain(root)
    brain.prepare("Fix calculator.py", task_id="offline-scope", strict=True)
    revision = workspace_revision(root)
    fabricated = ScopeExpansionEvidence(
        evidence_type=ScopeEvidenceType.IMPORT_EDGE,
        target_path="helper.py",
        source_path="calculator.py",
        evidence_id=f"repo:{revision}:import:calculator.py->helper.py",
        source_revision=revision,
        details="model-authored unsupported claim",
    )
    decision = brain.authorize_mutation(
        ["helper.py"], expansion_evidence=[fabricated]
    )
    return ScenarioResult(
        "fabricated-scope-evidence",
        "policy-adversarial",
        not decision.allowed,
        int((time.monotonic() - started) * 1000),
        decision.reason,
    )


def _scenario_concurrent_edit(base: Path) -> ScenarioResult:
    started = time.monotonic()
    root = base / "concurrent-edit"
    _fixture_repository(root)
    brain = EngineeringBrain(root)
    brain.prepare("Fix calculator.py", task_id="offline-concurrency", strict=True)
    (root / "calculator.py").write_text(
        "def multiply(a, b):\n    return a * b + 1\n", encoding="utf-8"
    )
    decision = brain.authorize_mutation(["calculator.py"])
    return ScenarioResult(
        "optimistic-concurrency",
        "concurrency-adversarial",
        not decision.allowed and "concurrent modification" in decision.reason.lower(),
        int((time.monotonic() - started) * 1000),
        decision.reason,
    )


def run_offline_reliability_benchmark(
    *, artifact_root: str | Path | None = None
) -> OfflineReliabilityReport:
    """Execute fresh deterministic scenarios and return a machine-readable report."""
    started = _utc_now()
    if artifact_root:
        base = Path(artifact_root).expanduser().resolve()
        base.mkdir(parents=True, exist_ok=True)
        temporary = None
    else:
        temporary = tempfile.TemporaryDirectory(prefix="nexus-offline-reliability-")
        base = Path(temporary.name)
    try:
        scenarios = [
            _scenario_repository_repair(base),
            _scenario_prohibition(base),
            _scenario_prose_evidence(base),
            _scenario_fabricated_scope(base),
            _scenario_concurrent_edit(base),
        ]
        return OfflineReliabilityReport(
            started_at=started,
            completed_at=_utc_now(),
            nexus_version=__version__,
            scenarios=scenarios,
        )
    finally:
        if temporary is not None:
            temporary.cleanup()
