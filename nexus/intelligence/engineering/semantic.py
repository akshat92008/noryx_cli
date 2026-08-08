"""Criterion-specific semantic acceptance for repository changes.

Model prose is never evidence.  A requirement is supported only by a verified typed
record explicitly mapped to that criterion, or by a conservative deterministic mapping
for generic build/test/lint criteria.
"""

from __future__ import annotations

import fnmatch
import hashlib
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable

from nexus.intelligence.engineering.constraints import CompiledConstraint, ConstraintKind
from nexus.verification_evidence import effective_verification_evidence

_PROOF_KINDS = {
    "verification_check",
    "behavioral_check",
    "behavioral_verification",
    "compatibility_check",
    "security_check",
    "static_invariant",
    "http_observation",
    "database_assertion",
    "build_artifact",
    "file_hash_assertion",
    "compiler_check",
}
_MODEL_TOOLS = {"model", "llm", "assistant", "planner", "model_note"}


@dataclass
class SemanticFinding:
    code: str
    severity: str
    message: str
    evidence_ids: list[str] = field(default_factory=list)


@dataclass
class SemanticVerificationResult:
    status: str
    satisfied: bool
    findings: list[SemanticFinding] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    requirement_results: dict[str, str] = field(default_factory=dict)
    requirement_evidence: dict[str, list[str]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "satisfied": self.satisfied,
            "findings": [asdict(item) for item in self.findings],
            "evidence_ids": self.evidence_ids,
            "requirement_results": self.requirement_results,
            "requirement_evidence": self.requirement_evidence,
        }


def _criterion(value: Any, index: int) -> tuple[str, str, str]:
    if isinstance(value, dict):
        statement = str(
            value.get("statement") or value.get("text") or value.get("criterion") or ""
        ).strip()
        identifier = str(value.get("id") or "").strip()
        validation_type = (
            str(value.get("validation_type") or value.get("type") or "").strip().lower()
        )
    else:
        statement = str(value).strip()
        identifier = ""
        validation_type = ""
    if not identifier:
        digest = hashlib.sha256(statement.encode("utf-8")).hexdigest()[:10]
        identifier = f"AC-{index:03d}-{digest}"
    return identifier, statement, validation_type


def _record_is_independent_proof(record: dict[str, Any]) -> bool:
    if record.get("kind") not in _PROOF_KINDS or record.get("status") != "verified":
        return False
    tool = str(record.get("tool", "")).strip().lower()
    metadata = dict(record.get("metadata") or {})
    producer = str(metadata.get("producer_type") or metadata.get("source") or "").lower()
    if tool in _MODEL_TOOLS or producer in _MODEL_TOOLS:
        return False
    if record.get("command") and record.get("exit_code") != 0:
        return False
    if metadata.get("independently_validated") is not True:
        return False
    check_type = str(metadata.get("check_type") or "").lower()
    if check_type in {"test", "tests"}:
        if metadata.get("runner_valid") is not True:
            return False
        if metadata.get("verification_valid") is not True:
            return False
        if not str(metadata.get("workspace_revision") or ""):
            return False
        observed = metadata.get("observed_test_count", metadata.get("test_count"))
        detail = str(metadata.get("validation_detail") or metadata.get("validation_reason") or "")
        if observed is None and not any(
            marker in detail.lower()
            for marker in (
                "runner-specific success",
                "observed test-runner",
                "executed tests",
                "assertion script",
            )
        ):
            return False
    return True


def _explicit_criterion_ids(record: dict[str, Any]) -> set[str]:
    metadata = dict(record.get("metadata") or {})
    values: list[Any] = []
    for key in ("criterion_ids", "supports_criteria", "acceptance_criteria"):
        raw = metadata.get(key, [])
        if isinstance(raw, (str, int)):
            values.append(raw)
        elif isinstance(raw, list):
            values.extend(raw)
    return {str(value).strip() for value in values if str(value).strip()}


def _generic_validation_type(statement: str, declared: str = "") -> str:
    if declared:
        return declared
    lowered = statement.lower()
    if re.search(r"\b(?:all\s+)?tests?\b|\bregression\b", lowered):
        return "test"
    if "lint" in lowered or "format" in lowered:
        return "lint"
    if "type" in lowered and ("check" in lowered or "error" in lowered):
        return "type_check"
    if any(term in lowered for term in ("build", "compile", "syntax")):
        return "build"
    if "security" in lowered or "vulnerab" in lowered:
        return "security"
    if "api" in lowered and any(term in lowered for term in ("compatible", "unchanged", "stable")):
        return "api_compatibility"
    return ""


