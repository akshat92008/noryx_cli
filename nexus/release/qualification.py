"""Deterministic release qualification and supply-chain evidence contracts.

This module intentionally refuses to infer success.  Callers must provide test and
security evidence; absent evidence is reported as a warning/failure according to
channel policy rather than being converted into a green release claim.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Mapping

_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:[-+][0-9A-Za-z.-]+)?$")


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_tree_sha256(root: str | Path) -> str:
    """Hash release source while excluding generated runtime and build state."""

    base = Path(root).resolve()
    ignored = {
        ".git",
        ".venv",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "build",
        "dist",
        "release_evidence",
        "verification_evidence",
        "runs",
        ".noryx",
        ".nexusai",
    }
    digest = hashlib.sha256()
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(base)
        if any(part in ignored or part.endswith(".egg-info") for part in relative.parts):
            continue
        if path.suffix in {".pyc", ".whl", ".zip", ".gz"}:
            continue
        if path.name.startswith(".coverage"):
            continue
        digest.update(relative.as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


class ReleaseStatus(str, Enum):
    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


@dataclass(frozen=True)
class ChannelPolicy:
    name: str = "private-alpha"
    require_test_evidence: bool = False
    require_security_evidence: bool = False
    require_secret_scan: bool = True
    require_rollback_plan: bool = True
    require_artifact_evidence: bool = False
    required_test_names: tuple[str, ...] = ()
    required_security_names: tuple[str, ...] = ()
    required_report_names: tuple[str, ...] = ("junit", "coverage", "benchmark")
    allowed_failure_count: int = 0
    require_bound_evidence: bool = False


@dataclass(frozen=True)
class ReleaseScope:
    stable: tuple[str, ...] = ("cli_core", "workspace", "verification", "safety")
    beta: tuple[str, ...] = (
        "two_node_execution",
        "model_routing",
        "repository_intelligence",
        "recovery",
    )
    experimental: tuple[str, ...] = (
        "collaboration",
        "autonomy",
        "enterprise",
        "webapp",
        "plugins",
    )

    def classify(self, capability: str) -> str:
        key = str(capability).strip().lower()
        for label, values in (
            ("stable", self.stable),
            ("beta", self.beta),
            ("experimental", self.experimental),
        ):
            if key in {item.lower() for item in values}:
                return label
        return "unqualified"


DEFAULT_RELEASE_SCOPE = ReleaseScope()


@dataclass(frozen=True)
class ArtifactEvidence:
    path: str
    sha256: str
    size_bytes: int
    exists: bool = True

    @classmethod
    def from_path(cls, path: str | Path) -> "ArtifactEvidence":
        target = Path(path)
        if not target.is_file():
            return cls(str(target), "", 0, False)
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        return cls(str(target), digest, target.stat().st_size, True)


@dataclass(frozen=True)
class SupplyChainEvidence:
    dependencies: tuple[str, ...] = ()
    dependency_digest: str = ""
    secret_scan_passed: bool = False
    artifacts: tuple[ArtifactEvidence, ...] = ()
    provenance_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class RollbackPlan:
    safe_version: str = ""
    downgrade_tested: bool = False
    instructions: tuple[str, ...] = ()


@dataclass
class ReleaseQualification:
    version: str
    scope: ReleaseScope = DEFAULT_RELEASE_SCOPE
    supply_chain: SupplyChainEvidence = field(default_factory=SupplyChainEvidence)
    rollback_plan: RollbackPlan = field(default_factory=RollbackPlan)
    test_results: Mapping[str, Any] = field(default_factory=dict)
    security_results: Mapping[str, Any] = field(default_factory=dict)
    channel_policy: ChannelPolicy = field(default_factory=ChannelPolicy)
    evidence_binding: Mapping[str, Any] = field(default_factory=dict)
    expected_source_sha256: str = ""
    evidence_root: str = ""

    @staticmethod
    def _result_passed(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"pass", "passed", "ok", "success", "true", "verified"}
        if isinstance(value, Mapping):
            if "passed" in value:
                return bool(value["passed"])
            if "status" in value:
                return ReleaseQualification._result_passed(value["status"])
        return False

    def _validate_bound_evidence(self) -> list[str]:
        binding = self.evidence_binding
        failures: list[str] = []
        if not isinstance(binding, Mapping) or not binding:
            return ["bound_evidence_missing"]
        if binding.get("schema_version") != "nexus.release-evidence.v1":
            failures.append("bound_evidence_schema_invalid")
        if str(binding.get("version", "")) != self.version:
            failures.append("bound_evidence_version_mismatch")
        if (
            self.expected_source_sha256
            and binding.get("source_tree_sha256") != self.expected_source_sha256
        ):
            failures.append("bound_evidence_source_hash_mismatch")

        summary = binding.get("test_summary")
        parsed_counts: tuple[int, int, int, int] | None = None
        if not isinstance(summary, Mapping):
            failures.append("bound_evidence_test_summary_missing")
        else:
            try:
                parsed_counts = tuple(
                    int(summary[name]) for name in ("collected", "passed", "failed", "skipped")
                )
            except (KeyError, TypeError, ValueError):
                failures.append("bound_evidence_test_counts_invalid")
            else:
                collected, passed, failed, skipped = parsed_counts
                if min(parsed_counts) < 0:
                    failures.append("bound_evidence_test_counts_invalid")
                if collected != passed + failed + skipped:
                    failures.append("bound_evidence_test_counts_do_not_reconcile")
                if failed > self.channel_policy.allowed_failure_count:
                    failures.append("bound_evidence_contains_test_failures")

        try:
            stamp = datetime.fromisoformat(
                str(binding.get("generated_at", "")).replace("Z", "+00:00")
            )
            if stamp.tzinfo is None:
                raise ValueError("timezone missing")
            if stamp > datetime.now(timezone.utc).astimezone(stamp.tzinfo):
                failures.append("bound_evidence_timestamp_in_future")
        except (TypeError, ValueError):
            failures.append("bound_evidence_timestamp_invalid")

        artifact_hashes = binding.get("artifacts")
        if not isinstance(artifact_hashes, Mapping):
            failures.append("bound_evidence_artifacts_missing")
            artifact_hashes = {}
        for artifact in self.supply_chain.artifacts:
            if not artifact.exists:
                continue
            if artifact_hashes.get(Path(artifact.path).name) != artifact.sha256:
                failures.append(f"bound_evidence_artifact_hash_mismatch:{artifact.path}")

        source_archive_hash = str(binding.get("source_archive_sha256", ""))
        archive_artifacts = [
            item
            for item in self.supply_chain.artifacts
            if item.exists and Path(item.path).name.endswith((".tar.gz", ".zip"))
        ]
        if archive_artifacts and source_archive_hash not in {
            item.sha256 for item in archive_artifacts
        }:
            failures.append("bound_evidence_source_archive_hash_mismatch")
        elif archive_artifacts and not source_archive_hash:
            failures.append("bound_evidence_source_archive_hash_missing")

        if not str(binding.get("test_command", "")).strip():
            failures.append("bound_evidence_test_command_missing")
        runner = binding.get("runner")
        if not isinstance(runner, Mapping) or not runner.get("os") or not runner.get("python"):
            failures.append("bound_evidence_runner_missing")

        reports = binding.get("reports")
        required_reports = self.channel_policy.required_report_names
        if not isinstance(reports, Mapping) or any(
            name not in reports for name in required_reports
        ):
            failures.append("bound_evidence_reports_missing")
            return failures

        evidence_root = (
            Path(self.evidence_root).expanduser().resolve() if self.evidence_root else None
        )
        resolved_reports: dict[str, Path] = {}
        for name in required_reports:
            descriptor = reports.get(name)
            if not isinstance(descriptor, Mapping):
                failures.append(f"bound_evidence_report_invalid:{name}")
                continue
            relative = str(descriptor.get("path", "")).strip()
            expected_hash = str(descriptor.get("sha256", "")).strip()
            if not relative or not expected_hash:
                failures.append(f"bound_evidence_report_invalid:{name}")
                continue
            if evidence_root is None:
                continue
            target = (evidence_root / relative).resolve()
            try:
                target.relative_to(evidence_root)
            except ValueError:
                failures.append(f"bound_evidence_report_path_escape:{name}")
                continue
            if not target.is_file():
                failures.append(f"bound_evidence_report_missing:{name}")
                continue
            if sha256_file(target) != expected_hash:
                failures.append(f"bound_evidence_report_hash_mismatch:{name}")
                continue
            resolved_reports[name] = target

        junit = resolved_reports.get("junit")
        if junit is not None and parsed_counts is not None:
            try:
                xml_root = ET.parse(junit).getroot()
                suites = (
                    [xml_root]
                    if xml_root.tag == "testsuite"
                    else list(xml_root.findall("testsuite"))
                )
                collected = sum(int(item.attrib.get("tests", 0)) for item in suites)
                failed = sum(
                    int(item.attrib.get("failures", 0)) + int(item.attrib.get("errors", 0))
                    for item in suites
                )
                skipped = sum(int(item.attrib.get("skipped", 0)) for item in suites)
                actual = (collected, collected - failed - skipped, failed, skipped)
                if actual != parsed_counts:
                    failures.append("bound_evidence_junit_counts_mismatch")
            except (ET.ParseError, OSError, TypeError, ValueError):
                failures.append("bound_evidence_junit_invalid")

        benchmark = resolved_reports.get("benchmark")
        if benchmark is not None:
            try:
                benchmark_payload = json.loads(benchmark.read_text(encoding="utf-8"))
                benchmark_summary = benchmark_payload.get("summary", {})
                if int(benchmark_summary.get("failed", 1)) != 0:
                    failures.append("bound_evidence_benchmark_failed")
                if int(benchmark_summary.get("manifest_valid_tasks", 0)) < 1:
                    failures.append("bound_evidence_benchmark_not_executed_or_validated")
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                failures.append("bound_evidence_benchmark_invalid")

        offline = resolved_reports.get("offline_reliability")
        if offline is not None:
            try:
                offline_payload = json.loads(offline.read_text(encoding="utf-8"))
                offline_summary = offline_payload.get("summary", {})
                if int(offline_summary.get("failed", 1)) != 0:
                    failures.append("bound_evidence_offline_reliability_failed")
                if int(offline_summary.get("executed_scenarios", 0)) < 5:
                    failures.append("bound_evidence_offline_scenarios_missing")
                if int(offline_summary.get("real_repository_repairs", 0)) < 1:
                    failures.append("bound_evidence_offline_repair_missing")
                if offline_summary.get("intelligence_claim") != "none":
                    failures.append("bound_evidence_offline_benchmark_overclaims_intelligence")
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                failures.append("bound_evidence_offline_reliability_invalid")

        sbom = resolved_reports.get("sbom")
        if sbom is not None:
            try:
                sbom_payload = json.loads(sbom.read_text(encoding="utf-8"))
                if sbom_payload.get("spdxVersion") != "SPDX-2.3":
                    failures.append("bound_evidence_sbom_schema_invalid")
                packages = sbom_payload.get("packages") or []
                root_packages = [
                    item
                    for item in packages
                    if isinstance(item, Mapping) and item.get("name") == "noryx-cli"
                ]
                if not root_packages or root_packages[0].get("versionInfo") != self.version:
                    failures.append("bound_evidence_sbom_version_mismatch")
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                failures.append("bound_evidence_sbom_invalid")

        deploy = resolved_reports.get("deploy_check")
        if deploy is not None:
            try:
                deploy_payload = json.loads(deploy.read_text(encoding="utf-8"))
                if deploy_payload.get("supervised_production_ready") is not True:
                    failures.append("bound_evidence_supervised_deployment_not_ready")
                if deploy_payload.get("autonomous_production_ready") is True:
                    sandbox = deploy_payload.get("sandbox_qualification") or {}
                    competitive = deploy_payload.get("competitive_superiority") or {}
                    if (
                        not isinstance(sandbox, Mapping)
                        or sandbox.get("autonomous_ready") is not True
                    ):
                        failures.append("bound_evidence_autonomous_readiness_overclaimed")
                    elif (
                        not isinstance(competitive, Mapping)
                        or competitive.get("qualified") is not True
                    ):
                        failures.append("bound_evidence_autonomous_readiness_overclaimed")
                elif deploy_payload.get("autonomous_production_ready") is not False:
                    failures.append("bound_evidence_autonomous_readiness_overclaimed")
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                failures.append("bound_evidence_deploy_check_invalid")
        return failures

    def evaluate(self) -> dict[str, Any]:
        failures: list[str] = []
        warnings: list[str] = []

        if not _SEMVER.fullmatch(self.version or ""):
            failures.append("version_must_be_semver")
        if self.channel_policy.require_secret_scan and not self.supply_chain.secret_scan_passed:
            failures.append("secret_scan_failed")
        if self.channel_policy.require_rollback_plan:
            if not self.rollback_plan.safe_version:
                failures.append("rollback_safe_version_missing")
            if not self.rollback_plan.downgrade_tested:
                failures.append("rollback_not_tested")

        for name in self.channel_policy.required_test_names:
            if name not in self.test_results:
                failures.append(f"test_evidence_missing:{name}")
        for name in self.channel_policy.required_security_names:
            if name not in self.security_results:
                failures.append(f"security_evidence_missing:{name}")

        failed_tests = sorted(k for k, v in self.test_results.items() if not self._result_passed(v))
        if failed_tests:
            failures.extend(f"test_failed:{name}" for name in failed_tests)
        elif self.channel_policy.require_test_evidence and not self.test_results:
            failures.append("test_evidence_missing")
        elif not self.test_results:
            warnings.append("test_evidence_not_supplied")

        failed_security = sorted(
            k for k, v in self.security_results.items() if not self._result_passed(v)
        )
        if failed_security:
            failures.extend(f"security_failed:{name}" for name in failed_security)
        elif self.channel_policy.require_security_evidence and not self.security_results:
            failures.append("security_evidence_missing")
        elif not self.security_results:
            warnings.append("security_evidence_not_supplied")

        if self.channel_policy.require_artifact_evidence and not self.supply_chain.artifacts:
            failures.append("artifact_evidence_missing")
        missing_artifacts = [item.path for item in self.supply_chain.artifacts if not item.exists]
        failures.extend(f"artifact_missing:{path}" for path in missing_artifacts)

        if self.channel_policy.require_bound_evidence:
            failures.extend(self._validate_bound_evidence())
        status = ReleaseStatus.FAIL.value if failures else ReleaseStatus.PASS.value
        return {
            "status": status,
            "version": self.version,
            "channel": self.channel_policy.name,
            "failures": failures,
            "warnings": warnings,
            "qualified_scope": asdict(self.scope),
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"evaluation": self.evaluate()}


def build_supply_chain_evidence(
    *,
    dependency_lines: Iterable[str] = (),
    secret_scan_passed: bool = True,
    artifact_paths: Iterable[str | Path] = (),
    provenance_notes: Iterable[str] = (),
) -> SupplyChainEvidence:
    dependencies = tuple(
        line.strip()
        for line in dependency_lines
        if str(line).strip() and not str(line).lstrip().startswith("#")
    )
    canonical = "\n".join(sorted(dependencies)).encode("utf-8")
    digest = hashlib.sha256(canonical).hexdigest()
    artifacts = tuple(ArtifactEvidence.from_path(path) for path in artifact_paths)
    return SupplyChainEvidence(
        dependencies=dependencies,
        dependency_digest=digest,
        secret_scan_passed=bool(secret_scan_passed),
        artifacts=artifacts,
        provenance_notes=tuple(str(item) for item in provenance_notes),
    )
