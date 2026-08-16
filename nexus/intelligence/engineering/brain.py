"""Repository-aware engineering control plane used by the canonical runtime."""

from __future__ import annotations

import copy
import hashlib
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable

from nexus.path_grammar import extract_repository_paths
from nexus.intelligence.deliberation import DeliberationCompiler, DeliberationContract
from nexus.intelligence.engineering.constraints import ConstraintCompiler
from nexus.intelligence.engineering.failure_learning import FailureLearningStore, FailureLesson
from nexus.intelligence.engineering.long_horizon import LongHorizonController
from nexus.intelligence.engineering.memory import (
    EngineeringChange,
    EngineeringDecision,
    EngineeringFailure,
    EngineeringMemoryStore,
    EngineeringTaskMemory,
)
from nexus.intelligence.engineering.scope import (
    ScopeDecision,
    ScopeEvidenceType,
    ScopeExpansionEvidence,
    SurgicalScopeGuard,
)
from nexus.intelligence.engineering.semantic import SemanticVerificationResult, SemanticVerifier
from nexus.intelligence.repository.engine import RepositoryIntelligence
from nexus.intelligence.repository.evidence import FailureEvidenceExtractor
from nexus.intelligence.repository.model import ContextBundle
from nexus.intelligence.repository.snapshot import workspace_revision
from nexus.intelligence.task_profiles import TaskProfile, TaskProfiler
from nexus.multifile.ledger import CompletionLedger
from nexus.multifile.orchestrator import (
    CompletionAssessment,
    MultiFileCompletionContract,
    MultiFileOrchestrator,
)

_RISK_TERMS = {
    "critical": ("credential", "encryption", "payment", "billing", "authorization", "supply chain"),
    "high": ("authentication", "migration", "schema", "concurrency", "race condition", "security"),
}


def _task_type(objective: str) -> str:
    text = objective.lower()
    if any(term in text for term in ("fix", "bug", "regression", "broken", "error", "failure")):
        return "bug_repair"
    if "security" in text or "vulnerability" in text:
        return "security_remediation"
    if "migration" in text or "migrate" in text:
        return "migration"
    if "refactor" in text:
        return "refactor"
    if "test" in text and any(term in text for term in ("add", "create", "write")):
        return "test_creation"
    if any(term in text for term in ("add", "implement", "build", "create")):
        return "feature_implementation"
    if any(term in text for term in ("investigate", "explain", "analyze", "analyse")):
        return "investigation"
    return "feature_implementation"


def _risk_level(objective: str) -> str:
    text = objective.lower()
    for level, terms in _RISK_TERMS.items():
        if any(term in text for term in terms):
            return level
    return "medium"


def _explicit_paths(objective: str) -> list[str]:
    """Extract user-named repository paths through the canonical grammar."""
    return extract_repository_paths(objective)


def _extract_non_goals(objective: str) -> list[str]:
    matches = re.findall(r"(?:do not|don't|without)\s+([^.;\n]+)", objective, flags=re.IGNORECASE)
    return [item.strip() for item in matches if item.strip()]


