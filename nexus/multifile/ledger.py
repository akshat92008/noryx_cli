"""Persistent completion ledger for repository-wide change obligations."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable

from nexus.intelligence.concurrency import ConcurrencyAnalyzer
from nexus.intelligence.task_profiles import RepositoryTaskKind, TaskProfile

if TYPE_CHECKING:  # pragma: no cover
    from nexus.intelligence.repository.engine import RepositoryIntelligence
    from nexus.multifile.orchestrator import MultiFileCompletionContract


class ObligationState(str, Enum):
    PENDING = "pending"
    SATISFIED = "satisfied"
    WAIVED = "waived"


@dataclass
class LedgerObligation:
    obligation_id: str
    path: str
    action: str
    reason: str
    blocking: bool = True
    source: str = "contract"
    state: ObligationState = ObligationState.PENDING
    evidence: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        return payload


@dataclass(frozen=True)
class LedgerAssessment:
    complete: bool
    unresolved: tuple[str, ...]
    unresolved_paths: tuple[str, ...]
    satisfied: int
    total: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class CompletionLedger:
    """Track obligations independently of model claims of completion."""

    def __init__(
        self, objective: str, repository_tree_hash: str, obligations: Iterable[LedgerObligation]
    ):
        self.objective = objective
        self.repository_tree_hash = repository_tree_hash
        self.obligations: dict[str, LedgerObligation] = {
            item.obligation_id: item for item in obligations
        }

    @classmethod
    def from_contract(
        cls,
        contract: "MultiFileCompletionContract",
        *,
        repository: "RepositoryIntelligence",
        profile: TaskProfile,
    ) -> "CompletionLedger":
        obligations: list[LedgerObligation] = []
        for item in contract.obligations:
            # Advisory contracts use inspection items as guidance, not as a
            # completion blocker. Explicit change/verification obligations and
            # every obligation in a hard contract remain fail-closed.
            blocking = item.blocking and (contract.hard_enforcement or item.obligation != "inspect")
            obligations.append(
                cls._obligation(
                    item.path, item.obligation, item.reason, blocking, "completion_contract"
                )
            )
        for path in contract.required_change_files:
            obligations.append(
                cls._obligation(
                    path, "change", "Explicit required change file.", True, "completion_contract"
                )
            )
        for path in contract.required_verification_files:
            obligations.append(
                cls._obligation(
                    path,
                    "verify",
                    "Mapped regression verification file.",
                    True,
                    "completion_contract",
                )
            )

        if profile.kind in {
            RepositoryTaskKind.REPOSITORY_API_CHANGE,
            RepositoryTaskKind.FRAMEWORK_MIGRATION,
            RepositoryTaskKind.DIFFICULT_REFACTOR,
        }:
            objective_symbols = cls._objective_symbols(contract.objective)
            owner_paths: list[str] = []
            resolved_symbols: list[str] = []
            for symbol in objective_symbols:
                owners = repository.find_symbols(symbol, limit=20)
                for owner in owners:
                    owner_paths.append(owner.file_path)
                    resolved_symbols.append(owner.name)
                    obligations.append(
                        cls._obligation(
                            owner.file_path,
                            "inspect",
                            f"Defines repository-wide symbol '{symbol}'.",
                            True,
                            "symbol_owner",
                        )
                    )

            closure_seeds = list(dict.fromkeys([*owner_paths, *contract.required_change_files]))
            closure = repository.impact_closure(
                closure_seeds,
                symbols=resolved_symbols or objective_symbols,
                max_hops=profile.max_graph_hops,
                limit=max(250, profile.max_files * 8),
                include_tests=True,
                include_configuration=(profile.kind == RepositoryTaskKind.FRAMEWORK_MIGRATION),
            )
            for impact in closure:
                path = str(impact["path"])
                reasons = ", ".join(impact.get("reasons") or ["impact closure"])
                if bool(impact.get("is_test")):
                    action = "verify"
                    source = "impacted_test_closure"
                elif bool(impact.get("is_config")) or bool(impact.get("is_migration")):
                    action = "inspect"
                    source = "migration_surface"
                elif profile.kind == RepositoryTaskKind.DIFFICULT_REFACTOR:
                    action = "inspect"
                    source = "refactor_impact_closure"
                else:
                    action = "change"
                    source = "transitive_caller_closure"
                obligations.append(
                    cls._obligation(
                        path,
                        action,
                        f"Transitive repository impact: {reasons}.",
                        True,
                        source,
                    )
                )

        if profile.kind == RepositoryTaskKind.STATE_CONCURRENCY_DEFECT:
            # Contract obligations are FileObligation objects; inspect all state-bearing candidates.
            candidate_paths = list(
                dict.fromkeys(
                    [
                        *contract.required_change_files,
                        *(
                            item.path
                            for item in contract.obligations
                            if item.obligation in {"inspect", "change"}
                        ),
                    ]
                )
            )
            for finding in ConcurrencyAnalyzer.analyze(repository.root, candidate_paths):
                obligations.append(
                    cls._obligation(
                        finding.path,
                        "verify",
                        f"Concurrency finding {finding.kind} at line {finding.line}: {finding.required_check}",
                        True,
                        "concurrency_analysis",
                    )
                )

        deduped: dict[tuple[str, str], LedgerObligation] = {}
        for item in obligations:
            key = (item.path, item.action)
            prior = deduped.get(key)
            if prior is None or (item.blocking and not prior.blocking):
                deduped[key] = item
            elif prior is not None and item.reason not in prior.reason:
                prior.reason = f"{prior.reason} {item.reason}"[:1200]
        return cls(contract.objective, contract.repository_tree_hash, deduped.values())

    @staticmethod
    def _objective_symbols(objective: str) -> list[str]:
        quoted = re.findall(r"[`'\"]([A-Za-z_][A-Za-z0-9_.]*)[`'\"]", objective)
        explicit = re.findall(
            r"(?:rename|change|migrate|replace|remove)\s+(?:the\s+)?(?:api|method|function|class|symbol)?\s*([A-Za-z_][A-Za-z0-9_.]*)",
            objective,
            re.I,
        )
        return list(dict.fromkeys(item.split(".")[-1] for item in [*quoted, *explicit]))

    @staticmethod
    def _obligation(
        path: str, action: str, reason: str, blocking: bool, source: str
    ) -> LedgerObligation:
        normalized = str(path).replace("\\", "/")
        digest = hashlib.sha256(f"{normalized}|{action}|{source}".encode()).hexdigest()[:16]
        return LedgerObligation(digest, normalized, action, reason, blocking, source)

    def record(self, action: str, paths: Iterable[str], evidence: str = "") -> None:
        normalized = {str(item).replace("\\", "/") for item in paths}
        for item in self.obligations.values():
            if item.action == action and item.path in normalized:
                item.state = ObligationState.SATISFIED
                if evidence:
                    item.evidence.append(evidence[:2000])

    def waive(self, obligation_id: str, *, reason: str) -> None:
        item = self.obligations[obligation_id]
        item.state = ObligationState.WAIVED
        item.evidence.append(f"WAIVER: {reason[:1800]}")

    def assess(self) -> LedgerAssessment:
        unresolved_items = [
            item
            for item in self.obligations.values()
            if item.blocking and item.state == ObligationState.PENDING
        ]
        unresolved = tuple(
            sorted(f"{item.action}:{item.path}:{item.reason}" for item in unresolved_items)
        )
        paths = tuple(sorted({item.path for item in unresolved_items}))
        satisfied = sum(item.state != ObligationState.PENDING for item in self.obligations.values())
        return LedgerAssessment(
            not unresolved_items, unresolved, paths, satisfied, len(self.obligations)
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "nexus.completion-ledger.v1",
            "objective": self.objective,
            "repository_tree_hash": self.repository_tree_hash,
            "obligations": [
                item.to_dict()
                for item in sorted(self.obligations.values(), key=lambda x: (x.path, x.action))
            ],
            "assessment": self.assess().to_dict(),
        }

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
        return target
