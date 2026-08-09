"""Reproducible, versioned benchmark runner for Noryx engineering tasks."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import statistics
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nexus import __version__

BENCHMARK_SCHEMA_VERSION = "nexus.benchmark.v2"
SUPPORTED_BENCHMARK_SCHEMAS = {"nexus.benchmark.v1", BENCHMARK_SCHEMA_VERSION}
RESULT_SCHEMA_VERSION = "nexus.benchmark-result.v3"


SUPPORTED_CATEGORIES = {
    "single-file-edit",
    "bug-repair",
    "feature-implementation",
    "multi-file-refactor",
    "dependency-migration",
    "test-generation",
    "repository-debugging",
    "ui-implementation",
    "api-development",
    "security-repair",
    "startup-build",
    "long-horizon-system",
}


@dataclass(frozen=True)
class BenchmarkTask:
    """One isolated repository task and its deterministic acceptance checks."""

    id: str
    category: str
    prompt: str
    repository: str
    verification: tuple[tuple[str, ...], ...]
    timeout_seconds: int = 900
    allowed_paths: tuple[str, ...] = ()
    expected_changed_files: tuple[str, ...] = ()
    required_paths: tuple[str, ...] = ()
    forbidden_content: tuple[str, ...] = ()
    minimum_changed_files: int = 0
    minimum_test_files: int = 0
    max_attempts: int = 1
    max_turns_per_attempt: int = 50
    max_hosted_calls_per_attempt: int | None = None
    max_provider_attempts_per_attempt: int | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any], base: Path) -> "BenchmarkTask":
        task_id = str(value.get("id", "")).strip()
        category = str(value.get("category", "")).strip()
        prompt = str(value.get("prompt", "")).strip()
        repository_value = str(value.get("repository", "")).strip()
        if not task_id or not prompt or not repository_value:
            raise ValueError("Each benchmark task requires id, prompt, and repository")
        repository = Path(repository_value).expanduser()
        if not repository.is_absolute():
            repository = (base / repository).resolve()
        verification = value.get("verification", [])
        if category not in SUPPORTED_CATEGORIES:
            raise ValueError(f"Unsupported benchmark category for {task_id}: {category}")
        if not isinstance(verification, list) or not verification:
            raise ValueError(f"Benchmark task {task_id} requires verification commands")
        commands: list[tuple[str, ...]] = []
        for command in verification:
            if (
                not isinstance(command, list)
                or not command
                or not all(isinstance(item, str) and item for item in command)
            ):
                raise ValueError(f"Benchmark task {task_id} verification must use argv arrays")
            commands.append(tuple(command))
        return cls(
            id=task_id,
            category=category,
            prompt=prompt,
            repository=str(repository),
            verification=tuple(commands),
            timeout_seconds=max(30, int(value.get("timeout_seconds", 900))),
            allowed_paths=tuple(str(item) for item in value.get("allowed_paths", [])),
            expected_changed_files=tuple(
                str(item) for item in value.get("expected_changed_files", [])
            ),
            required_paths=tuple(str(item) for item in value.get("required_paths", [])),
            forbidden_content=tuple(str(item) for item in value.get("forbidden_content", [])),
            minimum_changed_files=max(0, int(value.get("minimum_changed_files", 0))),
            minimum_test_files=max(0, int(value.get("minimum_test_files", 0))),
            max_attempts=min(8, max(1, int(value.get("max_attempts", 1)))),
            max_turns_per_attempt=min(
                500,
                max(1, int(value.get("max_turns_per_attempt", 50))),
            ),
            max_hosted_calls_per_attempt=_optional_positive_int(
                value.get("max_hosted_calls_per_attempt"),
                f"{task_id}.max_hosted_calls_per_attempt",
            ),
            max_provider_attempts_per_attempt=_optional_positive_int(
                value.get("max_provider_attempts_per_attempt"),
                f"{task_id}.max_provider_attempts_per_attempt",
            ),
        )


@dataclass
class BenchmarkTaskResult:
    task_id: str
    category: str
    status: str
    duration_ms: int
    nexus_exit_code: int | None = None
    agent_status: str | None = None
    verification: list[dict[str, Any]] = field(default_factory=list)
    changed_files: list[str] = field(default_factory=list)
    unexpected_files: list[str] = field(default_factory=list)
    model_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    estimated_cost_usd: float | None = None
    retries: int = 0
    human_intervention: bool = False
    recovery_succeeded: bool | None = None
    detail: str = ""
    failure_phase: str = ""
    failure_type: str = ""
    environment_failure: bool = False
    stdout_tail: str = ""
    stderr_tail: str = ""
    external_verification_passed: bool = False
    internal_outcome: str = ""
    tool_calls: int = 0
    tests_executed: int = 0
    criteria_satisfied: int = 0
    criteria_unverified: int = 0
    rollbacks: int = 0
    attempts: int = 0
    resume_count: int = 0
    checkpoints: int = 0
    run_ids: list[str] = field(default_factory=list)
    attempt_reports: list[dict[str, Any]] = field(default_factory=list)
    quality_gates: list[dict[str, Any]] = field(default_factory=list)
    quality_score: float = 0.0
    workspace_artifact: str = ""

    @property
    def passed(self) -> bool:
        return self.status == "PASSED"

    @property
    def manifest_valid(self) -> bool:
        """Whether a dry-run validated the task without executing the agent."""
        return self.status == "VALID"


@dataclass
class BenchmarkReport:
    suite: str
    nexus_version: str
    started_at: str
    completed_at: str
    results: list[BenchmarkTaskResult]
    profile: str = "standard"
    manifest_schema_version: str = BENCHMARK_SCHEMA_VERSION
    schema_version: str = RESULT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        passed = sum(item.passed for item in self.results)
        executed = [item for item in self.results if item.status != "VALID"]
        verified_passed = sum(
            1 for item in executed if item.passed and item.agent_status == "VERIFIED"
        )
        return {
            "schema_version": self.schema_version,
            "suite": self.suite,
            "profile": self.profile,
            "manifest_schema_version": self.manifest_schema_version,
            "nexus_version": self.nexus_version,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "summary": {
                "tasks": len(self.results),
                "passed": passed,
                "verified_passed": verified_passed,
                "manifest_valid_tasks": sum(item.manifest_valid for item in self.results),
                "executed_tasks": len(executed),
                "not_executed_tasks": len(self.results) - len(executed),
                "failed": sum(1 for item in executed if not item.passed),
                "pass_rate": (round(passed / len(executed), 4) if executed else 0.0),
                "verified_pass_rate": (
                    round(verified_passed / len(executed), 4) if executed else 0.0
                ),
                "total_duration_ms": sum(item.duration_ms for item in self.results),
                "total_model_calls": sum(item.model_calls for item in self.results),
                "average_model_calls": round(
                    sum(item.model_calls for item in self.results) / len(self.results), 2
                )
                if self.results
                else 0.0,
                "average_retries": round(
                    sum(item.retries for item in self.results) / len(self.results), 2
                )
                if self.results
                else 0.0,
                "total_attempts": sum(item.attempts for item in self.results),
                "total_resumes": sum(item.resume_count for item in self.results),
                "recovered_tasks": sum(
                    1 for item in self.results if item.recovery_succeeded is True
                ),
                "average_quality_score": round(
                    sum(item.quality_score for item in self.results) / len(self.results),
                    4,
                )
                if self.results
                else 0.0,
                "total_prompt_tokens": sum(item.prompt_tokens for item in self.results),
                "total_completion_tokens": sum(item.completion_tokens for item in self.results),
                "total_cost_usd": round(
                    sum(item.estimated_cost_usd or 0.0 for item in self.results), 8
                ),
                "average_cost_usd": round(
                    sum(item.estimated_cost_usd or 0.0 for item in self.results)
                    / len(self.results),
                    4,
                )
                if self.results
                else 0.0,
                "completion_rate": (round(passed / len(executed), 4) if executed else 0.0),
                "verification_rate": (
                    round(verified_passed / len(executed), 4) if executed else 0.0
                ),
                "human_intervention_rate": (
                    round(sum(1 for item in executed if item.human_intervention) / len(executed), 4)
                    if executed
                    else 0.0
                ),
                "false_success_rate": (
                    round(
                        sum(
                            1
                            for item in executed
                            if item.agent_status == "VERIFIED" and not item.passed
                        )
                        / sum(1 for item in executed if item.agent_status == "VERIFIED"),
                        4,
                    )
                    if sum(1 for item in executed if item.agent_status == "VERIFIED") > 0
                    else 0.0
                ),
                "median_tool_calls": (
                    statistics.median([item.tool_calls for item in executed]) if executed else 0.0
                ),
                "median_cost_usd": (
                    round(
                        statistics.median([(item.estimated_cost_usd or 0.0) for item in executed]),
                        4,
                    )
                    if executed
                    else 0.0
                ),
                "median_duration_ms": (
                    statistics.median([item.duration_ms for item in executed]) if executed else 0.0
                ),
                "failure_categories": {
                    cat: sum(1 for item in executed if item.failure_type == cat)
                    for cat in set(item.failure_type for item in executed if item.failure_type)
                },
            },
            "results": [asdict(item) for item in self.results],
        }


class BenchmarkSuite:
    """Validated benchmark manifest."""

    def __init__(
        self,
        name: str,
        tasks: list[BenchmarkTask],
        source: Path,
        *,
        schema_version: str = BENCHMARK_SCHEMA_VERSION,
        profile: str = "standard",
    ):
        self.name = name
        self.tasks = tasks
        self.source = source
        self.schema_version = schema_version
        self.profile = profile

    @classmethod
    def load(cls, path: str | Path) -> "BenchmarkSuite":
        source = Path(path).expanduser().resolve()
        value = json.loads(source.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("Benchmark manifest must be a JSON object")
        schema_version = str(value.get("schema_version", ""))
        if schema_version not in SUPPORTED_BENCHMARK_SCHEMAS:
            raise ValueError(f"Unsupported benchmark schema: {value.get('schema_version')!r}")
        raw_tasks = value.get("tasks", [])
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise ValueError("Benchmark manifest must contain at least one task")
        tasks = [BenchmarkTask.from_dict(item, source.parent) for item in raw_tasks]
        ids = [task.id for task in tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("Benchmark task ids must be unique")
        profile = str(value.get("profile", "standard")).strip() or "standard"
        return cls(
            str(value.get("name", source.stem)),
            tasks,
            source,
            schema_version=schema_version,
            profile=profile,
        )


class BenchmarkRunner:
    """Run tasks in isolated copies with bounded automatic crash recovery."""

    def __init__(
        self,
        suite: BenchmarkSuite,
        *,
        artifact_root: str | Path | None = None,
        keep_workspaces: bool = False,
    ):
        self.suite = suite
        self.artifact_root = Path(artifact_root).expanduser().resolve() if artifact_root else None
        self.keep_workspaces = bool(keep_workspaces)
        if self.artifact_root:
            self.artifact_root.mkdir(parents=True, exist_ok=True)

    def _preflight(self):
        from nexus.models import resolve_model
        from nexus.preflight import BackendProbe, probe_model

        from nexus.env import noryx_env
        model = noryx_env("MODEL", "nova")
        model_cfg = resolve_model(model)
        if not model_cfg:
            return BackendProbe(
                ready=False,
                backend="configuration",
                code="unknown_model",
                detail=f"Unknown benchmark model: {model}",
                remediation=("Set NEXUS_MODEL to a key shown by `noryx --list-models`.",),
            )
        return probe_model(model_cfg, model_name=model)

    def _preflight_check(self) -> bool:
        """Backward-compatible boolean check used by external callers."""
        return bool(self._preflight().ready)

    @staticmethod
    def _uses_custom_execution_gateway() -> bool:
        """Return True only when an injected gateway owns provider execution.

        The production gateway must respect backend preflight. Deterministic tests and
        embedders may inject a gateway adapter; that adapter becomes the execution
        authority and can exercise the benchmark harness without external credentials.
        """
        from nexus.process_gateway import ProcessExecutionGateway

        module = getattr(ProcessExecutionGateway.run, "__module__", "")
        return module != "nexus.process_gateway"

    def run(self, *, dry_run: bool = False) -> BenchmarkReport:
        started = _utc_now()
        probe = None if dry_run else self._preflight()

        from nexus.env import noryx_env
        strict_preflight = (
            noryx_env("BENCHMARK_STRICT_PREFLIGHT", "0") == "1"
            or not self._uses_custom_execution_gateway()
        )
        if probe is not None and not probe.ready and strict_preflight:
            results = [
                BenchmarkTaskResult(
                    task_id=task.id,
                    category=task.category,
                    status="INVALID_CONFIGURATION",
                    duration_ms=0,
                    detail=probe.format(),
                    failure_phase="provider_preflight",
                    failure_type=probe.code,
                    environment_failure=True,
                )
                for task in self.suite.tasks
            ]
        else:
            # Execute through the production gateway even when local provider
            # preflight is unavailable.  The spawned Noryx process remains the
            # authority for configuration errors, while tests and custom gateway
            # adapters can exercise isolation without requiring real credentials.
            results = [
                self._validate_task(task) if dry_run else self._run_task(task)
                for task in self.suite.tasks
            ]
            if probe is not None and not probe.ready:
                for result in results:
                    if not result.detail:
                        result.detail = f"Provider preflight warning: {probe.detail}"

        return BenchmarkReport(
            suite=self.suite.name,
            nexus_version=__version__,
            started_at=started,
            completed_at=_utc_now(),
            results=results,
            profile=self.suite.profile,
            manifest_schema_version=self.suite.schema_version,
        )

    def _validate_task(self, task: BenchmarkTask) -> BenchmarkTaskResult:
        repository = Path(task.repository)
        available = repository.is_dir()
        return BenchmarkTaskResult(
            task_id=task.id,
            category=task.category,
            status="VALID" if available else "BLOCKED",
            duration_ms=0,
            detail=(
                "Manifest and repository are valid"
                if available
                else f"Repository does not exist: {repository}"
            ),
        )

    def _run_task(self, task: BenchmarkTask) -> BenchmarkTaskResult:
        source = Path(task.repository)
        started = time.monotonic()
        if not source.is_dir():
            return BenchmarkTaskResult(
                task.id,
                task.category,
                "BLOCKED",
                0,
                detail=f"Repository does not exist: {source}",
            )
        temporary_root = Path(
            tempfile.mkdtemp(
                prefix=f"nexus-benchmark-{task.id}-",
                dir=str(self.artifact_root) if self.artifact_root else None,
            )
        )
        workspace = temporary_root / "workspace"
        evidence_dir = temporary_root / "evidence"
        evidence_dir.mkdir()
        state_root = temporary_root / "state"
        try:
            shutil.copytree(
                source,
                workspace,
                symlinks=False,
                ignore=shutil.ignore_patterns(
                    ".git",
                    ".nexusai",
                    ".venv",
                    "venv",
                    "node_modules",
                    "dist",
                    "build",
                    "__pycache__",
                ),
            )
            before = _fingerprints(workspace)
            env = {
                "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
                "NEXUS_HOME": str(state_root),
            }
            allowed_keys = [
                "NEXUS_MODEL",
                "NEXUS_OPENAI_API_KEY",
                "NEXUS_ANTHROPIC_API_KEY",
                "OPENAI_API_KEY",
                "ANTHROPIC_API_KEY",
                "GEMINI_API_KEY",
                "NEXUS_GEMINI_API_KEY",
                "NEXUS_API_KEY",
                "AWS_ACCESS_KEY_ID",
                "AWS_SECRET_ACCESS_KEY",
                "AWS_REGION",
            ]
            for key in allowed_keys:
                if key in os.environ:
                    env[key] = os.environ[key]

            attempts: list[dict[str, Any]] = []
            run_ids: list[str] = []
            resume_from = ""
            final_process: dict[str, Any] = {
                "returncode": None,
                "stdout": "",
                "stderr": "",
                "timed_out": False,
            }
            final_payload: dict[str, Any] = {}

            for attempt_number in range(1, task.max_attempts + 1):
                command = self._agent_command(task, workspace, resume_from=resume_from)
                attempt_started = time.monotonic()
                from nexus.process_gateway import ProcessExecutionGateway, ProcessRequest

                request = ProcessRequest.create(
                    purpose="benchmark_agent",
                    command=command,
                    workspace=Path.cwd(),  # Use trusted cwd to prevent module shadowing
                    trust_level="trusted",
                    timeout_seconds=task.timeout_seconds,
                    env_additions=env,
                    isolation_policy="optional",
                    network_policy="allow",
                    allowed_sensitive_env_keys=allowed_keys,
                )
                result = ProcessExecutionGateway.run(request)

                if result.timed_out:
                    final_process = {
                        "returncode": None,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "timed_out": True,
                    }
                else:
                    final_process = {
                        "returncode": result.exit_code,
                        "stdout": result.stdout,
                        "stderr": result.stderr,
                        "timed_out": False,
                    }

                final_payload = _last_json_object(final_process["stdout"])
                run_id = _run_id_from_payload(final_payload) or self._discover_run_id(
                    state_root,
                    workspace,
                )
                if run_id and run_id not in run_ids:
                    run_ids.append(run_id)
                run_report = final_payload.get("run", {}) if isinstance(final_payload, dict) else {}
                status = str(run_report.get("status", ""))
                attempt_record = {
                    "attempt": attempt_number,
                    "kind": "resume" if resume_from else "initial",
                    "run_id": run_id,
                    "returncode": final_process["returncode"],
                    "timed_out": final_process["timed_out"],
                    "agent_status": status or "unknown",
                    "duration_ms": int((time.monotonic() - attempt_started) * 1000),
                    "stdout_tail": _redact_tail(final_process["stdout"]),
                    "stderr_tail": _redact_tail(final_process["stderr"]),
                }
                attempts.append(attempt_record)
                _atomic_write_json(
                    evidence_dir / f"attempt-{attempt_number:02d}.json",
                    {
                        **attempt_record,
                        "command": command,
                        "stdout": _redact_text(final_process["stdout"]),
                        "stderr": _redact_text(final_process["stderr"]),
                    },
                )

                if final_process["returncode"] == 0 and status == "VERIFIED":
                    break
                if attempt_number >= task.max_attempts or not run_id:
                    break
                if status in {"AWAITING_APPROVAL", "BLOCKED", "ROLLED_BACK", "VERIFIED"}:
                    break
                resume_from = run_id

            after = _fingerprints(workspace)
            changed = sorted(
                path for path in set(before) | set(after) if before.get(path) != after.get(path)
            )
            unexpected = [
                path
                for path in changed
                if task.allowed_paths and not _matches_any(path, task.allowed_paths)
            ]
            missing_expected = [path for path in task.expected_changed_files if path not in changed]
            checks = []
            from nexus.process_gateway import ProcessExecutionGateway, ProcessRequest

            for argv in task.verification:
                req = ProcessRequest.create(
                    purpose="benchmark_verification",
                    command=argv,
                    workspace=workspace,
                    timeout_seconds=min(task.timeout_seconds, 300),
                    network_policy="deny",
                    isolation_policy="required",
                )
                result = ProcessExecutionGateway.run(req)
                checks.append(result.to_dict())
            run_report = final_payload.get("run", {}) if isinstance(final_payload, dict) else {}
            quality_gates = _quality_gates(
                task,
                workspace,
                changed,
                checks,
                unexpected,
                missing_expected,
            )
            external_passed = all(item["passed"] for item in quality_gates)
            passed = (
                final_process["returncode"] == 0
                and external_passed
                and run_report.get("status") == "VERIFIED"
            )
            details = []
            if final_process["timed_out"]:
                details.append(f"final Noryx attempt timed out after {task.timeout_seconds}s")
            elif final_process["returncode"]:
                details.append(f"Noryx exit code {final_process['returncode']}")
            failed_gates = [item["name"] for item in quality_gates if not item["passed"]]
            if failed_gates:
                details.append(f"failed quality gates: {failed_gates}")
            if run_report.get("status") != "VERIFIED":
                details.append(f"run status: {run_report.get('status', 'missing')}")
            if final_process["timed_out"]:
                failure_phase, failure_type, environment_failure = (
                    "agent_execution",
                    "timeout",
                    False,
                )
            else:
                failure_phase, failure_type, environment_failure = _classify_process_failure(
                    int(final_process["returncode"] or 0),
                    final_process["stdout"],
                    final_process["stderr"],
                    run_report,
                )
            aggregate = _aggregate_attempt_usage(attempts, state_root, run_ids)
            checkpoint_count = len(list(state_root.glob("runs/*/turn-*/checkpoints/*.json")))
            return BenchmarkTaskResult(
                task_id=task.id,
                category=task.category,
                status="PASSED" if passed else "FAILED",
                duration_ms=int((time.monotonic() - started) * 1000),
                nexus_exit_code=final_process["returncode"],
                agent_status=run_report.get("status", "unknown"),
                verification=checks,
                changed_files=changed,
                unexpected_files=unexpected,
                model_calls=aggregate["model_calls"],
                prompt_tokens=aggregate["prompt_tokens"],
                completion_tokens=aggregate["completion_tokens"],
                estimated_cost_usd=aggregate["estimated_cost_usd"],
                retries=aggregate["retries"],
                human_intervention=run_report.get("status") == "AWAITING_APPROVAL",
                recovery_succeeded=(passed if len(attempts) > 1 else None),
                detail="; ".join(details) if details else "All acceptance checks passed",
                failure_phase="" if passed else failure_phase,
                failure_type="" if passed else failure_type,
                environment_failure=False if passed else environment_failure,
                stdout_tail=_redact_tail(final_process["stdout"]),
                stderr_tail=_redact_tail(final_process["stderr"]),
                external_verification_passed=external_passed,
                internal_outcome=str(run_report.get("outcome", "")),
                tool_calls=aggregate["tool_calls"],
                tests_executed=aggregate["tests_executed"],
                criteria_satisfied=int(
                    run_report.get("metadata", {}).get("criteria_satisfied", 0) or 0
                ),
                criteria_unverified=int(
                    run_report.get("metadata", {}).get("criteria_unverified", 0) or 0
                ),
                rollbacks=aggregate["rollbacks"],
                attempts=len(attempts),
                resume_count=max(0, len(attempts) - 1),
                checkpoints=checkpoint_count,
                run_ids=run_ids,
                attempt_reports=attempts,
                quality_gates=quality_gates,
                quality_score=round(
                    sum(item["passed"] for item in quality_gates) / len(quality_gates),
                    4,
                )
                if quality_gates
                else 0.0,
                workspace_artifact=str(workspace) if self.keep_workspaces else "",
            )
        finally:
            if not self.keep_workspaces:
                if self.artifact_root:
                    shutil.rmtree(workspace, ignore_errors=True)
                    shutil.rmtree(state_root, ignore_errors=True)
                else:
                    shutil.rmtree(temporary_root, ignore_errors=True)

    @staticmethod
    def _agent_command(
        task: BenchmarkTask,
        workspace: Path,
        *,
        resume_from: str = "",
    ) -> list[str]:
        command = [sys.executable, "-m", "nexus"]
        if resume_from:
            command.extend(["--resume-run", resume_from])
        else:
            command.extend(["run", "--prompt", task.prompt])
        command.extend(
            [
                "--mode",
                "ci",
                "--working-dir",
                str(workspace),
                "--no-workspace",
                "--output-format",
                "json",
                "--max-turns",
                str(task.max_turns_per_attempt),
            ]
        )
        if task.max_hosted_calls_per_attempt is not None:
            command.extend(["--max-hosted-calls", str(task.max_hosted_calls_per_attempt)])
        if task.max_provider_attempts_per_attempt is not None:
            command.extend(
                [
                    "--max-provider-attempts",
                    str(task.max_provider_attempts_per_attempt),
                ]
            )
        return command

    @staticmethod
    def _discover_run_id(state_root: Path, workspace: Path) -> str:
        from nexus.run_catalog import RunCatalog

        candidates = RunCatalog(root=state_root).list(working_dir=workspace, limit=1)
        if not candidates:
            return ""
        return f"{candidates[0].session_id}/{candidates[0].turn_id}"


def _optional_positive_int(value: Any, field_name: str) -> int | None:
    if value is None:
        return None
    parsed = int(value)
    if parsed < 1:
        raise ValueError(f"{field_name} must be a positive integer")
    return parsed


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _run_id_from_payload(payload: dict[str, Any]) -> str:
    session_id = str(payload.get("session_id", "")).strip()
    report = payload.get("run", {})
    turn_id = str(report.get("turn_id", "")).strip() if isinstance(report, dict) else ""
    return f"{session_id}/{turn_id}" if session_id and turn_id else ""


def _quality_gates(
    task: BenchmarkTask,
    workspace: Path,
    changed: list[str],
    checks: list[dict[str, Any]],
    unexpected: list[str],
    missing_expected: list[str],
) -> list[dict[str, Any]]:
    def gate(name: str, passed: bool, detail: str) -> dict[str, Any]:
        return {"name": name, "passed": bool(passed), "detail": detail}

    gates = [
        gate(
            "deterministic_verification",
            bool(checks) and all(item.get("success") for item in checks),
            f"{sum(bool(item.get('success')) for item in checks)}/{len(checks)} checks passed",
        ),
        gate(
            "allowed_change_scope",
            not unexpected,
            "No unexpected files changed" if not unexpected else f"Unexpected files: {unexpected}",
        ),
        gate(
            "expected_mutations",
            not missing_expected,
            "Expected files changed"
            if not missing_expected
            else f"Expected files unchanged: {missing_expected}",
        ),
    ]
    if task.required_paths:
        missing_required = [
            pattern for pattern in task.required_paths if not any(workspace.glob(pattern))
        ]
        gates.append(
            gate(
                "required_artifacts",
                not missing_required,
                "All required artifact patterns exist"
                if not missing_required
                else f"Missing required patterns: {missing_required}",
            )
        )
    if task.minimum_changed_files:
        gates.append(
            gate(
                "minimum_change_surface",
                len(changed) >= task.minimum_changed_files,
                f"{len(changed)} changed files; minimum {task.minimum_changed_files}",
            )
        )
    if task.minimum_test_files:
        test_files = _test_files(workspace)
        gates.append(
            gate(
                "test_suite_depth",
                len(test_files) >= task.minimum_test_files,
                f"{len(test_files)} test files; minimum {task.minimum_test_files}",
            )
        )
    if task.forbidden_content:
        findings = _forbidden_content_findings(workspace, changed, task.forbidden_content)
        gates.append(
            gate(
                "no_placeholder_implementation",
                not findings,
                "No forbidden placeholder content found"
                if not findings
                else f"Forbidden content found: {findings[:20]}",
            )
        )
    return gates


def _test_files(workspace: Path) -> list[str]:
    matches = []
    for path in workspace.rglob("*"):
        if not path.is_file():
            continue
        name = path.name.lower()
        if name.startswith("test_") or name.endswith(
            ("_test.py", ".test.js", ".test.ts", ".spec.js", ".spec.ts")
        ):
            matches.append(path.relative_to(workspace).as_posix())
    return sorted(matches)


def _forbidden_content_findings(
    workspace: Path,
    changed: list[str],
    forbidden: tuple[str, ...],
) -> list[str]:
    findings = []
    for relative in changed:
        path = workspace / relative
        if not path.is_file() or path.stat().st_size > 1_000_000:
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for needle in forbidden:
            if needle and needle in content:
                findings.append(f"{relative}: {needle}")
    return findings


def _aggregate_attempt_usage(
    attempts: list[dict[str, Any]],
    state_root: Path,
    run_ids: list[str],
) -> dict[str, Any]:
    from nexus.run_catalog import RunCatalog

    totals: dict[str, Any] = {
        "model_calls": 0,
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "estimated_cost_usd": None,
        "retries": 0,
        "tool_calls": 0,
        "tests_executed": 0,
        "rollbacks": 0,
    }
    cost_seen = False
    catalog = RunCatalog(root=state_root)
    for run_id in run_ids:
        try:
            inspected = catalog.inspect(run_id)
        except FileNotFoundError:
            continue
        report = inspected.get("final_report", {})
        provider_metrics = report.get("provider_metrics", {})
        metadata = report.get("metadata", {}) if isinstance(report, dict) else {}

        totals["model_calls"] += int(
            metadata.get("model_calls", provider_metrics.get("hosted_calls", 0)) or 0
        )
        totals["prompt_tokens"] += int(provider_metrics.get("prompt_tokens", 0) or 0)
        totals["completion_tokens"] += int(provider_metrics.get("completion_tokens", 0) or 0)
        if provider_metrics.get("cost_usd") is not None:
            cost_seen = True
            totals["estimated_cost_usd"] = float(totals["estimated_cost_usd"] or 0.0) + float(
                provider_metrics["cost_usd"]
            )
        for key in ("retries", "tool_calls", "tests_executed", "rollbacks"):
            totals[key] += int(metadata.get(key, 0) or 0)
    if not cost_seen:
        totals["estimated_cost_usd"] = None
    totals["attempts"] = len(attempts)
    return totals


def _atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        temporary.write_text(
            json.dumps(value, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _fingerprints(root: Path) -> dict[str, str]:
    ignored = {".git", ".nexusai", "__pycache__", ".pytest_cache", ".ruff_cache"}
    result = {}
    for path in root.rglob("*"):
        if not path.is_file() or any(part in ignored for part in path.relative_to(root).parts):
            continue
        try:
            result[path.relative_to(root).as_posix()] = hashlib.sha256(
                path.read_bytes()
            ).hexdigest()
        except OSError:
            continue
    return result


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    from fnmatch import fnmatch

    return any(fnmatch(path, pattern) for pattern in patterns)


def _last_json_object(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    return {}


def _redact_tail(value: Any, limit: int = 4000) -> str:
    return _redact_text(value, limit=limit)[-max(0, int(limit)) :]


def _redact_text(value: Any, limit: int = 200_000) -> str:
    text = _as_text(value)
    for name in (
        "NVIDIA_API_KEY",
        "GROQ_API_KEY",
        "OPENROUTER_API_KEY",
        "NEXUS_OPENAI_API_KEY",
    ):
        secret = os.environ.get(name)
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text[-max(0, int(limit)) :]


def _classify_process_failure(
    exit_code: int,
    stdout: str,
    stderr: str,
    run_report: dict[str, Any],
) -> tuple[str, str, bool]:
    if exit_code == 0 and run_report.get("status") == "VERIFIED":
        return "", "", False
    haystack = (str(stdout) + "\n" + str(stderr)).lower()
    environment_signatures = {
        "ollama_unreachable": ("ollama", "connection refused"),
        "dependency_missing": ("modulenotfounderror",),
        "credentials_missing": ("api key", "missing"),
        "provider_unreachable": ("provider", "connection"),
    }
    for failure_type, signatures in environment_signatures.items():
        if all(signature in haystack for signature in signatures):
            return "environment", failure_type, True
    if not run_report:
        return "result_parsing", "missing_structured_result", True
    status = str(run_report.get("status", "")).lower()
    if status in {"awaiting_approval", "blocked"}:
        return "policy", status, False
    if "verification" in haystack or status in {"unverified", "partially_verified"}:
        return "verification", "acceptance_checks_failed", False
    return "agent_execution", "nonzero_exit", False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
