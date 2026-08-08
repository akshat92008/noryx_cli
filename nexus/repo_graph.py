"""Persistent, incremental repository intelligence for Nexus CLI — Sprint 5.

Unified with nexus.intelligence.repository.engine.RepositoryIntelligence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from nexus.intelligence.repository.engine import RepositoryIntelligence
from nexus.intelligence.repository.model import RepositorySymbol, RepositoryFile, RiskLevel


@dataclass
class SymbolRecord:
    name: str
    kind: str
    path: str
    line: int
    qualified_name: str = ""


@dataclass
class FileRecord:
    path: str
    language: str
    mtime_ns: int
    size: int
    sha256: str
    imports: list[str] = field(default_factory=list)
    symbols: list[SymbolRecord] = field(default_factory=list)
    references: list[str] = field(default_factory=list)
    routes: list[str] = field(default_factory=list)
    database_models: list[str] = field(default_factory=list)
    configuration: bool = False
    owners: list[str] = field(default_factory=list)
    is_test: bool = False
    parse_error: str = ""


@dataclass
class IndexStats:
    scanned: int = 0
    indexed: int = 0
    reused: int = 0
    removed: int = 0
    parse_errors: int = 0


class RepoGraph:
    """Incremental repository graph wrapping RepositoryIntelligence for backward compatibility."""

    def __init__(
        self,
        root: str | Path,
        *,
        state_root: str | Path | None = None,
        max_files: int = 5000,
    ):
        self.engine = RepositoryIntelligence(root, state_root=state_root, max_files=max_files)
        self.root = self.engine.root
        self.cache_dir = self.engine.cache_dir
        self.cache_path = self.engine.cache_path
        self.max_files = max_files
        self._sync_files()

    def _sync_files(self) -> None:
        """Expose self.files dict matching legacy FileRecord interfaces."""
        legacy: dict[str, FileRecord] = {}
        for path, repo_file in self.engine.files.items():
            legacy_symbols = [
                SymbolRecord(
                    name=s.name,
                    kind=s.kind,
                    path=s.file_path,
                    line=s.line,
                    qualified_name=s.qualified_name,
                )
                for s in repo_file.symbols
            ]
            legacy[path] = FileRecord(
                path=repo_file.path,
                language=repo_file.language or "unknown",
                mtime_ns=repo_file.mtime_ns,
                size=repo_file.size_bytes,
                sha256=repo_file.content_hash,
                imports=repo_file.imports,
                symbols=legacy_symbols,
                references=repo_file.references,
                routes=repo_file.routes,
                database_models=repo_file.database_models,
                is_test=repo_file.test_file,
                configuration=repo_file.config_file,
                parse_error=repo_file.parse_error,
            )
        self.files = legacy

    def build(self, *, force: bool = False) -> IndexStats:
        stats_dict = self.engine.build(force=force)
        self._sync_files()
        return IndexStats(
            scanned=stats_dict.get("total", len(self.files)),
            indexed=stats_dict.get("indexed", 0),
            reused=stats_dict.get("reused", 0),
        )

    def update_paths(self, paths: Iterable[str | Path]) -> IndexStats:
        self.engine.update_paths(paths)
        self._sync_files()
        return IndexStats(scanned=len(list(paths)), indexed=len(list(paths)))

    def find_symbols(self, query: str, *, limit: int = 50) -> list[SymbolRecord]:
        symbols = self.engine.find_symbols(query, limit=limit)
        return [
            SymbolRecord(name=s.name, kind=s.kind, path=s.file_path, line=s.line, qualified_name=s.qualified_name)
            for s in symbols
        ]

    def find_callers(self, symbol: str, *, limit: int = 100) -> list[dict[str, Any]]:
        return self.engine.find_callers(symbol, limit=limit)

    def dependencies(self, path: str | Path) -> dict[str, list[str]]:
        rel = self.engine._relative_key(path)
        rf = self.engine.files.get(rel)
        imports = list(rf.imports) if rf else []
        imported_by = [
            other.path for other in self.engine.files.values()
            if other.path != rel and any(rel in imp or Path(rel).stem in imp for imp in other.imports)
        ]
        return {"imports": sorted(set(imports)), "imported_by": sorted(set(imported_by))}

    def impacted_tests(self, paths: Iterable[str | Path], *, limit: int = 100) -> list[str]:
        return self.engine.impacted_tests(paths, limit=limit)

    def relevant_files(self, query: str, *, limit: int = 40) -> list[dict[str, Any]]:
        bundle = self.engine.context_bundle(query, max_files=limit)
        return [
            {"path": f.path, "score": 0.9, "reasons": [f.selection_reason]}
            for f in bundle.files
        ]

    def routes(self, query: str = "") -> list[dict[str, Any]]:
        needle = query.lower().strip()
        results = []
        for record in self.files.values():
            for route in record.routes:
                if needle and needle not in route.lower():
                    continue
                results.append({"path": record.path, "route": route, "language": record.language})
        return sorted(results, key=lambda x: (x["route"], x["path"]))

    def models(self, query: str = "") -> list[dict[str, Any]]:
        needle = query.lower().strip()
        results = []
        for record in self.files.values():
            for model in record.database_models:
                if needle and needle not in model.lower():
                    continue
                results.append({"path": record.path, "model": model, "language": record.language})
        return sorted(results, key=lambda x: (x["model"], x["path"]))

    def frameworks(self) -> list[str]:
        imports = {imp.lower() for f in self.files.values() for imp in f.imports}
        detected = []
        if any("fastapi" in imp for imp in imports):
            detected.append("FastAPI")
        if any("flask" in imp for imp in imports):
            detected.append("Flask")
        if any("django" in imp for imp in imports):
            detected.append("Django")
        if any("react" in imp for imp in imports):
            detected.append("React")
        return detected

    def ownership(self, path: str | Path) -> list[str]:
        return self.engine.ownership(path)

    def context_bundle(
        self,
        query: str,
        *,
        max_files: int = 12,
        max_chars: int = 36_000,
        lines_per_file: int = 120,
    ) -> str:
        bundle = self.engine.context_bundle(
            query,
            max_files=max_files,
            max_total_tokens=max_chars // 4,
        )
        return bundle.to_formatted_prompt()

    def git_changed_files(self, *, limit: int = 200) -> list[str]:
        return self.engine.git_changed_files()[:limit]

    def summary(self) -> dict[str, Any]:
        return self.engine.summary()
