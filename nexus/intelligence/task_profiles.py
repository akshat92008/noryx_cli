"""Hard-task classification and capability requirements for repository work."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover
    from nexus.intelligence.repository.evidence import EvidenceSignals


class RepositoryTaskKind(str, Enum):
    HIDDEN_MULTI_FILE_BUG = "hidden_multi_file_bug"
    FRAMEWORK_MIGRATION = "framework_migration"
    FEATURE_ADDITION = "feature_addition"
    DIFFICULT_REFACTOR = "difficult_refactor"
    INDIRECT_TEST_FAILURE = "indirect_test_failure"
    REPOSITORY_API_CHANGE = "repository_wide_api_change"
    STATE_CONCURRENCY_DEFECT = "state_concurrency_defect"
    GENERAL_ENGINEERING = "general_engineering"


@dataclass(frozen=True)
class TaskProfile:
    kind: RepositoryTaskKind
    legacy_task_type: str
    risk_level: str
    max_files: int
    max_tokens: int
    max_graph_hops: int
    required_investigations: tuple[str, ...] = ()
    verification_layers: tuple[str, ...] = ()
    completion_obligations: tuple[str, ...] = ()
    signals: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        return payload


class TaskProfiler:
    """Classify by operational consequences, not a single keyword."""

    _PROFILES = {
        RepositoryTaskKind.HIDDEN_MULTI_FILE_BUG: dict(
            legacy_task_type="bug_repair",
            risk_level="high",
            max_files=28,
            max_tokens=56000,
            max_graph_hops=5,
            required_investigations=(
                "reproduce failure",
                "trace canonical call path",
                "inspect callers and state transitions",
                "map impacted tests",
            ),
            verification_layers=(
                "failing test",
                "impacted tests",
                "regression suite",
                "final diff review",
            ),
            completion_obligations=(
                "root cause evidenced",
                "all indirect callers inspected",
                "regression test added or identified",
            ),
        ),
        RepositoryTaskKind.FRAMEWORK_MIGRATION: dict(
            legacy_task_type="migration",
            risk_level="critical",
            max_files=48,
            max_tokens=96000,
            max_graph_hops=6,
            required_investigations=(
                "inventory framework surface",
                "map deprecated APIs",
                "inspect build/configuration",
                "identify compatibility boundaries",
                "stage migration",
            ),
            verification_layers=(
                "build",
                "type checks",
                "targeted tests",
                "full tests",
                "packaging/install smoke",
            ),
            completion_obligations=(
                "all deprecated usages resolved",
                "configuration migrated",
                "compatibility behavior proven",
                "rollback path retained",
            ),
        ),
        RepositoryTaskKind.FEATURE_ADDITION: dict(
            legacy_task_type="feature_implementation",
            risk_level="medium",
            max_files=24,
            max_tokens=48000,
            max_graph_hops=4,
            required_investigations=(
                "identify integration boundary",
                "map data/control flow",
                "identify acceptance tests",
            ),
            verification_layers=("new behavioral tests", "impacted tests", "regression suite"),
            completion_obligations=(
                "feature reachable from entry point",
                "error paths handled",
                "tests prove acceptance criteria",
            ),
        ),
        RepositoryTaskKind.DIFFICULT_REFACTOR: dict(
            legacy_task_type="refactor",
            risk_level="high",
            max_files=36,
            max_tokens=72000,
            max_graph_hops=5,
            required_investigations=(
                "map public contracts",
                "map callers",
                "capture behavior baseline",
                "identify architecture boundary",
            ),
            verification_layers=(
                "behavioral characterization",
                "type checks",
                "impacted tests",
                "full tests",
            ),
            completion_obligations=(
                "behavior preserved",
                "all callers migrated",
                "old path removed or compatibility-shimmed",
            ),
        ),
        RepositoryTaskKind.INDIRECT_TEST_FAILURE: dict(
            legacy_task_type="test_repair",
            risk_level="high",
            max_files=30,
            max_tokens=60000,
            max_graph_hops=5,
            required_investigations=(
                "reproduce exact test",
                "trace fixture/setup path",
                "inspect production dependency",
                "reject test-only workaround",
            ),
            verification_layers=(
                "original failing test",
                "neighboring tests",
                "production behavior check",
                "regression suite",
            ),
            completion_obligations=(
                "production root cause fixed",
                "test semantics unchanged unless incorrect",
                "indirect dependency verified",
            ),
        ),
        RepositoryTaskKind.REPOSITORY_API_CHANGE: dict(
            legacy_task_type="migration",
            risk_level="critical",
            max_files=52,
            max_tokens=104000,
            max_graph_hops=6,
            required_investigations=(
                "identify defining symbols",
                "enumerate static callers",
                "scan dynamic/string references",
                "inspect docs/config/external boundary",
            ),
            verification_layers=(
                "caller-level tests",
                "type checks",
                "full tests",
                "package/API smoke",
            ),
            completion_obligations=(
                "all discoverable callers changed",
                "dynamic references resolved or declared",
                "compatibility decision documented",
            ),
        ),
        RepositoryTaskKind.STATE_CONCURRENCY_DEFECT: dict(
            legacy_task_type="bug_repair",
            risk_level="critical",
            max_files=40,
            max_tokens=80000,
            max_graph_hops=6,
            required_investigations=(
                "identify shared mutable state",
                "map synchronization boundary",
                "reproduce under load",
                "inspect transaction/process lifecycle",
            ),
            verification_layers=(
                "deterministic race test",
                "repeated stress test",
                "process/thread leak check",
                "full regression suite",
            ),
            completion_obligations=(
                "race invariant stated",
                "atomicity proven",
                "stress test repeated",
                "cleanup verified",
            ),
        ),
        RepositoryTaskKind.GENERAL_ENGINEERING: dict(
            legacy_task_type="feature_implementation",
            risk_level="medium",
            max_files=18,
            max_tokens=36000,
            max_graph_hops=4,
            required_investigations=("identify decisive files", "map impacted tests"),
            verification_layers=("targeted verification", "impacted tests", "diff review"),
            completion_obligations=("acceptance criteria evidenced",),
        ),
    }

    @classmethod
    def classify(cls, objective: str) -> TaskProfile:
        text = " ".join(str(objective).lower().split())
        scores: dict[RepositoryTaskKind, int] = {kind: 0 for kind in RepositoryTaskKind}
        signals: list[str] = []

        def add(kind: RepositoryTaskKind, weight: int, *terms: str) -> None:
            matched = [term for term in terms if term in text]
            if matched:
                scores[kind] += weight + len(matched) - 1
                signals.extend(matched)

        add(
            RepositoryTaskKind.STATE_CONCURRENCY_DEFECT,
            7,
            "race condition",
            "deadlock",
            "concurrency",
            "thread-safe",
            "lost update",
            "shared state",
            "atomic",
            "transaction isolation",
            "process leak",
        )
        add(
            RepositoryTaskKind.REPOSITORY_API_CHANGE,
            7,
            "repository-wide api",
            "all callers",
            "across the repository",
            "breaking api",
            "public api",
            "rename everywhere",
            "signature change",
        )
        add(
            RepositoryTaskKind.FRAMEWORK_MIGRATION,
            6,
            "framework migration",
            "migrate from",
            "upgrade framework",
            "major version",
            "deprecated api",
            "codemod",
            "schema migration",
        )
        add(
            RepositoryTaskKind.INDIRECT_TEST_FAILURE,
            5,
            "indirect cause",
            "test failure",
            "failing test",
            "fixture",
            "passes alone",
            "fails in suite",
            "hidden dependency",
        )
        add(
            RepositoryTaskKind.DIFFICULT_REFACTOR,
            5,
            "refactor",
            "extract module",
            "architecture",
            "decompose",
            "preserve behavior",
            "technical debt",
        )
        # Generic repairs are not automatically "hidden multi-file" work.
        # Reserve the expensive hard-task profile for evidence of indirect,
        # cross-file, intermittent, or otherwise non-local failure modes.
        add(
            RepositoryTaskKind.HIDDEN_MULTI_FILE_BUG,
            5,
            "hidden bug",
            "multi-file bug",
            "root cause",
            "intermittent",
            "unseen",
            "cross-file",
            "indirect dependency",
        )
        generic_repair = any(
            term in text
            for term in ("fix", "bug", "broken", "error", "failure", "regression", "repair")
        )
        add(
            RepositoryTaskKind.FEATURE_ADDITION,
            3,
            "implement",
            "add feature",
            "new endpoint",
            "new command",
            "support for",
            "build",
        )

        file_mentions = len(re.findall(r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.[A-Za-z0-9]+", text))
        if file_mentions >= 2 and any(term in text for term in ("fix", "bug", "error", "failure")):
            scores[RepositoryTaskKind.HIDDEN_MULTI_FILE_BUG] += 3
            signals.append("multiple explicit files")
        if any(term in text for term in ("all usages", "all references", "every caller")):
            scores[RepositoryTaskKind.REPOSITORY_API_CHANGE] += 4
            signals.append("global usage migration")

        kind = max(scores, key=lambda item: scores[item])
        if scores[kind] <= 0:
            kind = RepositoryTaskKind.GENERAL_ENGINEERING
        config = dict(cls._PROFILES[kind])
        if kind == RepositoryTaskKind.GENERAL_ENGINEERING and generic_repair:
            config["legacy_task_type"] = "bug_repair"
            signals.append("routine local repair")
        return TaskProfile(kind=kind, signals=tuple(dict.fromkeys(signals)), **config)

    @classmethod
    def refine(cls, current: TaskProfile, evidence: "EvidenceSignals") -> TaskProfile:
        """Refine an initial objective classification using runtime evidence.

        Objective text is necessarily incomplete for hidden failures. Runtime stack
        paths, migration diagnostics, test nodes, and concurrency symptoms are
        stronger signals and may promote the task to a stricter profile. The method
        never reduces the existing context or verification budget.
        """
        target = current.kind
        reasons = list(current.signals)
        failure_kinds = set(evidence.failure_kinds)
        migration_terms = set(evidence.migration_terms)

        if evidence.concurrency_terms or "timeout_or_deadlock" in failure_kinds:
            target = RepositoryTaskKind.STATE_CONCURRENCY_DEFECT
            reasons.append("runtime concurrency evidence")
        elif migration_terms:
            api_terms = {"api change", "breaking change", "rename", "compatibility layer"}
            if migration_terms.intersection(api_terms) and (
                evidence.symbols or len(evidence.paths) >= 3
            ):
                target = RepositoryTaskKind.REPOSITORY_API_CHANGE
                reasons.append("runtime repository-wide API evidence")
            else:
                target = RepositoryTaskKind.FRAMEWORK_MIGRATION
                reasons.append("runtime migration evidence")
        elif evidence.tests and (len(evidence.paths) >= 2 or evidence.modules or evidence.symbols):
            target = RepositoryTaskKind.INDIRECT_TEST_FAILURE
            reasons.append("test failure crosses production dependencies")
        elif len(evidence.paths) >= 3 or (
            evidence.high_uncertainty
            and failure_kinds.intersection(
                {"runtime_exception", "type_contract_failure", "data_integrity_failure"}
            )
        ):
            target = RepositoryTaskKind.HIDDEN_MULTI_FILE_BUG
            reasons.append("runtime multi-file failure evidence")

        target_config = dict(cls._PROFILES[target])
        risk_rank = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        risk_level = max(
            (current.risk_level, str(target_config["risk_level"])),
            key=lambda value: risk_rank.get(value, 1),
        )
        return TaskProfile(
            kind=target,
            legacy_task_type=str(target_config["legacy_task_type"]),
            risk_level=risk_level,
            max_files=max(current.max_files, int(target_config["max_files"])),
            max_tokens=max(current.max_tokens, int(target_config["max_tokens"])),
            max_graph_hops=max(current.max_graph_hops, int(target_config["max_graph_hops"])),
            required_investigations=tuple(
                dict.fromkeys(
                    [
                        *current.required_investigations,
                        *target_config["required_investigations"],
                    ]
                )
            ),
            verification_layers=tuple(
                dict.fromkeys(
                    [
                        *current.verification_layers,
                        *target_config["verification_layers"],
                    ]
                )
            ),
            completion_obligations=tuple(
                dict.fromkeys(
                    [
                        *current.completion_obligations,
                        *target_config["completion_obligations"],
                    ]
                )
            ),
            signals=tuple(
                dict.fromkeys(
                    [
                        *reasons,
                        *evidence.failure_kinds,
                        *evidence.concurrency_terms,
                        *evidence.migration_terms,
                    ]
                )
            ),
        )
