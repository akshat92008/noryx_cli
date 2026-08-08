"""Graph-propagated, risk-aware repository context selection.

The ranker produces local relevance.  This layer expands only through explicit
repository relationships so callers, tests, configuration and risk boundaries
are not omitted merely because their filenames do not match the prompt.
"""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Iterable

from nexus.intelligence.repository.model import ContextCandidate, RepositoryFile, RiskLevel


@dataclass(frozen=True)
class ContextCoverage:
    explicit_files: tuple[str, ...] = ()
    decisive_files: tuple[str, ...] = ()
    callers: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    reverse_dependencies: tuple[str, ...] = ()
    related_tests: tuple[str, ...] = ()
    configuration: tuple[str, ...] = ()
    risk_boundaries: tuple[str, ...] = ()
    graph_hops_used: int = 0
    confidence: float = 0.0
    limitations: tuple[str, ...] = ()


@dataclass(frozen=True)
class AdaptiveSelection:
    candidates: tuple[ContextCandidate, ...]
    coverage: ContextCoverage
    relationships: tuple[tuple[str, str, str], ...] = ()


class AdaptiveContextSelector:
    """Deterministic breadth-bounded propagation over repository evidence."""

    _IMPORT_SUFFIXES = (".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs")

    def __init__(self, root, files: dict[str, RepositoryFile]):
        self.root = root
        self.files = files
        self._symbol_owners: dict[str, set[str]] = defaultdict(set)
        self._imports: dict[str, set[str]] = defaultdict(set)
        self._reverse: dict[str, set[str]] = defaultdict(set)
        self._references: dict[str, set[str]] = defaultdict(set)
        self._build_graph()

    def select(
        self,
        query: str,
        candidates: Iterable[ContextCandidate],
        *,
        explicit_files: Iterable[str] = (),
        max_candidates: int = 24,
        max_hops: int = 3,
    ) -> AdaptiveSelection:
        max_candidates = max(1, int(max_candidates))
        explicit = [self._normalize_path(item) for item in explicit_files]
        explicit = [item for item in explicit if item in self.files]
        seed_map: dict[str, ContextCandidate] = {}
        for item in candidates:
            path = self._normalize_path(item.path)
            if path not in self.files:
                continue
            prior = seed_map.get(path)
            if prior is None or item.score > prior.score:
                seed_map[path] = item
        for path in explicit:
            seed_map[path] = ContextCandidate(
                path=path,
                source_signal="user_explicit",
                relationship="explicit",
                confidence=1.0,
                estimated_tokens=max(1, self.files[path].size_bytes // 4),
                risk=self.files[path].risk_level,
                reasons=["User explicitly named this repository file."],
                score=max(100.0, seed_map.get(path, ContextCandidate(path, "", "", 0, 0)).score),
            )
        if not seed_map:
            return AdaptiveSelection(
                (), ContextCoverage(limitations=("No repository-backed seed candidate was found.",))
            )

        selected: dict[str, ContextCandidate] = dict(seed_map)
        relationships: set[tuple[str, str, str]] = set()
        categories: dict[str, set[str]] = defaultdict(set)
        for path in explicit:
            categories["explicit"].add(path)
        for path in seed_map:
            categories["decisive"].add(path)

        queue = deque((path, 0, max(1.0, candidate.score)) for path, candidate in seed_map.items())
        visited_depth: dict[str, int] = {path: 0 for path in seed_map}
        max_used = 0
        while queue and len(selected) < max_candidates:
            source, depth, source_score = queue.popleft()
            if depth >= max_hops:
                continue
            for target, relation, weight, category in self._neighbors(source, query):
                if target not in self.files or target == source:
                    continue
                next_depth = depth + 1
                prior_depth = visited_depth.get(target)
                if prior_depth is not None and prior_depth <= next_depth:
                    relationships.add((source, target, relation))
                    continue
                visited_depth[target] = next_depth
                max_used = max(max_used, next_depth)
                confidence = max(0.35, min(0.98, weight * (0.92**depth)))
                score = max(1.0, source_score * weight * (0.82**depth))
                record = self.files[target]
                reason = f"{relation} from {source}"
                existing = selected.get(target)
                candidate = ContextCandidate(
                    path=target,
                    source_signal=relation,
                    relationship=relation,
                    confidence=confidence,
                    estimated_tokens=max(1, record.size_bytes // 4),
                    risk=record.risk_level,
                    reasons=list(dict.fromkeys([*(existing.reasons if existing else []), reason])),
                    score=max(score, existing.score if existing else 0.0),
                )
                selected[target] = candidate
                categories[category].add(target)
                relationships.add((source, target, relation))
                queue.append((target, next_depth, score))
                if len(selected) >= max_candidates:
                    break

        ranked = sorted(
            selected.values(),
            key=lambda item: (
                item.path not in explicit,
                -self._risk_rank(item.risk),
                -item.score,
                item.path,
            ),
        )[:max_candidates]
        selected_paths = {item.path for item in ranked}
        related_tests = {path for path in categories["tests"] if path in selected_paths}
        high_risk_seeds = {
            path
            for path in selected_paths
            if self.files[path].risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
        }
        confidence = self._confidence(
            selected_paths,
            explicit=explicit,
            tests=related_tests,
            high_risk=high_risk_seeds,
            relationships=relationships,
        )
        limitations: list[str] = []
        if len(selected) > len(ranked):
            limitations.append(
                f"Context cap omitted {len(selected) - len(ranked)} graph candidates."
            )
        if (
            any(self.files[path].test_file is False for path in selected_paths)
            and not related_tests
        ):
            limitations.append("No repository-backed related test was selected.")
        return AdaptiveSelection(
            candidates=tuple(ranked),
            coverage=ContextCoverage(
                explicit_files=tuple(sorted(set(explicit).intersection(selected_paths))),
                decisive_files=tuple(sorted(categories["decisive"].intersection(selected_paths))),
                callers=tuple(sorted(categories["callers"].intersection(selected_paths))),
                dependencies=tuple(sorted(categories["dependencies"].intersection(selected_paths))),
                reverse_dependencies=tuple(
                    sorted(categories["reverse"].intersection(selected_paths))
                ),
                related_tests=tuple(sorted(related_tests)),
                configuration=tuple(
                    sorted(categories["configuration"].intersection(selected_paths))
                ),
                risk_boundaries=tuple(sorted(categories["risk"].intersection(selected_paths))),
                graph_hops_used=max_used,
                confidence=confidence,
                limitations=tuple(limitations),
            ),
            relationships=tuple(sorted(relationships)),
        )

    def _build_graph(self) -> None:
        for path, record in self.files.items():
            for symbol in record.symbols:
                self._symbol_owners[symbol.name].add(path)
                if symbol.qualified_name:
                    self._symbol_owners[symbol.qualified_name].add(path)
            for raw in record.imports:
                target = self._resolve_import(path, raw)
                if target:
                    self._imports[path].add(target)
                    self._reverse[target].add(path)
            for reference in record.references:
                self._references[reference].add(path)

    def _neighbors(self, path: str, query: str):
        record = self.files[path]
        emitted: set[tuple[str, str]] = set()

        def emit(target: str, relation: str, weight: float, category: str):
            key = (target, relation)
            if key in emitted:
                return
            emitted.add(key)
            yield target, relation, weight, category

        for target in sorted(self._imports.get(path, ())):
            yield from emit(target, "direct_import", 0.84, "dependencies")
        for target in sorted(self._reverse.get(path, ())):
            target_record = self.files[target]
            category = "tests" if target_record.test_file else "callers"
            relation = "related_test" if target_record.test_file else "reverse_import"
            yield from emit(target, relation, 0.88 if target_record.test_file else 0.80, category)

        owned_symbols = {item.name for item in record.symbols}
        for symbol in sorted(owned_symbols):
            for target in sorted(self._references.get(symbol, ())):
                if target == path:
                    continue
                target_record = self.files[target]
                category = "tests" if target_record.test_file else "callers"
                relation = (
                    "test_symbol_reference" if target_record.test_file else "symbol_reference"
                )
                yield from emit(
                    target, relation, 0.90 if target_record.test_file else 0.82, category
                )

        package = PurePosixPath(path).parent.as_posix()
        for target, target_record in self.files.items():
            if target == path:
                continue
            if target_record.test_file and self._test_matches(path, target):
                yield from emit(target, "test_mapping", 0.86, "tests")
            if target_record.config_file and self._config_matches(
                record, target_record, package, query
            ):
                yield from emit(target, "configuration_boundary", 0.62, "configuration")
            if (
                record.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
                and target_record.risk_level in {RiskLevel.HIGH, RiskLevel.CRITICAL}
                and PurePosixPath(target).parent == PurePosixPath(path).parent
            ):
                yield from emit(target, "risk_boundary", 0.55, "risk")

    def _resolve_import(self, source_path: str, raw_import: str) -> str | None:
        raw = str(raw_import or "").strip()
        if not raw:
            return None
        source = PurePosixPath(source_path)
        candidates: list[str] = []
        if raw.startswith(("./", "../")):
            base = posixpath.normpath(f"{source.parent.as_posix()}/{raw}")
            if base == ".." or base.startswith("../") or base.startswith("/"):
                return None
            candidates.extend(self._module_candidates(base))
        elif raw.startswith("."):
            level = len(raw) - len(raw.lstrip("."))
            module = raw[level:].replace(".", "/")
            parent = source.parent
            for _ in range(max(0, level - 1)):
                parent = parent.parent
            candidates.extend(self._module_candidates(f"{parent.as_posix()}/{module}".rstrip("/")))
        else:
            candidates.extend(self._module_candidates(raw.replace(".", "/")))
        tail = self._normalize_path(raw.replace(".", "/"))
        if tail:
            for path in self.files:
                stem = str(PurePosixPath(path).with_suffix(""))
                if (
                    stem == tail
                    or stem.endswith("/" + tail)
                    or path.endswith("/" + tail + "/__init__.py")
                ):
                    candidates.append(path)
        for candidate in candidates:
            normalized = self._normalize_path(candidate)
            if normalized in self.files:
                return normalized
        return None

    def _module_candidates(self, base: str) -> list[str]:
        base = self._normalize_path(base)
        if not base:
            return []
        result = [base]
        if PurePosixPath(base).suffix:
            return result
        result.extend(base + suffix for suffix in self._IMPORT_SUFFIXES)
        result.extend(f"{base}/__init__{suffix}" for suffix in (".py", ".pyi"))
        result.extend(f"{base}/index{suffix}" for suffix in (".js", ".jsx", ".ts", ".tsx"))
        return result

    @staticmethod
    def _test_matches(source: str, candidate: str) -> bool:
        source_stem = PurePosixPath(source).stem.lower()
        test_stem = PurePosixPath(candidate).stem.lower()
        normalized = re.sub(r"^(test_|spec_)|(_test|_spec)$", "", test_stem)
        return source_stem == normalized or source_stem in test_stem

    @staticmethod
    def _config_matches(
        source: RepositoryFile, target: RepositoryFile, package: str, query: str
    ) -> bool:
        config_name = PurePosixPath(target.path).name.lower()
        if config_name in {
            "pyproject.toml",
            "package.json",
            "tsconfig.json",
            "go.mod",
            "cargo.toml",
        }:
            return True
        terms = {part.lower() for part in re.findall(r"[A-Za-z0-9_]+", query) if len(part) > 3}
        haystack = " ".join([source.path, package, *source.imports, *source.references]).lower()
        return any(term in haystack or term in config_name for term in terms)

    @staticmethod
    def _risk_rank(level: RiskLevel) -> int:
        return {
            RiskLevel.CRITICAL: 4,
            RiskLevel.HIGH: 3,
            RiskLevel.MEDIUM: 2,
            RiskLevel.LOW: 1,
        }.get(level, 0)

    @staticmethod
    def _confidence(selected, *, explicit, tests, high_risk, relationships) -> float:
        if not selected:
            return 0.0
        score = 0.45
        if set(explicit).issubset(selected):
            score += 0.15
        if relationships:
            score += min(0.20, len(relationships) * 0.015)
        if tests:
            score += 0.12
        if high_risk:
            score += 0.05
        return round(min(0.98, score), 3)

    @staticmethod
    def _normalize_path(path: str) -> str:
        raw = str(path).replace("\\", "/").strip()
        if not raw:
            return ""
        normalized = posixpath.normpath(raw)
        if (
            normalized in {"", ".", ".."}
            or normalized.startswith("../")
            or normalized.startswith("/")
        ):
            return ""
        return str(PurePosixPath(normalized))
