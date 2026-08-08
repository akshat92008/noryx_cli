"""Blind, reproducible head-to-head coding-agent benchmark harness.

The harness does not claim parity.  It creates matched disposable repositories,
withholds oracle material during agent execution, randomizes invocation order,
and scores both systems through the same deterministic verifier.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class AgentInvocation:
    name: str
    argv: tuple[str, ...]
    version_argv: tuple[str, ...] = ()
    success_markers: tuple[str, ...] = ("COMPLETED", "VERIFIED", "SUCCESS")
    metrics_file: str = ""
    product_identity: str = ""
    model_identity: str = ""


@dataclass
class AgentRunResult:
    agent: str
    task_id: str
    available: bool
    completed: bool
    claimed_success: bool
    verified: bool
    false_success: bool
    exit_code: int | None
    duration_ms: int
    changed_files: list[str] = field(default_factory=list)
    unexpected_files: list[str] = field(default_factory=list)
    verification: list[dict[str, Any]] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    provenance: dict[str, Any] = field(default_factory=dict)
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    human_interventions: int | None = None


@dataclass
class DuelReport:
    schema_version: str
    manifest_sha256: str
    seed: int
    dry_run: bool
    generated_at: str
    task_results: list[dict[str, Any]]
    summary: dict[str, Any]
    qualification: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class _CapturedProcess:
    returncode: int | None
    stdout: str
    stderr: str
    timed_out: bool
    descendants_reaped: bool


def _read_capped(stream: Any, *, limit: int = 1_000_000) -> str:
    """Read bounded UTF-8 evidence without keeping unbounded child output in RAM."""
    stream.flush()
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    if size <= limit:
        stream.seek(0)
        raw = stream.read()
    else:
        head = max(1, limit // 4)
        tail = max(1, limit - head)
        stream.seek(0)
        prefix = stream.read(head)
        stream.seek(max(0, size - tail))
        suffix = stream.read(tail)
        raw = prefix + b"\n...[NEXUS OUTPUT TRUNCATED]...\n" + suffix
    if isinstance(raw, str):
        return raw
    return raw.decode("utf-8", errors="replace")


def _posix_group_exists(pgid: int) -> bool:
    if os.name != "posix":
        return False
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _reap_process_tree(process: subprocess.Popen[bytes], *, grace: float = 0.75) -> bool:
    """Best-effort cleanup of every descendant created by a benchmark command.

    Each benchmark command gets a fresh session/process group.  Cleanup runs even
    after a successful parent exit because coding agents and test runners can leave
    background helpers behind that would otherwise contaminate later trials.
    """
    descendants_reaped = False
    if os.name == "posix":
        pgid = process.pid
        if not _posix_group_exists(pgid):
            return False
        descendants_reaped = True
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            return descendants_reaped
        deadline = time.monotonic() + max(0.05, grace)
        while time.monotonic() < deadline and _posix_group_exists(pgid):
            time.sleep(0.02)
        if _posix_group_exists(pgid):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if process.poll() is None:
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
        return descendants_reaped

    # Windows has no os.killpg equivalent. taskkill /T is the strongest stdlib-
    # compatible tree cleanup available without adding a platform dependency.
    if os.name == "nt":  # pragma: no cover - exercised in Windows CI
        descendants_reaped = True
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(process.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            if process.poll() is None:
                process.kill()
        try:
            process.wait(timeout=1.0)
        except subprocess.TimeoutExpired:
            pass
    return descendants_reaped


def _run_captured_process(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    timeout: float,
    env: Mapping[str, str] | None = None,
) -> _CapturedProcess:
    """Run one command with bounded evidence and deterministic tree cleanup."""
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        kwargs: dict[str, Any] = {
            "cwd": cwd,
            "stdout": stdout_file,
            "stderr": stderr_file,
            "env": None if env is None else dict(env),
        }
        if os.name == "posix":
            kwargs["start_new_session"] = True
        elif os.name == "nt":  # pragma: no cover - exercised in Windows CI
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        try:
            process = subprocess.Popen(list(argv), **kwargs)
        except OSError as exc:
            return _CapturedProcess(None, "", str(exc), False, False)

        timed_out = False
        try:
            process.wait(timeout=max(0.01, float(timeout)))
        except subprocess.TimeoutExpired:
            timed_out = True
        descendants_reaped = _reap_process_tree(process)
        if process.poll() is None:
            # Defensive final kill if platform tree cleanup failed to reap parent.
            try:
                process.kill()
                process.wait(timeout=1.0)
            except (OSError, subprocess.TimeoutExpired):
                pass
        stdout = _read_capped(stdout_file)
        stderr = _read_capped(stderr_file)
        if timed_out:
            stderr = (stderr + "\nTIMEOUT").strip()
        return _CapturedProcess(
            process.returncode, stdout, stderr, timed_out, descendants_reaped
        )


class CompetitiveDuelRunner:
    def __init__(self, manifest: str | Path | Mapping[str, Any], *, seed: int = 370):
        if isinstance(manifest, Mapping):
            self.manifest = dict(manifest)
            raw = json.dumps(self.manifest, sort_keys=True).encode()
            self.manifest_path = None
        else:
            self.manifest_path = Path(manifest).expanduser().resolve()
            raw = self.manifest_path.read_bytes()
            self.manifest = json.loads(raw)
        self.manifest_sha256 = hashlib.sha256(raw).hexdigest()
        self.seed = int(seed)
        self._validate_manifest()

    def superiority_preflight(self, *, thresholds: Any = None) -> dict[str, Any]:
        """Validate a private superiority campaign before paid agent execution.

        This does not qualify Nexus. It prevents structurally invalid, duplicated,
        placeholder, or underpowered campaigns from consuming model budget.
        """
        from collections import Counter

        from nexus.competitive_qualification import (
            REQUIRED_HARD_TASK_CATEGORIES,
            SuperiorityThresholds,
        )

        limits = thresholds or SuperiorityThresholds()
        failures: list[str] = []
        required_agents = {
            limits.nexus_agent,
            limits.direct_baseline_agent,
            limits.claude_agent,
        }
        declared_agents = set(self.manifest.get("agents") or {})
        if declared_agents != required_agents:
            failures.append(
                "agents_must_be_exactly:" + ",".join(sorted(required_agents))
            )

        def placeholder(value: Any) -> bool:
            text = json.dumps(value, sort_keys=True).lower()
            return any(
                marker in text
                for marker in ("replace-with", "placeholder", "unsigned-until")
            )

        agent_data = self.manifest.get("agents") or {}
        identities: dict[str, tuple[str, str]] = {}
        for name in sorted(required_agents):
            data = agent_data.get(name) or {}
            product = str(data.get("product_identity", "")).strip()
            model = str(data.get("model_identity", "")).strip()
            identities[name] = (product, model)
            if not product or placeholder(product):
                failures.append(f"agent_product_identity_invalid:{name}")
            if not model or placeholder(model):
                failures.append(f"agent_model_identity_invalid:{name}")
            if not data.get("version_argv"):
                failures.append(f"agent_version_command_missing:{name}")
        if (
            limits.nexus_agent in identities
            and limits.direct_baseline_agent in identities
            and identities[limits.nexus_agent][1]
            != identities[limits.direct_baseline_agent][1]
        ):
            failures.append("direct_baseline_model_mismatch")
        products = {value[0] for value in identities.values() if value[0]}
        if len(products) != len(required_agents):
            failures.append("agent_product_identities_not_distinct")

        qualification = self.manifest.get("qualification") or {}
        for key in (
            "blind",
            "oracle_withheld",
            "private_unseen_tasks",
            "independent_evaluator",
            "equal_budget_policy",
            "task_selection_frozen_before_run",
        ):
            if qualification.get(key) is not True:
                failures.append(f"qualification_flag_missing:{key}")
        for key in ("campaign_id", "dataset_revision", "evaluator_id"):
            value = str(qualification.get(key, "")).strip()
            if not value or placeholder(value):
                failures.append(f"qualification_identity_invalid:{key}")

        trials = max(1, int(self.manifest.get("trials", 1)))
        if trials < limits.minimum_trials_per_task:
            failures.append(
                f"trials:{trials}<{limits.minimum_trials_per_task}"
            )
        tasks = self.manifest.get("tasks") or []
        if len(tasks) < limits.minimum_unique_tasks:
            failures.append(
                f"unique_tasks:{len(tasks)}<{limits.minimum_unique_tasks}"
            )
        categories = Counter(str(task.get("category", "")) for task in tasks)
        required_categories = set(
            limits.required_categories or tuple(sorted(REQUIRED_HARD_TASK_CATEGORIES))
        )
        for category in sorted(required_categories):
            if categories[category] < limits.minimum_tasks_per_category:
                failures.append(
                    f"category_tasks:{category}:"
                    f"{categories[category]}<{limits.minimum_tasks_per_category}"
                )

        repository_hashes: set[str] = set()
        fingerprints: set[tuple[str, str]] = set()
        for task in tasks:
            task_id = str(task.get("id", "")).strip()
            source = self._resolve(str(task.get("repository", "")))
            oracle_name = str(task.get("oracle_dir", ".oracle"))
            oracle = source / oracle_name
            if not oracle.is_dir() or not any(path.is_file() for path in oracle.rglob("*")):
                failures.append(f"oracle_missing_or_empty:{task_id}")
            snapshot = self._snapshot(source, excluded_paths=(oracle_name,))
            repository_hash = hashlib.sha256(
                json.dumps(snapshot, sort_keys=True).encode("utf-8")
            ).hexdigest()
            prompt_hash = hashlib.sha256(
                str(task.get("prompt", "")).encode("utf-8")
            ).hexdigest()
            repository_hashes.add(repository_hash)
            fingerprint = (repository_hash, prompt_hash)
            if fingerprint in fingerprints:
                failures.append(f"duplicate_task_content:{task_id}")
            fingerprints.add(fingerprint)
        if len(repository_hashes) < limits.minimum_unique_repositories:
            failures.append(
                "unique_repositories:"
                f"{len(repository_hashes)}<{limits.minimum_unique_repositories}"
            )

        budget = self._budget_policy()
        declared_budget = budget.get("declared") or {}
        if not declared_budget or placeholder(declared_budget):
            failures.append("budget_policy_invalid")
        for key in (
            "same_repository_revision",
            "same_task_prompt",
            "same_oracle",
            "same_network_policy",
        ):
            if declared_budget.get(key) is not True:
                failures.append(f"budget_policy_not_equal:{key}")
        try:
            maximum_wall = float(
                declared_budget.get("maximum_wall_time_seconds_per_run", 0)
            )
        except (TypeError, ValueError):
            maximum_wall = 0.0
        if maximum_wall <= 0 or budget["timeout_seconds"] > maximum_wall:
            failures.append("budget_wall_time_invalid")
        environment = self._environment_manifest().get("declared") or {}
        if not environment or placeholder(environment):
            failures.append("environment_manifest_invalid")
        for key in ("runner_image", "operating_system", "hardware_class"):
            if not str(environment.get(key, "")).strip():
                failures.append(f"environment_field_missing:{key}")

        return {
            "schema_version": "nexus.superiority-preflight.v1",
            "ready": not failures,
            "manifest_sha256": self.manifest_sha256,
            "unique_tasks": len(tasks),
            "unique_repositories": len(repository_hashes),
            "trials": trials,
            "categories": dict(sorted(categories.items())),
            "failures": sorted(set(failures)),
        }

    def _validate_manifest(self) -> None:
        if not isinstance(self.manifest.get("agents"), dict) or not 2 <= len(self.manifest["agents"]) <= 4:
            raise ValueError("manifest.agents must define between two and four agents")
        tasks = self.manifest.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise ValueError("manifest.tasks must be a non-empty list")
        ids: set[str] = set()
        for task in tasks:
            task_id = str(task.get("id", "")).strip()
            if not task_id or task_id in ids:
                raise ValueError("every task requires a unique non-empty id")
            ids.add(task_id)
            repo = self._resolve(str(task.get("repository", "")))
            if not repo.is_dir():
                raise ValueError(f"task repository not found: {repo}")
            verification = task.get("verification")
            if not isinstance(verification, list) or not verification:
                raise ValueError(f"task {task_id} requires deterministic verification argv arrays")
            if any(not isinstance(command, list) or not command for command in verification):
                raise ValueError(f"task {task_id} verification entries must be argv arrays")

    def _resolve(self, value: str) -> Path:
        path = Path(value).expanduser()
        if not path.is_absolute() and self.manifest_path:
            path = self.manifest_path.parent / path
        return path.resolve()

    def run(self, *, output: str | Path | None = None, dry_run: bool = False) -> DuelReport:
        rng = random.Random(self.seed)
        invocations = {name: self._invocation(name, data) for name, data in self.manifest["agents"].items()}
        all_results: list[dict[str, Any]] = []
        trials = max(1, int(self.manifest.get("trials", 1)))
        with tempfile.TemporaryDirectory(prefix="nexus-duel-") as temporary:
            root = Path(temporary)
            for trial in range(trials):
                for task in self.manifest["tasks"]:
                    order = list(invocations)
                    rng.shuffle(order)
                    task_record = {
                        "task_id": task["id"],
                        "category": str(task.get("category", "unspecified")),
                        "repository_id": str(task.get("repository_id", task["id"])),
                        "trial": trial + 1,
                        "order": order,
                        "budget": self._effective_task_budget(task),
                        "results": [],
                    }
                    for agent_name in order:
                        result = self._run_one(
                            invocation=invocations[agent_name], task=task, trial=trial + 1,
                            workspace_root=root, dry_run=dry_run,
                        )
                        task_record["results"].append(asdict(result))
                    all_results.append(task_record)
        qualification = dict(self.manifest.get("qualification") or {})
        # Signatures are added only after execution by the independent evaluator.
        # Never carry a pre-run signature into a report whose results have changed.
        for key in ("evaluator_public_key", "evaluator_signature", "signature_algorithm"):
            qualification.pop(key, None)
        qualification.update(
            {
                "sealed_manifest_sha256": self.manifest_sha256,
                "oracle_bundle_sha256": self._oracle_bundle_sha256(),
                "budget_policy": self._budget_policy(),
                "budget_policy_sha256": self._budget_policy_sha256(),
                "environment_manifest": self._environment_manifest(),
                "environment_manifest_sha256": self._environment_manifest_sha256(),
            }
        )
        report = DuelReport(
            schema_version="nexus.competitive-duel.v3",
            manifest_sha256=self.manifest_sha256,
            seed=self.seed,
            dry_run=bool(dry_run),
            generated_at=datetime.now(timezone.utc).isoformat(),
            task_results=all_results,
            summary=self._summary(all_results),
            qualification=qualification,
        )
        if output:
            Path(output).write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True))
        return report

    @staticmethod
    def _hash_directory(root: Path) -> str:
        digest = hashlib.sha256()
        if not root.is_dir():
            digest.update(b"MISSING")
            return digest.hexdigest()
        for path in sorted(item for item in root.rglob("*") if item.is_file()):
            relative = path.relative_to(root).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            data = path.read_bytes()
            digest.update(len(data).to_bytes(8, "big"))
            digest.update(data)
        return digest.hexdigest()

    def _oracle_bundle_sha256(self) -> str:
        payload: dict[str, str] = {}
        for task in self.manifest["tasks"]:
            source = self._resolve(str(task["repository"]))
            oracle_name = str(task.get("oracle_dir", ".oracle"))
            payload[str(task["id"])] = self._hash_directory(source / oracle_name)
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _canonical_sha256(payload: Mapping[str, Any]) -> str:
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()

    def _budget_policy(self) -> dict[str, Any]:
        declared = self.manifest.get("budget_policy") or {}
        if not isinstance(declared, Mapping):
            declared = {}
        return {
            "trials": max(1, int(self.manifest.get("trials", 1))),
            "timeout_seconds": float(self.manifest.get("timeout_seconds", 900)),
            "verification_timeout_seconds": float(
                self.manifest.get("verification_timeout_seconds", 300)
            ),
            "declared": dict(declared),
        }

    def _budget_policy_sha256(self) -> str:
        return self._canonical_sha256(self._budget_policy())

    def _environment_manifest(self) -> dict[str, Any]:
        declared = self.manifest.get("environment_manifest") or {}
        if not isinstance(declared, Mapping):
            declared = {}
        runtime = {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python_implementation": platform.python_implementation(),
            "python_version": platform.python_version(),
            "executable": str(Path(sys.executable).resolve()),
            "harness": "nexus.competitive-duel.v3",
        }
        return {"declared": dict(declared), "runtime": runtime}

    def _environment_manifest_sha256(self) -> str:
        return self._canonical_sha256(self._environment_manifest())

    def _effective_task_budget(self, task: Mapping[str, Any]) -> dict[str, float]:
        return {
            "agent_timeout_seconds": float(
                task.get("timeout_seconds", self.manifest.get("timeout_seconds", 900))
            ),
            "verification_timeout_seconds": float(
                task.get(
                    "verification_timeout_seconds",
                    self.manifest.get("verification_timeout_seconds", 300),
                )
            ),
        }

    def _invocation(self, name: str, data: Mapping[str, Any]) -> AgentInvocation:
        argv = data.get("argv")
        if not isinstance(argv, list) or not argv:
            raise ValueError(f"agent {name} requires argv")
        return AgentInvocation(
            name=name,
            argv=tuple(map(str, argv)),
            version_argv=tuple(map(str, data.get("version_argv") or [])),
            success_markers=tuple(map(str, data.get("success_markers") or ("COMPLETED", "VERIFIED", "SUCCESS"))),
            metrics_file=str(data.get("metrics_file") or ""),
            product_identity=str(data.get("product_identity") or "").strip(),
            model_identity=str(data.get("model_identity") or "").strip(),
        )

    def _run_one(
        self, *, invocation: AgentInvocation, task: Mapping[str, Any], trial: int,
        workspace_root: Path, dry_run: bool,
    ) -> AgentRunResult:
        source = self._resolve(str(task["repository"]))
        workspace = workspace_root / f"{task['id']}-{trial}-{invocation.name}"
        oracle_name = str(task.get("oracle_dir", ".oracle"))
        shutil.copytree(source, workspace, ignore=shutil.ignore_patterns(oracle_name, ".git", ".nexus"))
        baseline = self._snapshot(workspace)
        argv = tuple(self._expand(item, task, workspace) for item in invocation.argv)
        executable = shutil.which(argv[0]) or (argv[0] if Path(argv[0]).is_file() else "")
        baseline_hash = hashlib.sha256(
            json.dumps(baseline, sort_keys=True).encode("utf-8")
        ).hexdigest()
        provenance = {
            "argv_sha256": hashlib.sha256(json.dumps(argv).encode()).hexdigest(),
            "executable": str(Path(executable).resolve()) if executable else "",
            "version": self._version(invocation) if executable else "unavailable",
            "repository_sha256": baseline_hash,
            "prompt_sha256": hashlib.sha256(str(task.get("prompt", "")).encode()).hexdigest(),
            "product_identity": invocation.product_identity,
            "model_identity": invocation.model_identity,
        }
        if dry_run:
            return AgentRunResult(invocation.name, str(task["id"]), bool(executable), False, False, False, False, None, 0, provenance=provenance)
        if not executable:
            return AgentRunResult(invocation.name, str(task["id"]), False, False, False, False, False, None, 0, stderr="executable unavailable", provenance=provenance)
        started = time.monotonic()
        timeout = self._effective_task_budget(task)["agent_timeout_seconds"]
        completed = _run_captured_process(
            argv, cwd=workspace, timeout=timeout, env=self._safe_env(task)
        )
        exit_code = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
        completed_normally = not completed.timed_out and exit_code is not None
        provenance["process_tree_cleanup"] = {
            "isolated_group": os.name in {"posix", "nt"},
            "descendants_reaped": completed.descendants_reaped,
            "timed_out": completed.timed_out,
        }
        duration = int((time.monotonic() - started) * 1000)
        changed = self._changed_files(baseline, self._snapshot(workspace))
        allowed = tuple(map(str, task.get("allowed_paths") or []))
        unexpected = [path for path in changed if allowed and not self._matches(path, allowed)]
        self._install_oracle(source, workspace, oracle_name)
        verification = self._verify(task, workspace)
        forbidden = tuple(map(str, task.get("forbidden_content") or []))
        forbidden_hit = self._contains_forbidden(workspace, forbidden)
        verified = bool(verification) and all(item["success"] for item in verification) and not unexpected and not forbidden_hit
        output = f"{stdout}\n{stderr}".upper()
        claimed = exit_code == 0 and any(marker.upper() in output for marker in invocation.success_markers)
        metrics = self._load_agent_metrics(invocation, task, workspace)
        return AgentRunResult(
            invocation.name, str(task["id"]), True, completed_normally, claimed, verified,
            claimed and not verified, exit_code, duration, changed, unexpected, verification,
            stdout[-20000:], stderr[-20000:], provenance,
            cost_usd=metrics.get("cost_usd"),
            input_tokens=metrics.get("input_tokens"),
            output_tokens=metrics.get("output_tokens"),
            human_interventions=metrics.get("human_interventions"),
        )

    @staticmethod
    def _load_agent_metrics(
        invocation: AgentInvocation, task: Mapping[str, Any], workspace: Path
    ) -> dict[str, Any]:
        template = invocation.metrics_file or str(task.get("metrics_file", ""))
        if not template:
            return {}
        candidate = Path(template.format(
            workspace=str(workspace), task_id=str(task.get("id", "")), agent=invocation.name
        ))
        if not candidate.is_absolute():
            candidate = workspace / candidate
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return {}
        if not isinstance(payload, Mapping):
            return {}
        metrics: dict[str, Any] = {}
        for key in ("cost_usd", "input_tokens", "output_tokens", "human_interventions"):
            value = payload.get(key)
            if value is None:
                continue
            try:
                metrics[key] = float(value) if key == "cost_usd" else int(value)
            except (TypeError, ValueError):
                continue
        return metrics

    @staticmethod
    def _expand(value: str, task: Mapping[str, Any], workspace: Path) -> str:
        return value.format(workspace=str(workspace), prompt=str(task.get("prompt", "")), task_id=str(task.get("id", "")))

    def _install_oracle(self, source: Path, workspace: Path, oracle_name: str) -> None:
        oracle = source / oracle_name
        if oracle.is_dir():
            shutil.copytree(oracle, workspace / oracle_name, dirs_exist_ok=True)

    def _verify(self, task: Mapping[str, Any], workspace: Path) -> list[dict[str, Any]]:
        records = []
        timeout = self._effective_task_budget(task)["verification_timeout_seconds"]
        for argv in task["verification"]:
            expanded = [self._expand(str(item), task, workspace) for item in argv]
            started = time.monotonic()
            result = _run_captured_process(
                expanded, cwd=workspace, timeout=timeout, env=self._safe_env(task)
            )
            records.append({
                "argv": expanded,
                "success": not result.timed_out and result.returncode == 0,
                "exit_code": result.returncode,
                "duration_ms": int((time.monotonic()-started)*1000),
                "stdout": result.stdout[-10000:],
                "stderr": result.stderr[-10000:],
                "timed_out": result.timed_out,
                "descendants_reaped": result.descendants_reaped,
            })
        return records

    @staticmethod
    def _safe_env(task: Mapping[str, Any]) -> dict[str, str]:
        allowed = {"PATH", "HOME", "USER", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "COMSPEC", "PATHEXT", "LANG", "LC_ALL"}
        env = {key: value for key, value in os.environ.items() if key in allowed}
        env.update({str(k): str(v) for k, v in dict(task.get("env") or {}).items()})
        return env

    @staticmethod
    def _snapshot(
        root: Path, *, excluded_paths: Sequence[str] = ()
    ) -> dict[str, str]:
        result: dict[str, str] = {}
        excluded = tuple(
            tuple(Path(value).parts) for value in excluded_paths if str(value).strip()
        )
        for path in sorted(root.rglob("*")):
            if not path.is_file() or ".git" in path.parts or ".nexus" in path.parts:
                continue
            relative = path.relative_to(root)
            if any(relative.parts[: len(prefix)] == prefix for prefix in excluded):
                continue
            result[relative.as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    @staticmethod
    def _changed_files(before: Mapping[str, str], after: Mapping[str, str]) -> list[str]:
        return sorted(path for path in set(before) | set(after) if before.get(path) != after.get(path))

    @staticmethod
    def _matches(path: str, patterns: Sequence[str]) -> bool:
        from fnmatch import fnmatch
        return any(fnmatch(path, pattern) or path == pattern or path.startswith(pattern.rstrip("/") + "/") for pattern in patterns)

    @staticmethod
    def _contains_forbidden(root: Path, markers: Iterable[str]) -> bool:
        terms = tuple(markers)
        if not terms:
            return False
        for path in root.rglob("*"):
            if path.is_file() and path.stat().st_size < 2_000_000:
                try:
                    text = path.read_text(errors="ignore")
                except OSError:
                    continue
                if any(term in text for term in terms):
                    return True
        return False

    @staticmethod
    def _version(invocation: AgentInvocation) -> str:
        if not invocation.version_argv:
            return "not-declared"
        result = _run_captured_process(invocation.version_argv, timeout=10)
        if result.returncode is None or result.timed_out:
            return "unavailable"
        return (result.stdout or result.stderr).strip()[:500]

    @staticmethod
    def _summary(records: list[dict[str, Any]]) -> dict[str, Any]:
        by_agent: dict[str, dict[str, Any]] = {}
        valid_pairs = 0
        valid_groups = 0
        categories: dict[str, int] = {}
        repositories: set[str] = set()
        for task in records:
            results = task["results"]
            complete = all(item["available"] and item["completed"] for item in results)
            if complete:
                valid_groups += 1
            if len(results) == 2 and complete:
                valid_pairs += 1
            category = str(task.get("category", "unspecified"))
            categories[category] = categories.get(category, 0) + 1
            repositories.add(str(task.get("repository_id", task.get("task_id", ""))))
            for item in results:
                bucket = by_agent.setdefault(item["agent"], {
                    "runs": 0, "available": 0, "completed": 0, "verified": 0,
                    "false_success": 0, "unexpected_change_runs": 0,
                    "duration_ms": 0, "cost_usd_total": 0.0, "cost_observations": 0,
                    "human_interventions": 0, "intervention_observations": 0,
                })
                bucket["runs"] += 1
                bucket["available"] += int(item["available"])
                bucket["completed"] += int(item["completed"])
                bucket["verified"] += int(item["verified"])
                bucket["false_success"] += int(item["false_success"])
                bucket["unexpected_change_runs"] += int(bool(item.get("unexpected_files")))
                bucket["duration_ms"] += int(item["duration_ms"])
                if item.get("cost_usd") is not None:
                    bucket["cost_usd_total"] += float(item["cost_usd"])
                    bucket["cost_observations"] += 1
                if item.get("human_interventions") is not None:
                    bucket["human_interventions"] += int(item["human_interventions"])
                    bucket["intervention_observations"] += 1
        for bucket in by_agent.values():
            runs = max(1, bucket["runs"])
            bucket["verified_rate"] = bucket["verified"] / runs
            bucket["false_success_rate"] = bucket["false_success"] / runs
            bucket["unexpected_change_rate"] = bucket["unexpected_change_runs"] / runs
            bucket["average_duration_ms"] = bucket["duration_ms"] / runs
            bucket["average_cost_usd"] = (
                bucket["cost_usd_total"] / bucket["cost_observations"]
                if bucket["cost_observations"] else None
            )
            bucket["average_human_interventions"] = (
                bucket["human_interventions"] / bucket["intervention_observations"]
                if bucket["intervention_observations"] else None
            )
        return {
            "valid_pairs": valid_pairs,
            "valid_groups": valid_groups,
            "categories": categories,
            "unique_repositories": len(repositories),
            "agents": by_agent,
            "parity_claim_supported": False,
            "superiority_claim_supported": False,
        }