def _record_matches_validation_type(record: dict[str, Any], validation_type: str) -> bool:
    if not validation_type:
        return False
    metadata = dict(record.get("metadata") or {})
    check_type = str(metadata.get("check_type") or record.get("kind") or "").lower()
    aliases = {
        "test": {"test", "tests", "pytest", "unit", "integration", "regression"},
        "lint": {"lint", "ruff", "format"},
        "type_check": {"type_check", "type", "mypy", "pyright"},
        "build": {"build", "compile", "syntax"},
        "security": {"security", "security_scan", "sast"},
        "api_compatibility": {"api_compatibility", "contract", "compatibility"},
    }
    return check_type in aliases.get(validation_type, {validation_type})


class SemanticVerifier:
    """Fail closed when criteria, scope, or independent evidence are incomplete."""

    def verify(
        self,
        *,
        objective: str,
        task_type: str,
        evidence: Iterable[dict[str, Any]],
        changed_files: Iterable[str],
        allowed_files: Iterable[str],
        prohibited_patterns: Iterable[str],
        acceptance_criteria: Iterable[Any] = (),
        constraints: Iterable[dict[str, Any]] = (),
        review_required: bool = True,
        workspace_revision: str = "",
    ) -> SemanticVerificationResult:
        del objective
        raw_records = [dict(item) for item in evidence]
        effective_checks = effective_verification_evidence(
            [item for item in raw_records if item.get("kind") == "verification_check"]
        )
        effective_ids = {str(item.get("id")) for item in effective_checks}
        records = [
            item
            for item in raw_records
            if item.get("kind") != "verification_check" or str(item.get("id")) in effective_ids
        ]
        changed = list(dict.fromkeys(str(item) for item in changed_files if str(item)))
        allowed = set(str(item) for item in allowed_files)
        findings: list[SemanticFinding] = []
        evidence_ids = [str(item.get("id", "")) for item in records if item.get("id")]

        mutations = [
            item
            for item in records
            if item.get("kind") == "file_mutation" and item.get("status") == "verified"
        ]
        checks = [item for item in records if _record_is_independent_proof(item)]
        reviews = [
            item
            for item in records
            if item.get("kind") == "independent_review" and item.get("status") == "verified"
        ]

        if workspace_revision:
            stale = [
                item
                for item in checks
                if (item.get("metadata") or {}).get("workspace_revision")
                and (item.get("metadata") or {}).get("workspace_revision") != workspace_revision
            ]
            if stale:
                findings.append(
                    SemanticFinding(
                        "SEM-STALE-EVIDENCE",
                        "error",
                        "Verification evidence was produced against a different workspace revision.",
                        [str(item.get("id", "")) for item in stale if item.get("id")],
                    )
                )
                checks = [item for item in checks if item not in stale]

        if task_type not in {"read_only", "investigation", "documentation", "code_explanation"}:
            if not mutations and not changed:
                findings.append(
                    SemanticFinding(
                        "SEM-NO-MUTATION",
                        "error",
                        "The task required a repository change but no verified mutation exists.",
                    )
                )
            if (mutations or changed) and not checks:
                findings.append(
                    SemanticFinding(
                        "SEM-NO-EXTERNAL-CHECK",
                        "error",
                        "Mutations exist without a passing independent verification record.",
                    )
                )

        if review_required and (mutations or changed) and not reviews:
            findings.append(
                SemanticFinding(
                    "SEM-NO-INDEPENDENT-REVIEW",
                    "error",
                    "No independent semantic review supports the completion claim.",
                )
            )

        prohibited = [str(item) for item in prohibited_patterns]
        prohibited_changes = [
            path
            for path in changed
            if any(fnmatch.fnmatch(path, pattern) for pattern in prohibited)
        ]
        if prohibited_changes:
            findings.append(
                SemanticFinding(
                    "SEM-PROHIBITED-CHANGE",
                    "error",
                    "Changed files violate an explicit prohibition: "
                    + ", ".join(prohibited_changes),
                )
            )

        if allowed:
            unexpected = [path for path in changed if path not in allowed]
            if unexpected:
                findings.append(
                    SemanticFinding(
                        "SEM-SCOPE-EXPANSION",
                        "error",
                        "Changed files fall outside the approved engineering scope: "
                        + ", ".join(unexpected),
                    )
                )

        implementation_files = [path for path in changed if "test" not in path.lower()]
        if (
            task_type not in {"test_creation", "test_repair", "documentation"}
            and changed
            and not implementation_files
        ):
            findings.append(
                SemanticFinding(
                    "SEM-TESTS-ONLY",
                    "error",
                    "Only tests changed for a task that requires implementation behavior.",
                )
            )

        failed_evidence = [
            item
            for item in records
            if item.get("status") == "failed"
            and item.get("kind")
            in {
                "verification_check",
                "behavioral_check",
                "behavioral_verification",
                "compatibility_check",
                "independent_review",
                "engineering_state_integrity",
            }
        ]
        if failed_evidence:
            findings.append(
                SemanticFinding(
                    "SEM-FAILED-EVIDENCE",
                    "error",
                    "One or more required verification or review records failed.",
                    [str(item.get("id", "")) for item in failed_evidence if item.get("id")],
                )
            )

        compiled_constraints = [CompiledConstraint.from_dict(item) for item in constraints]
        for constraint in compiled_constraints:
            if constraint.kind == ConstraintKind.UNRESOLVED_HARD_CONSTRAINT:
                findings.append(
                    SemanticFinding(
                        "SEM-UNRESOLVED-CONSTRAINT",
                        "error",
                        f"Hard constraint was not compiled into an enforceable policy: {constraint.source_text}",
                    )
                )
            elif constraint.kind in {
                ConstraintKind.FORBID_PUBLIC_API_CHANGE,
                ConstraintKind.REQUIRE_BACKWARD_COMPATIBILITY,
            }:
                compatible = any(
                    _record_matches_validation_type(item, "api_compatibility") for item in checks
                )
                if not compatible:
                    findings.append(
                        SemanticFinding(
                            "SEM-COMPATIBILITY-PROOF-MISSING",
                            "error",
                            f"Constraint requires criterion-specific compatibility evidence: {constraint.source_text}",
                        )
                    )
            elif constraint.kind == ConstraintKind.PRESERVE_BEHAVIOR:
                regression = any(
                    _record_matches_validation_type(item, "test")
                    and str((item.get("metadata") or {}).get("test_origin", "")).lower()
                    in {"pre_existing", "trusted_acceptance", "external"}
                    for item in checks
                )
                if not regression:
                    findings.append(
                        SemanticFinding(
                            "SEM-BEHAVIOR-PRESERVATION-PROOF-MISSING",
                            "error",
                            f"Constraint requires a pre-existing or external regression check: {constraint.source_text}",
                        )
                    )

        criteria = [
            _criterion(item, index)
            for index, item in enumerate(acceptance_criteria, 1)
            if str(item).strip()
        ]
        requirement_results: dict[str, str] = {}
        requirement_evidence: dict[str, list[str]] = {}
        for criterion_id, statement, declared_type in criteria:
            validation_type = _generic_validation_type(statement, declared_type)
            matching: list[dict[str, Any]] = []
            for item in checks:
                explicit = _explicit_criterion_ids(item)
                if criterion_id in explicit or statement in explicit:
                    matching.append(item)
                    continue
                if _record_matches_validation_type(item, validation_type):
                    matching.append(item)
            ids = [str(item.get("id", "")) for item in matching if item.get("id")]
            requirement_results[statement] = "SUPPORTED" if ids else "UNVERIFIED"
            requirement_evidence[statement] = ids

        if criteria and any(value == "UNVERIFIED" for value in requirement_results.values()):
            findings.append(
                SemanticFinding(
                    "SEM-ACCEPTANCE-GAP",
                    "error",
                    "One or more explicit acceptance criteria lack criterion-specific independent evidence.",
                )
            )

        errors = [item for item in findings if item.severity == "error"]
        if errors:
            status = (
                "FAILED"
                if any(
                    item.code
                    in {
                        "SEM-FAILED-EVIDENCE",
                        "SEM-SCOPE-EXPANSION",
                        "SEM-PROHIBITED-CHANGE",
                        "SEM-STALE-EVIDENCE",
                    }
                    for item in errors
                )
                else "PARTIALLY_VERIFIED"
            )
            return SemanticVerificationResult(
                status, False, findings, evidence_ids, requirement_results, requirement_evidence
            )
        if findings:
            return SemanticVerificationResult(
                "PARTIALLY_VERIFIED",
                False,
                findings,
                evidence_ids,
                requirement_results,
                requirement_evidence,
            )
        return SemanticVerificationResult(
            "VERIFIED", True, findings, evidence_ids, requirement_results, requirement_evidence
        )
