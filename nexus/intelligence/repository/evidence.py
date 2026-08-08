"""Evidence-driven context expansion for hard repository tasks.

This module converts runtime failures, stack traces, test output, migration
messages, and concurrency symptoms into deterministic repository signals.  It
then derives an expansion budget from uncertainty rather than applying a fixed
"add N files" rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Iterable, Mapping

from nexus.intelligence.repository.model import ContextBundle, TaskIntent

if TYPE_CHECKING:  # pragma: no cover
    from nexus.intelligence.repository.engine import RepositoryIntelligence


_PATH_PATTERNS = (
    re.compile(r'File\s+["\'](?P<path>[^"\']+)["\'],\s*line\s+\d+'),
    re.compile(
        r"(?P<path>(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+\.(?:py|pyi|js|jsx|ts|tsx|go|rs|java|kt|rb|php|cs|cpp|c|h|hpp|json|ya?ml|toml|ini|cfg|sql|sh|md))(?::\d+(?::\d+)?)?"
    ),
)
_SYMBOL_PATTERNS = (
    re.compile(r"NameError:\s*name\s*['\"](?P<symbol>[A-Za-z_][A-Za-z0-9_]*)['\"]"),
    re.compile(
        r"AttributeError:.*?has no attribute\s*['\"](?P<symbol>[A-Za-z_][A-Za-z0-9_]*)['\"]"
    ),
    re.compile(r"cannot import name\s*['\"]?(?P<symbol>[A-Za-z_][A-Za-z0-9_]*)"),
    re.compile(
        r"(?:undefined|unresolved)\s+(?:name|symbol|reference)\s*[:=]?\s*['\"]?(?P<symbol>[A-Za-z_$][A-Za-z0-9_$]*)",
        re.I,
    ),
    re.compile(
        r"TypeError:\s*(?P<symbol>[A-Za-z_][A-Za-z0-9_.]*)\([^\n]*?(?:missing|required|unexpected keyword)"
    ),
)
_TEST_NODE = re.compile(
    r"(?P<path>(?:tests?|specs?)/[^\s:]+\.(?:py|js|jsx|ts|tsx))(?:::(?P<node>[^\s]+))?"
)
_IMPORT_PATTERN = re.compile(
    r"(?:ModuleNotFoundError|ImportError):\s*(?:No module named\s*)?['\"]?(?P<module>[A-Za-z0-9_./-]+)"
)
_CONFIG_PATTERN = re.compile(
    r"(?P<name>(?:pyproject\.toml|package\.json|tsconfig(?:\.[A-Za-z0-9_-]+)?\.json|go\.mod|Cargo\.toml|requirements[^\s/]*\.txt|[^\s/]+\.(?:ya?ml|toml|ini|cfg)))",
    re.I,
)


@dataclass(frozen=True)
class EvidenceSignals:
    paths: tuple[str, ...] = ()
    symbols: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    modules: tuple[str, ...] = ()
    configuration: tuple[str, ...] = ()
    failure_kinds: tuple[str, ...] = ()
    concurrency_terms: tuple[str, ...] = ()
    migration_terms: tuple[str, ...] = ()
    uncertainty_score: float = 0.0
    raw_excerpt: str = ""

    @property
    def high_uncertainty(self) -> bool:
        return self.uncertainty_score >= 0.65

    def query_terms(self) -> list[str]:
        return list(
            dict.fromkeys(
                [
                    *self.paths,
                    *self.symbols,
                    *self.modules,
                    *self.configuration,
                    *self.failure_kinds,
                    *self.concurrency_terms,
                    *self.migration_terms,
                ]
            )
        )


@dataclass(frozen=True)
class ExpansionBudget:
    max_files: int
    max_total_tokens: int
    max_graph_hops: int
    candidate_multiplier: int
    reason: str


class FailureEvidenceExtractor:
    """Extract repository-relevant signals without trusting model summaries."""

    @classmethod
    def extract(
        cls,
        evidence: str | Mapping[str, object] | Iterable[object] | None,
        *,
        repository_paths: Iterable[str] = (),
    ) -> EvidenceSignals:
        text = cls._flatten(evidence)
        known = {cls._normalize_path(item) for item in repository_paths}
        known.discard("")
        paths: list[str] = []
        for pattern in _PATH_PATTERNS:
            for match in pattern.finditer(text):
                normalized = cls._match_repository_path(match.group("path"), known)
                if normalized:
                    paths.append(normalized)
        tests: list[str] = []
        for match in _TEST_NODE.finditer(text):
            normalized = cls._match_repository_path(match.group("path"), known)
            if normalized:
                tests.append(normalized)
                paths.append(normalized)
        symbols = [
            match.group("symbol").split(".")[-1]
            for pattern in _SYMBOL_PATTERNS
            for match in pattern.finditer(text)
        ]
        modules = [match.group("module").strip("'\"") for match in _IMPORT_PATTERN.finditer(text)]
        configuration: list[str] = []
        for match in _CONFIG_PATTERN.finditer(text):
            name = match.group("name")
            candidates = sorted(
                path
                for path in known
                if PurePosixPath(path).name.lower() == PurePosixPath(name).name.lower()
            )
            configuration.extend(candidates or [name])

        lowered = text.lower()
        failure_kinds: list[str] = []
        kind_terms = {
            "assertion_failure": ("assertionerror", "assertion failed", "expected", "actual"),
            "import_failure": ("modulenotfounderror", "importerror", "cannot import"),
            "type_contract_failure": ("typeerror", "mypy", "pyright", "type mismatch"),
            "build_failure": ("build failed", "compilation failed", "syntaxerror", "linker"),
            "runtime_exception": ("traceback", "exception", "panic:", "segmentation fault"),
            "timeout_or_deadlock": ("timed out", "timeout", "deadlock", "hung"),
            "data_integrity_failure": (
                "constraint failed",
                "integrityerror",
                "duplicate key",
                "corrupt",
            ),
        }
        for kind, terms in kind_terms.items():
            if any(term in lowered for term in terms):
                failure_kinds.append(kind)

        concurrency_vocab = (
            "race condition",
            "deadlock",
            "lock contention",
            "lost update",
            "atomic",
            "thread",
            "asyncio",
            "concurrent",
            "concurrency",
            "transaction",
            "isolation level",
            "shared state",
            "mutex",
            "semaphore",
            "process leak",
            "connection pool",
        )
        migration_vocab = (
            "migration",
            "migrate",
            "deprecated",
            "deprecation",
            "breaking change",
            "schema",
            "codemod",
            "framework upgrade",
            "api change",
            "rename",
            "compatibility layer",
        )
        concurrency_terms = [item for item in concurrency_vocab if item in lowered]
        migration_terms = [item for item in migration_vocab if item in lowered]

        signal_count = (
            len(set(paths)) + len(set(symbols)) + len(set(modules)) + len(set(failure_kinds))
        )
        unresolved_penalty = 0.25 if not paths else 0.0
        no_symbol_penalty = 0.15 if not symbols and "error" in lowered else 0.0
        broad_penalty = (
            0.20
            if any(
                term in lowered
                for term in (
                    "repository-wide",
                    "all callers",
                    "across the repo",
                    "framework migration",
                )
            )
            else 0.0
        )
        uncertainty = min(
            1.0,
            0.2
            + unresolved_penalty
            + no_symbol_penalty
            + broad_penalty
            + (0.15 if signal_count <= 2 else 0.0),
        )
        return EvidenceSignals(
            paths=tuple(dict.fromkeys(paths)),
            symbols=tuple(dict.fromkeys(symbols)),
            tests=tuple(dict.fromkeys(tests)),
            modules=tuple(dict.fromkeys(modules)),
            configuration=tuple(dict.fromkeys(configuration)),
            failure_kinds=tuple(dict.fromkeys(failure_kinds)),
            concurrency_terms=tuple(dict.fromkeys(concurrency_terms)),
            migration_terms=tuple(dict.fromkeys(migration_terms)),
            uncertainty_score=round(uncertainty, 3),
            raw_excerpt=text[-4000:],
        )

    @classmethod
    def _flatten(cls, value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            return value
        if isinstance(value, Mapping):
            return "\n".join(f"{key}: {cls._flatten(item)}" for key, item in value.items())
        if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray)):
            return "\n".join(cls._flatten(item) for item in value)
        return str(value)

    @staticmethod
    def _normalize_path(path: str) -> str:
        raw = str(path).replace("\\", "/").strip().strip("'\"()[]{}:,;")
        if not raw:
            return ""
        while raw.startswith("./"):
            raw = raw[2:]
        if raw.startswith("/") or raw.startswith("../"):
            return raw
        return str(PurePosixPath(raw))

    @classmethod
    def _match_repository_path(cls, raw: str, known: set[str]) -> str:
        normalized = cls._normalize_path(raw)
        if normalized in known:
            return normalized
        if normalized.startswith("/"):
            matches = [path for path in known if normalized.endswith("/" + path)]
            return min(matches, key=len) if matches else ""
        suffix_matches = [
            path for path in known if path.endswith("/" + normalized) or path == normalized
        ]
        return min(suffix_matches, key=len) if suffix_matches else normalized if not known else ""


class ExpansionPolicy:
    """Derive a bounded expansion budget from failure evidence and task shape."""

    @classmethod
    def derive(
        cls,
        bundle: ContextBundle,
        signals: EvidenceSignals,
        *,
        risk_level: str = "medium",
    ) -> ExpansionBudget:
        intent = bundle.task_intent
        risk = str(risk_level).lower()
        broad_intents = {
            TaskIntent.MIGRATION,
            TaskIntent.REFACTOR,
            TaskIntent.DEPENDENCY_UPGRADE,
            TaskIntent.SECURITY_FIX,
            TaskIntent.PERFORMANCE_OPTIMIZATION,
        }
        base_growth = 6
        if intent in broad_intents:
            base_growth += 8
        if signals.concurrency_terms:
            base_growth += 8
        if signals.migration_terms:
            base_growth += 8
        base_growth += min(16, len(signals.paths) * 2 + len(signals.symbols) + len(signals.modules))
        if signals.high_uncertainty:
            base_growth += 6
        if risk in {"high", "critical"}:
            base_growth += 6

        max_files = min(
            64, max(len(bundle.files) + base_growth, 24 if intent in broad_intents else 18)
        )
        token_growth = max(12_000, base_growth * 1_500)
        max_tokens = min(128_000, max(bundle.estimated_tokens + token_growth, max_files * 2_000))
        max_hops = (
            5
            if intent in broad_intents or signals.concurrency_terms or signals.migration_terms
            else 4
        )
        if signals.high_uncertainty:
            max_hops = min(6, max_hops + 1)
        multiplier = 5 if max_hops >= 5 else 4
        reason = (
            f"intent={intent.value}; risk={risk}; signals={len(signals.query_terms())}; "
            f"uncertainty={signals.uncertainty_score:.2f}"
        )
        return ExpansionBudget(max_files, max_tokens, max_hops, multiplier, reason)


class EvidenceDrivenContextExpander:
    """Expand context using concrete runtime evidence and repository graph edges."""

    def __init__(self, repository: "RepositoryIntelligence"):
        self.repository = repository

    def expand(
        self,
        bundle: ContextBundle,
        *,
        reason: str,
        evidence: object = None,
        additional_files: Iterable[str] = (),
        risk_level: str = "medium",
    ) -> ContextBundle:
        signals = FailureEvidenceExtractor.extract(
            evidence or reason, repository_paths=self.repository.files
        )
        explicit = list(
            dict.fromkeys(
                [
                    *(item.path for item in bundle.files),
                    *signals.paths,
                    *signals.tests,
                    *signals.configuration,
                    *(str(item) for item in additional_files),
                ]
            )
        )
        budget = ExpansionPolicy.derive(bundle, signals, risk_level=risk_level)
        query_parts = [bundle.task_intent.value, reason, *signals.query_terms()]
        expanded = self.repository.context_bundle(
            query=" | ".join(str(item) for item in query_parts if str(item).strip()),
            explicit_files=explicit,
            failing_stack_files=list(signals.paths),
            max_files=budget.max_files,
            max_total_tokens=budget.max_total_tokens,
            max_graph_hops=budget.max_graph_hops,
            candidate_multiplier=budget.candidate_multiplier,
        )
        expanded.limitations = [
            *expanded.limitations,
            f"Expanded due to: {reason}",
            f"Evidence-driven expansion: {budget.reason}",
        ]
        if signals.high_uncertainty:
            expanded.limitations.append(
                "Failure evidence remains ambiguous; completion must require targeted reproduction before mutation."
            )
        for path in signals.paths:
            expanded.selection_rationales.setdefault(path, []).append(
                "Observed directly in runtime failure evidence."
            )
        return expanded
