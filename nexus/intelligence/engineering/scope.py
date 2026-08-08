"""Surgical editing contracts enforced beneath the model layer."""

from __future__ import annotations

import fnmatch
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from nexus.intelligence.engineering.constraints import (
    CompiledConstraint,
    ConstraintCompiler,
)

_ALWAYS_FORBIDDEN = (
    ".git/**",
    ".noryx/task-memory/**",
    ".noryx/long-horizon/**",
    "**/.env",
    "**/.env.*",
    "**/*credential*",
    "**/*secret*",
    "**/id_rsa*",
)

_SECURITY_TERMS = (
    "credential",
    "secret",
    "environment variable",
    "env file",
    "authentication config",
    "rotate key",
)


class ScopeEvidenceType(str, Enum):
    IMPORT_EDGE = "import_edge"
    SYMBOL_REFERENCE = "symbol_reference"
    FAILING_TEST = "failing_test"
    COMPILER_ERROR = "compiler_error"
    CALLER_EDGE = "caller_edge"
    INTERFACE_IMPLEMENTATION = "interface_implementation"
    CONFIGURATION_RELATIONSHIP = "configuration_relationship"
    HUMAN_APPROVAL = "human_approval"


@dataclass(frozen=True)
class ScopeExpansionEvidence:
    """Deterministic proof that a specific outside-scope path is required."""

    evidence_type: ScopeEvidenceType
    target_path: str
    evidence_id: str
    source_revision: str
    source_path: str = ""
    symbol: str = ""
    details: str = ""
    approved_by: str = ""

    @classmethod
    def from_value(
        cls, value: "ScopeExpansionEvidence | dict[str, Any]"
    ) -> "ScopeExpansionEvidence":
        if isinstance(value, cls):
            return value
        return cls(
            evidence_type=ScopeEvidenceType(str(value.get("evidence_type", ""))),
            target_path=str(value.get("target_path", "")),
            evidence_id=str(value.get("evidence_id", "")),
            source_revision=str(value.get("source_revision", "")),
            source_path=str(value.get("source_path", "")),
            symbol=str(value.get("symbol", "")),
            details=str(value.get("details", "")),
            approved_by=str(value.get("approved_by", "")),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["evidence_type"] = self.evidence_type.value
        return data


@dataclass
class ScopeDecision:
    allowed: bool
    reason: str
    normalized_paths: list[str] = field(default_factory=list)
    requires_scope_expansion: bool = False
    remaining_expansions: int = 0
    expansion_evidence_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ScopeContract:
    allowed_files: list[str] = field(default_factory=list)
    allowed_directories: list[str] = field(default_factory=list)
    forbidden_patterns: list[str] = field(default_factory=lambda: list(_ALWAYS_FORBIDDEN))
    constraints: list[dict[str, Any]] = field(default_factory=list)
    unresolved_constraints: list[str] = field(default_factory=list)
    max_changed_files: int = 8
    max_changed_lines: int = 500
    expansion_budget: int = 2
    strict: bool = False
    rationale: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def compiled_constraints(self) -> list[CompiledConstraint]:
        return [CompiledConstraint.from_dict(item) for item in self.constraints]


class SurgicalScopeGuard:
    """Authorize repository mutations against an explicit, bounded contract."""

    def __init__(self, root: str | Path, contract: ScopeContract):
        self.root = Path(root).expanduser().resolve()
        self.contract = contract
        self.changed_files: set[str] = set()
        self.changed_lines = 0
        self.expansions_used = 0
        self._approved_expansion_evidence: dict[str, ScopeExpansionEvidence] = {}

    @staticmethod
    def explicit_prohibitions(objective: str) -> list[str]:
        """Compatibility surface returning executable path patterns."""
        return ConstraintCompiler.compile(objective).forbidden_patterns()

    @classmethod
    def from_repository_context(
        cls,
        root: str | Path,
        *,
        objective: str,
        decisive_files: Iterable[str],
        related_tests: Iterable[str],
        task_type: str,
        confidence: float,
        strict: bool,
    ) -> "SurgicalScopeGuard":
        compilation = ConstraintCompiler.compile(objective)
        candidates = list(
            dict.fromkeys([*(str(p) for p in decisive_files), *(str(p) for p in related_tests)])
        )
        allowed = ConstraintCompiler.remove_forbidden(candidates, compilation)
        directories = sorted(
            {
                str(PurePosixPath(path).parent)
                for path in allowed
                if PurePosixPath(path).parent != PurePosixPath(".")
            }
        )
        lowered = objective.lower()
        forbidden = [*_ALWAYS_FORBIDDEN, *compilation.forbidden_patterns()]
        if any(term in lowered for term in _SECURITY_TERMS):
            # Security tasks may intentionally edit secret-adjacent configuration, but
            # only when repository intelligence selected that exact file.  Generic
            # credential and env globs remain forbidden otherwise.
            exact_allowed = set(allowed)
            forbidden = [
                pattern
                for pattern in forbidden
                if not any(cls._matches(path, pattern) for path in exact_allowed)
            ]
        limits = {
            "bug_repair": (6, 320),
            "test_repair": (6, 320),
            "security_remediation": (8, 500),
            "feature_implementation": (14, 900),
            "migration": (20, 1600),
            "refactor": (20, 1600),
        }
        max_files, max_lines = limits.get(task_type, (10, 700))
        contract = ScopeContract(
            allowed_files=allowed,
            allowed_directories=directories,
            forbidden_patterns=list(dict.fromkeys(forbidden)),
            constraints=[item.to_dict() for item in compilation.constraints],
            unresolved_constraints=list(compilation.unresolved),
            max_changed_files=max_files,
            max_changed_lines=max_lines,
            expansion_budget=1 if confidence >= 0.8 else 3,
            strict=bool(strict and confidence >= 0.55 and allowed),
            rationale={path: "selected by repository intelligence" for path in allowed},
        )
        return cls(root, contract)

    def _normalize(self, raw: str | Path) -> str:
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = self.root / path
        resolved = path.resolve(strict=False)
        try:
            return resolved.relative_to(self.root).as_posix()
        except ValueError as exc:
            raise ValueError(f"Path is outside repository scope: {resolved}") from exc

    @staticmethod
    def _matches(path: str, pattern: str) -> bool:
        normalized_pattern = pattern.replace("\\", "/")
        normalized_path = path.replace("\\", "/")
        return fnmatch.fnmatch(normalized_path, normalized_pattern) or fnmatch.fnmatch(
            f"/{normalized_path}", normalized_pattern
        )

    def _validate_expansion_evidence(
        self,
        outside: list[str],
        evidence_values: Iterable[ScopeExpansionEvidence | dict[str, Any]] | None,
    ) -> tuple[bool, str, list[ScopeExpansionEvidence]]:
        if not evidence_values:
            return False, "No deterministic scope-expansion evidence was supplied.", []
        try:
            evidence = [ScopeExpansionEvidence.from_value(item) for item in evidence_values]
        except (KeyError, TypeError, ValueError) as exc:
            return False, f"Malformed scope-expansion evidence: {exc}", []

        approved: list[ScopeExpansionEvidence] = []
        for target in outside:
            matches = []
            for item in evidence:
                try:
                    normalized_target = self._normalize(item.target_path)
                except ValueError:
                    continue
                if normalized_target != target:
                    continue
                if not item.evidence_id.strip() or not item.source_revision.strip():
                    continue
                if item.evidence_type == ScopeEvidenceType.HUMAN_APPROVAL:
                    if item.approved_by.strip().lower() not in {
                        "user",
                        "human",
                        "organization_policy",
                        "repository_owner",
                    }:
                        continue
                else:
                    if not item.source_path.strip() and item.evidence_type not in {
                        ScopeEvidenceType.FAILING_TEST,
                        ScopeEvidenceType.COMPILER_ERROR,
                    }:
                        continue
                    if item.source_path:
                        try:
                            source_path = self._normalize(item.source_path)
                        except ValueError:
                            continue
                        if source_path not in self.contract.allowed_files:
                            continue
                matches.append(item)
            if not matches:
                return (
                    False,
                    f"No valid repository or human evidence authorizes expansion to {target}.",
                    [],
                )
            approved.append(matches[0])
        return True, "Scope expansion is supported by typed evidence.", approved

    def authorize(
        self,
        paths: Iterable[str | Path],
        *,
        expansion_evidence: Iterable[ScopeExpansionEvidence | dict[str, Any]] | None = None,
        reason: str = "",
    ) -> ScopeDecision:
        del reason  # free-form model prose is never authorization
        try:
            normalized = list(
                dict.fromkeys(self._normalize(path) for path in paths if str(path).strip())
            )
        except ValueError as exc:
            return ScopeDecision(False, str(exc))
        if not normalized:
            return ScopeDecision(False, "Mutation tool did not declare a target path.")

        for path in normalized:
            for pattern in self.contract.forbidden_patterns:
                if self._matches(path, pattern):
                    return ScopeDecision(
                        False,
                        f"{path} matches prohibited change pattern {pattern!r}.",
                        normalized,
                        remaining_expansions=max(
                            0, self.contract.expansion_budget - self.expansions_used
                        ),
                    )

        prospective = self.changed_files | set(normalized)
        if len(prospective) > self.contract.max_changed_files:
            return ScopeDecision(
                False,
                f"Change would exceed the {self.contract.max_changed_files}-file surgical budget.",
                normalized,
                remaining_expansions=max(0, self.contract.expansion_budget - self.expansions_used),
            )

        if not self.contract.strict:
            return ScopeDecision(
                True,
                "Scope is advisory because repository confidence is insufficient.",
                normalized,
            )

        outside = [path for path in normalized if path not in self.contract.allowed_files]
        if not outside:
            return ScopeDecision(
                True,
                "All targets are inside the approved surgical scope.",
                normalized,
            )

        if self.contract.unresolved_constraints:
            return ScopeDecision(
                False,
                "Unresolved hard user constraints require clarification or human approval before scope expansion: "
                + "; ".join(self.contract.unresolved_constraints),
                normalized,
                requires_scope_expansion=True,
                remaining_expansions=max(0, self.contract.expansion_budget - self.expansions_used),
            )

        if self.expansions_used >= self.contract.expansion_budget:
            return ScopeDecision(
                False,
                "Scope expansion budget exhausted; replan with repository evidence before editing "
                + ", ".join(outside),
                normalized,
                requires_scope_expansion=True,
                remaining_expansions=0,
            )

        valid, message, approved = self._validate_expansion_evidence(outside, expansion_evidence)
        if not valid:
            return ScopeDecision(
                False,
                message,
                normalized,
                requires_scope_expansion=True,
                remaining_expansions=self.contract.expansion_budget - self.expansions_used,
            )

        self.expansions_used += 1
        for path, evidence in zip(outside, approved, strict=True):
            if path not in self.contract.allowed_files:
                self.contract.allowed_files.append(path)
            self.contract.rationale[path] = f"{evidence.evidence_type.value}:{evidence.evidence_id}"
            self._approved_expansion_evidence[path] = evidence
        evidence_ids = [item.evidence_id for item in approved]
        return ScopeDecision(
            True,
            "Approved bounded scope expansion from typed repository/human evidence.",
            normalized,
            requires_scope_expansion=True,
            remaining_expansions=max(0, self.contract.expansion_budget - self.expansions_used),
            expansion_evidence_ids=evidence_ids,
        )

    def register_change(
        self, paths: Iterable[str | Path], *, lines_changed: int = 0
    ) -> ScopeDecision:
        decision = self.authorize(paths)
        if not decision.allowed:
            return decision
        projected_lines = self.changed_lines + max(0, int(lines_changed))
        if projected_lines > self.contract.max_changed_lines:
            return ScopeDecision(
                False,
                f"Cumulative edit volume {projected_lines} exceeds the "
                f"{self.contract.max_changed_lines}-line budget.",
                decision.normalized_paths,
                remaining_expansions=max(0, self.contract.expansion_budget - self.expansions_used),
            )
        self.changed_files.update(decision.normalized_paths)
        self.changed_lines = projected_lines
        return decision