@dataclass
class EngineeringContract:
    task_id: str
    objective: str
    task_type: str
    risk_level: str
    repository_tree_hash: str
    decisive_files: list[str] = field(default_factory=list)
    related_tests: list[str] = field(default_factory=list)
    callers: dict[str, list[str]] = field(default_factory=dict)
    architecture_constraints: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    context_confidence: float = 0.0
    scope_contract: dict[str, Any] = field(default_factory=dict)
    plan_critic: dict[str, Any] = field(default_factory=dict)
    memory_path: str = ""
    deliberation: dict[str, Any] = field(default_factory=dict)
    completion_contract: dict[str, Any] = field(default_factory=dict)
    task_profile: dict[str, Any] = field(default_factory=dict)
    completion_ledger: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class EngineeringBrain:
    """Coordinates repository intelligence, memory, scope, learning and semantics."""

    def __init__(self, repository_root: str | Path):
        self.root = Path(repository_root).expanduser().resolve()
        self.repository = RepositoryIntelligence(self.root)
        self.memory_store = EngineeringMemoryStore(self.root)
        self.failure_store = FailureLearningStore(self.root)
        self.semantic_verifier = SemanticVerifier()
        self.contract: EngineeringContract | None = None
        self.memory: EngineeringTaskMemory | None = None
        self.scope_guard: SurgicalScopeGuard | None = None
        self.long_horizon: LongHorizonController | None = None
        self.context_bundle: ContextBundle | None = None
        self.context_prompt: str = ""
        self._trusted_scope_evidence: dict[str, ScopeExpansionEvidence] = {}
        self._expected_file_hashes: dict[str, str | None] = {}
        self.deliberation: DeliberationContract | None = None
        self.completion_contract: MultiFileCompletionContract | None = None
        self.completion_ledger: CompletionLedger | None = None
        self.task_profile: TaskProfile | None = None
        self._inspected_files: set[str] = set()
        self._verified_files: set[str] = set()

    def prepare(self, objective: str, *, task_id: str, strict: bool = False) -> EngineeringContract:
        self.repository.build(force=False)
        self.task_profile = TaskProfiler.classify(objective)
        task_type = self.task_profile.legacy_task_type
        risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        keyword_risk = _risk_level(objective)
        risk = max(
            (self.task_profile.risk_level, keyword_risk), key=lambda item: risk_order.get(item, 1)
        )
        max_files = min(64, self.task_profile.max_files + (8 if strict else 0))
        max_tokens = min(128_000, self.task_profile.max_tokens + (16_000 if strict else 0))
        bundle = self.repository.context_bundle(
            objective,
            max_files=max_files,
            max_total_tokens=max_tokens,
            max_graph_hops=self.task_profile.max_graph_hops,
            candidate_multiplier=5 if self.task_profile.max_graph_hops >= 5 else 4,
        )
        compilation = ConstraintCompiler.compile(objective)
        decisive_candidates = list(
            dict.fromkeys(
                [
                    *(item.path for item in bundle.files),
                    *_explicit_paths(objective),
                ]
            )
        )
        decisive = ConstraintCompiler.remove_forbidden(decisive_candidates, compilation)
        related_tests = list(dict.fromkeys(item.test_file for item in bundle.tests))
        callers: dict[str, list[str]] = {}
        symbol_limit = 40 if self.task_profile.max_graph_hops >= 5 else 16
        caller_limit = 250 if self.task_profile.max_graph_hops >= 5 else 40
        for symbol in bundle.symbols[:symbol_limit]:
            paths = [
                item["path"]
                for item in self.repository.find_callers(symbol.name, limit=caller_limit)
            ]
            if paths:
                callers[symbol.name] = paths
        architecture_constraints = [
            f"Respect {item.layer_name} boundary ({len(item.files)} files)"
            for item in bundle.constraints
        ]
        non_goals = list(
            dict.fromkeys(
                [
                    *_extract_non_goals(objective),
                    *(item.source_text for item in compilation.constraints),
                ]
            )
        )
        self.scope_guard = SurgicalScopeGuard.from_repository_context(
            self.root,
            objective=objective,
            decisive_files=decisive,
            related_tests=related_tests,
            task_type=task_type,
            confidence=bundle.confidence,
            strict=strict,
        )
        self.scope_guard.contract.max_changed_files = max(
            self.scope_guard.contract.max_changed_files,
            min(48, max(8, self.task_profile.max_files // 2)),
        )
        self.scope_guard.contract.max_changed_lines = max(
            self.scope_guard.contract.max_changed_lines,
            3200 if risk == "critical" else 2200 if risk == "high" else 1200,
        )
        self.scope_guard.contract.expansion_budget = max(
            self.scope_guard.contract.expansion_budget,
            6 if self.task_profile.max_graph_hops >= 6 else 4,
        )
        self._expected_file_hashes = {
            path: record.content_hash
            for path, record in self.repository.files.items()
            if bool(record.test_file or getattr(record, "is_test", False))
        }
        for relative in self.scope_guard.contract.allowed_files:
            indexed = self.repository.files.get(relative)
            if indexed is not None:
                self._expected_file_hashes[relative] = indexed.content_hash
                continue
            target = (self.root / relative).resolve(strict=False)
            try:
                self._expected_file_hashes[relative] = (
                    hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
                )
            except OSError:
                self._expected_file_hashes[relative] = None
        critic = self._critic_summary(
            task_type=task_type,
            decisive_files=list(self.scope_guard.contract.allowed_files),
            related_tests=[
                path for path in related_tests if path in self.scope_guard.contract.allowed_files
            ],
            callers=callers,
            non_goals=non_goals,
            risk_level=risk,
            is_empty_repository=(len(self.repository.files) == 0),
        )
        allowed_decisive = list(self.scope_guard.contract.allowed_files)
        allowed_tests = [
            path for path in related_tests if path in self.scope_guard.contract.allowed_files
        ]
        caller_paths = list(dict.fromkeys(path for paths in callers.values() for path in paths))
        self.deliberation = DeliberationCompiler.compile(
            objective,
            task_type=task_type,
            risk_level=risk,
            context_tree_hash=bundle.repository_tree_hash,
            decisive_files=allowed_decisive,
            related_tests=allowed_tests,
            symbols=[item.name for item in bundle.symbols[:16]],
        )
        self.completion_contract = MultiFileOrchestrator.derive(
            objective,
            repository=self.repository,
            task_type=task_type,
            risk_level=risk,
            decisive_files=allowed_decisive,
            callers=caller_paths,
            related_tests=allowed_tests,
            explicit_files=_explicit_paths(objective),
            non_goals=non_goals,
        )
        self.completion_ledger = CompletionLedger.from_contract(
            self.completion_contract,
            repository=self.repository,
            profile=self.task_profile,
        )
        critic["task_profile"] = self.task_profile.to_dict()
        critic["required_steps"] = list(
            dict.fromkeys(
                [
                    *critic.get("required_steps", []),
                    *self.task_profile.required_investigations,
                    *self.task_profile.verification_layers,
                ]
            )
        )
        self._inspected_files.clear()
        self._verified_files.clear()
        self.memory = self.memory_store.create(
            task_id,
            objective,
            repository_tree_hash=bundle.repository_tree_hash,
            task_type=task_type,
            risk_level=risk,
            constraints=architecture_constraints,
            non_goals=non_goals,
            decisive_files=list(self.scope_guard.contract.allowed_files),
            related_tests=[
                path for path in related_tests if path in self.scope_guard.contract.allowed_files
            ],
        )
        self.long_horizon = LongHorizonController(self.root, task_id, objective)
        self.contract = EngineeringContract(
            task_id=task_id,
            objective=objective,
            task_type=task_type,
            risk_level=risk,
            repository_tree_hash=bundle.repository_tree_hash,
            decisive_files=list(self.scope_guard.contract.allowed_files),
            related_tests=[
                path for path in related_tests if path in self.scope_guard.contract.allowed_files
            ],
            callers=callers,
            architecture_constraints=architecture_constraints,
            non_goals=non_goals,
            context_confidence=bundle.confidence,
            scope_contract=self.scope_guard.contract.to_dict(),
            plan_critic=critic,
            memory_path=str(self.memory_store.path_for(task_id)),
            deliberation=self.deliberation.to_dict(),
            completion_contract=self.completion_contract.to_dict(),
            task_profile=self.task_profile.to_dict(),
            completion_ledger=self.completion_ledger.to_dict(),
        )
        self.context_bundle = bundle
        self.context_prompt = bundle.to_formatted_prompt()
        return self.contract

    def expand_context_from_failure(
        self,
        reason: str,
        evidence: object,
    ) -> dict[str, Any]:
        """Expand live engineering context from deterministic runtime evidence.

        The method only trusts paths that exist in the canonical repository index.
        It may broaden what the model can inspect, but mutation still passes through
        :meth:`authorize_mutation` and the surgical scope guard.
        """
        if self.context_bundle is None or self.contract is None:
            return {
                "expanded": False,
                "reason": "No active engineering context is available.",
                "paths": [],
            }

        self.repository.build(force=False)
        signals = FailureEvidenceExtractor.extract(
            evidence,
            repository_paths=self.repository.files,
        )
        previous_paths = {item.path for item in self.context_bundle.files}
        current_profile = self.task_profile or TaskProfiler.classify(self.contract.objective)
        refined_profile = TaskProfiler.refine(current_profile, signals)
        expanded = self.repository.expand_context(
            self.context_bundle,
            reason=reason,
            evidence=evidence,
            additional_files=list(signals.paths),
            risk_level=refined_profile.risk_level,
        )
        expanded_paths = [item.path for item in expanded.files]
        added_paths = [path for path in expanded_paths if path not in previous_paths]

        self.context_bundle = expanded
        self.context_prompt = expanded.to_formatted_prompt()
        self.task_profile = refined_profile
        self.contract.task_profile = refined_profile.to_dict()
        self.contract.context_confidence = expanded.confidence
        self.contract.decisive_files = list(
            dict.fromkeys([*self.contract.decisive_files, *expanded_paths])
        )
        self.contract.related_tests = list(
            dict.fromkeys(
                [
                    *self.contract.related_tests,
                    *(item.test_file for item in expanded.tests),
                    *signals.tests,
                ]
            )
        )
        self.contract.plan_critic = self._critic_summary(
            task_type=refined_profile.legacy_task_type,
            decisive_files=self.contract.decisive_files,
            related_tests=self.contract.related_tests,
            callers=self.contract.callers,
            non_goals=self.contract.non_goals,
            risk_level=refined_profile.risk_level,
            is_empty_repository=(len(self.repository.files) == 0),
        )

        # Runtime paths are eligible for scope expansion only when they were
        # extracted from actual verification/compiler output and are present in
        # the content-addressed repository index.
        revision = workspace_revision(self.root)
        registered: list[str] = []
        compiler_kinds = {
            "import_failure",
            "type_contract_failure",
            "build_failure",
            "runtime_exception",
        }
        for path in signals.paths:
            if path not in self.repository.files:
                continue
            evidence_type = (
                ScopeEvidenceType.FAILING_TEST
                if path in signals.tests or path.startswith(("test/", "tests/", "spec/", "specs/"))
                else ScopeEvidenceType.COMPILER_ERROR
                if set(signals.failure_kinds).intersection(compiler_kinds)
                else None
            )
            if evidence_type is None:
                continue
            evidence_id = hashlib.sha256(
                f"runtime:{revision}:{evidence_type.value}:{path}:{signals.raw_excerpt}".encode(
                    "utf-8"
                )
            ).hexdigest()
            scope_evidence = ScopeExpansionEvidence(
                evidence_type=evidence_type,
                target_path=path,
                evidence_id=f"runtime:{evidence_id[:24]}",
                source_revision=revision,
                details=(
                    "Path was extracted by Noryx from deterministic runtime verification "
                    "or compiler output and confirmed in the repository index."
                ),
            )
            self.register_scope_evidence(
                scope_evidence,
                trusted_source="verification_engine",
            )
            registered.append(scope_evidence.evidence_id)

        if self.memory is not None:
            self.record_decision(
                "Expanded repository context after runtime failure evidence.",
                rationale=reason[:1000],
                evidence=[*signals.failure_kinds, *signals.paths, *signals.symbols],
            )

        return {
            "expanded": bool(added_paths),
            "reason": reason,
            "paths": expanded_paths,
            "added_paths": added_paths,
            "tests": list(signals.tests),
            "symbols": list(signals.symbols),
            "failure_kinds": list(signals.failure_kinds),
            "task_profile": refined_profile.to_dict(),
            "registered_scope_evidence": registered,
            "confidence": expanded.confidence,
        }

    @staticmethod
    def _critic_summary(
        *,
        task_type: str,
        decisive_files: list[str],
        related_tests: list[str],
        callers: dict[str, list[str]],
        non_goals: list[str],
        risk_level: str,
        is_empty_repository: bool = False,
    ) -> dict[str, Any]:
        blocking: list[str] = []
        warnings: list[str] = []
        required_steps = [
            "reproduce or establish baseline behavior before mutation",
            "inspect decisive files and direct callers",
            "apply the smallest coherent patch",
            "run targeted verification",
            "run relevant regression verification",
            "review the final diff against the objective and non-goals",
        ]
        if not decisive_files and not is_empty_repository:
            blocking.append(
                "No decisive repository files were established; mutation must wait for context expansion."
            )
        if task_type in {"bug_repair", "security_remediation"} and not related_tests:
            warnings.append(
                "No related test was discovered; add or identify a behavioral acceptance check."
            )
        if risk_level in {"high", "critical"}:
            required_steps.append("run a bounded security or architecture-specific check")
        if not callers:
            warnings.append(
                "No callers were identified; explicitly search interfaces before changing a public symbol."
            )
        if non_goals:
            required_steps.append("prove prohibited areas remained unchanged")
        return {
            "decision": "REVISE"
            if blocking
            else ("APPROVE_WITH_WARNINGS" if warnings else "APPROVE"),
            "blocking_issues": blocking,
            "warnings": warnings,
            "required_steps": required_steps,
        }

    def _derive_scope_evidence(self, paths: Iterable[str | Path]) -> list[ScopeExpansionEvidence]:
        """Derive scope expansion from the current content-hashed repository graph."""
        if self.scope_guard is None:
            return []
        self.repository.build(force=False)
        revision = self.repository.tree_hash
        evidence: list[ScopeExpansionEvidence] = []
        allowed = set(self.scope_guard.contract.allowed_files)
        for raw_target in paths:
            try:
                target = self.scope_guard._normalize(raw_target)  # noqa: SLF001
            except ValueError:
                continue
            if target in allowed:
                continue
            target_record = self.repository.files.get(target)
            if target_record is None:
                continue
            target_stem = Path(target).stem
            target_symbols = {item.name for item in target_record.symbols}
            for source in sorted(allowed):
                source_record = self.repository.files.get(source)
                if source_record is None:
                    continue
                imported = any(
                    target_stem == Path(item.replace(".", "/")).name
                    or target_stem in item.split(".")
                    for item in source_record.imports
                )
                if imported:
                    evidence.append(
                        ScopeExpansionEvidence(
                            evidence_type=ScopeEvidenceType.IMPORT_EDGE,
                            target_path=target,
                            source_path=source,
                            evidence_id=f"repo:{revision}:import:{source}->{target}",
                            source_revision=revision,
                            details="Canonical repository index contains a direct import relationship.",
                        )
                    )
                    break
                referenced = sorted(target_symbols.intersection(source_record.references))
                if referenced:
                    evidence.append(
                        ScopeExpansionEvidence(
                            evidence_type=ScopeEvidenceType.SYMBOL_REFERENCE,
                            target_path=target,
                            source_path=source,
                            symbol=referenced[0],
                            evidence_id=f"repo:{revision}:symbol:{source}->{target}:{referenced[0]}",
                            source_revision=revision,
                            details="Canonical repository index contains a symbol reference.",
                        )
                    )
                    break
        for item in evidence:
            self._trusted_scope_evidence[item.evidence_id] = item
        return evidence

    def register_scope_evidence(
        self,
        evidence: ScopeExpansionEvidence,
        *,
        trusted_source: str,
    ) -> None:
        """Register scope evidence produced by a deterministic authority.

        Model-provided tool arguments are deliberately not a trust source.  Human
        approval must arrive through the confirmation subsystem, and repository
        evidence must be generated or revalidated by Noryx itself.
        """
        if trusted_source not in {
            "repository_index",
            "verification_engine",
            "compiler",
            "human_confirmation",
        }:
            raise ValueError(f"Untrusted scope-evidence source: {trusted_source}")
        current_revision = __import__(
            "nexus.intelligence.repository.snapshot",
            fromlist=["workspace_revision"],
        ).workspace_revision(self.root)
        if evidence.source_revision != current_revision:
            raise ValueError("Scope evidence does not match the current repository revision")
        self._trusted_scope_evidence[evidence.evidence_id] = evidence

    def _write_precondition_failure(self, paths: Iterable[str | Path]) -> str:
        """Return a conflict message when repository content changed since planning."""
        if self.scope_guard is None:
            return ""
        for raw in paths:
            try:
                relative = self.scope_guard._normalize(raw)  # noqa: SLF001
            except ValueError as exc:
                return str(exc)
            target = self.root / relative
            expected_known = relative in self._expected_file_hashes
            expected = self._expected_file_hashes.get(relative)
            if not expected_known:
                # Outside-scope files are validated by repository evidence first.
                # Capture their current content as the expansion precondition.
                self._expected_file_hashes[relative] = (
                    hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
                )
                continue
            current = hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else None
            if current != expected:
                return (
                    f"Concurrent modification detected for {relative}: repository content "
                    "changed after the engineering plan was established. Re-index and replan."
                )
        return ""

    def authorize_mutation(
        self,
        paths: Iterable[str | Path],
        *,
        expansion_evidence: Iterable[ScopeExpansionEvidence | dict[str, Any]] | None = None,
        reason: str = "",
    ) -> ScopeDecision:
        if self.scope_guard is None:
            return ScopeDecision(True, "No engineering scope contract is active.")
        if self.contract and self.contract.plan_critic.get("blocking_issues"):
            return ScopeDecision(
                False,
                "BLOCKED: repository intelligence could not establish a safe mutation scope."
            )
        conflict = self._write_precondition_failure(paths)
        if conflict:
            return ScopeDecision(False, conflict)
        normalized_requested: set[str] = set()
        for raw_path in paths:
            try:
                normalized_requested.add(self.scope_guard._normalize(raw_path))  # noqa: SLF001
            except ValueError:
                continue
        trusted: list[ScopeExpansionEvidence] = [
            item
            for item in self._trusted_scope_evidence.values()
            if item.target_path in normalized_requested
        ]
        for raw in expansion_evidence or []:
            try:
                candidate = ScopeExpansionEvidence.from_value(raw)
            except (KeyError, TypeError, ValueError):
                continue
            registered = self._trusted_scope_evidence.get(candidate.evidence_id)
            if registered == candidate and registered not in trusted:
                trusted.append(registered)
        trusted.extend(self._derive_scope_evidence(paths))
        return self.scope_guard.authorize(
            paths,
            expansion_evidence=trusted,
            reason=reason,
        )

    def _relative_path(self, path: str | Path) -> str:
        target = Path(path)
        if not target.is_absolute():
            target = self.root / target
        try:
            return target.resolve(strict=False).relative_to(self.root).as_posix()
        except ValueError as exc:
            raise ValueError(f"Path escapes repository root: {path}") from exc

    def record_inspection(self, paths: Iterable[str | Path]) -> None:
        normalized = [self._relative_path(path) for path in paths]
        self._inspected_files.update(normalized)
        if self.completion_ledger is not None:
            self.completion_ledger.record(
                "inspect", normalized, "Repository file inspected by runtime."
            )

    def record_verified_files(self, paths: Iterable[str | Path]) -> None:
        normalized = [self._relative_path(path) for path in paths]
        self._verified_files.update(normalized)
        if self.completion_ledger is not None:
            self.completion_ledger.record(
                "verify", normalized, "Verification evidence recorded by runtime."
            )

    def completion_assessment(
        self, changed_files: Iterable[str | Path]
    ) -> CompletionAssessment | None:
        if self.completion_contract is None:
            return None
        normalized = [self._relative_path(path) for path in changed_files]
        assessment = self.completion_contract.assess(
            inspected_files=self._inspected_files,
            changed_files=normalized,
            verified_files=self._verified_files,
        )
        if self.completion_ledger is None:
            return assessment
        self.completion_ledger.record(
            "change", normalized, "File content changed in the active transaction."
        )
        ledger = self.completion_ledger.assess()
        if ledger.complete:
            return assessment
        unresolved_inspection = {
            item.path
            for item in self.completion_ledger.obligations.values()
            if item.blocking and item.state.value == "pending" and item.action == "inspect"
        }
        unresolved_changes = {
            item.path
            for item in self.completion_ledger.obligations.values()
            if item.blocking and item.state.value == "pending" and item.action == "change"
        }
        unresolved_verification = {
            item.path
            for item in self.completion_ledger.obligations.values()
            if item.blocking and item.state.value == "pending" and item.action == "verify"
        }
        return CompletionAssessment(
            complete=False,
            missing_inspection=tuple(
                sorted(set(assessment.missing_inspection).union(unresolved_inspection))
            ),
            missing_changes=tuple(
                sorted(set(assessment.missing_changes).union(unresolved_changes))
            ),
            missing_verification=tuple(
                sorted(set(assessment.missing_verification).union(unresolved_verification))
            ),
            unexpected_changes=assessment.unexpected_changes,
            preserved_file_violations=assessment.preserved_file_violations,
            changed_files=assessment.changed_files,
            enforcement_mode=assessment.enforcement_mode,
        )

    def record_changes(
        self,
        changes: Iterable[tuple[str | Path, str, int]],
    ) -> None:
        """Persist a group of verified mutations as one authenticated state update.

        The in-memory contract is not advanced until the authenticated write
        succeeds.  This keeps multi-file mutations transactional with respect
        to engineering memory and optimistic-concurrency preconditions.
        """
        prepared: list[EngineeringChange] = []
        expected_hashes: dict[str, str | None] = {}
        for path, reason, lines_changed in changes:
            target = Path(path)
            if not target.is_absolute():
                target = self.root / target
            sha = ""
            if target.is_file():
                try:
                    sha = hashlib.sha256(target.read_bytes()).hexdigest()
                except OSError:
                    sha = ""
            try:
                rel = target.resolve(strict=False).relative_to(self.root).as_posix()
            except ValueError:
                rel = str(path)
            expected_hashes[rel] = sha or None
            prepared.append(
                EngineeringChange(
                    path=rel,
                    reason=reason[:1000],
                    sha256=sha,
                    lines_changed=max(0, int(lines_changed)),
                )
            )

        if self.memory is not None:
            candidate = copy.deepcopy(self.memory)
            candidate.changes.extend(prepared)
            self.memory_store.save(candidate)
            self.memory = candidate
        self._expected_file_hashes.update(expected_hashes)
        if self.completion_ledger is not None:
            self.completion_ledger.record(
                "change", expected_hashes, "Authenticated engineering change recorded."
            )

    def record_change(self, path: str | Path, *, reason: str, lines_changed: int = 0) -> None:
        self.record_changes([(path, reason, lines_changed)])

    def record_decision(
        self, statement: str, *, rationale: str = "", evidence: list[str] | None = None
    ) -> None:
        if self.memory is None:
            return
        self.memory.decisions.append(
            EngineeringDecision(
                statement=statement[:1000],
                rationale=rationale[:2000],
                evidence=list(evidence or []),
            )
        )
        self.memory_store.save(self.memory)

    def record_failure(self, *, category: str, phase: str, summary: str) -> FailureLesson:
        lesson = self.failure_store.record(category=category, phase=phase, summary=summary)
        if self.memory is not None:
            self.memory.failures.append(
                EngineeringFailure(
                    phase=phase,
                    category=category,
                    summary=summary[:2000],
                    strategy=lesson.recommended_strategy,
                    occurrence=lesson.occurrence,
                )
            )
            self.memory_store.save(self.memory)
        return lesson

    def semantic_verify(
        self,
        *,
        evidence: Iterable[dict[str, Any]],
        changed_files: Iterable[str],
        acceptance_criteria: Iterable[str] = (),
        review_required: bool = True,
    ) -> SemanticVerificationResult:
        if self.contract is None or self.scope_guard is None:
            return SemanticVerificationResult(
                status="PARTIALLY_VERIFIED",
                satisfied=False,
                findings=[],
                requirement_results={},
            )
        result = self.semantic_verifier.verify(
            objective=self.contract.objective,
            task_type=self.contract.task_type,
            evidence=evidence,
            changed_files=changed_files,
            allowed_files=self.scope_guard.contract.allowed_files,
            prohibited_patterns=self.scope_guard.contract.forbidden_patterns,
            acceptance_criteria=acceptance_criteria,
            constraints=self.scope_guard.contract.constraints,
            review_required=review_required,
            workspace_revision=__import__(
                "nexus.intelligence.repository.snapshot",
                fromlist=["workspace_revision"],
            ).workspace_revision(self.repository.root),
        )
        if self.memory is not None:
            self.memory.verification_summary = result.to_dict()
            self.memory.status = result.status
            self.memory_store.save(self.memory)
        return result

    def prompt_context(self) -> str:
        sections = ["[NEXUS ENGINEERING BRAIN]"]
        if self.contract:
            sections.append(
                "Task contract: "
                f"type={self.contract.task_type}, risk={self.contract.risk_level}, "
                f"tree={self.contract.repository_tree_hash}, confidence={self.contract.context_confidence:.2f}"
            )
            sections.append(
                "Decisive files: " + (", ".join(self.contract.decisive_files) or "none")
            )
            sections.append("Related tests: " + (", ".join(self.contract.related_tests) or "none"))
            if self.contract.non_goals:
                sections.append("Forbidden/non-goal changes: " + "; ".join(self.contract.non_goals))
            sections.append("Plan critic: " + str(self.contract.plan_critic))
            if self.completion_contract:
                sections.append("Completion contract: " + str(self.completion_contract.to_dict()))
            if self.task_profile:
                sections.append("Hard-task profile: " + str(self.task_profile.to_dict()))
            if self.completion_ledger:
                sections.append("Completion ledger: " + str(self.completion_ledger.to_dict()))
            if self.deliberation:
                sections.append(self.deliberation.to_prompt())
        if self.memory:
            sections.append(self.memory.prompt_context())
        lessons = self.failure_store.recent_context()
        if lessons:
            sections.append(lessons)
        if self.long_horizon:
            sections.append(self.long_horizon.resume_context())
        if self.context_prompt:
            sections.append(self.context_prompt)
        return "\n\n".join(sections)
