"""Authoritative Repository Intelligence Engine for Noryx CLI — Sprint 5."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from nexus.intelligence.repository.adaptive import AdaptiveContextSelector
from nexus.intelligence.repository.budget import ContextBudgetManager
from nexus.intelligence.repository.classification import FileClassifier
from nexus.intelligence.repository.discovery import RepositoryDiscovery
from nexus.intelligence.repository.extraction import LanguageExtractor
from nexus.intelligence.repository.model import (
    ArchitectureBoundary,
    ContextBundle,
    ContextRelationship,
    ContextSymbol,
    RepositoryFile,
    RepositorySymbol,
    RiskAnnotation,
    RiskLevel,
    TestRelationship,
)
from nexus.intelligence.repository.ranking import ExplainableContextRanker, TaskIntentClassifier
from nexus.intelligence.repository.secrets import SecretProtector
from nexus.paths import nexus_home

GRAPH_SCHEMA_VERSION = "nexus.repograph.v5"


class RepositoryIntelligence:
    """Canonical repository intelligence system managing graph indexing and context selection."""

    def __init__(
        self,
        root: str | Path,
        *,
        state_root: str | Path | None = None,
        max_files: int = 10000,
    ):
        self.root = Path(root).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError(f"Repository root does not exist: {self.root}")

        digest = hashlib.sha256(str(self.root).encode("utf-8")).hexdigest()[:16]
        base = Path(state_root).expanduser().resolve() if state_root else nexus_home()
        self.cache_dir = base / "repo-graphs" / digest
        self.cache_path = self.cache_dir / "graph.json"
        self.max_files = max_files

        self.discovery = RepositoryDiscovery(self.root)
        self.extractor = LanguageExtractor()
        self.ranker = ExplainableContextRanker()
        self.budget_manager = ContextBudgetManager()

        self.files: dict[str, RepositoryFile] = {}
        self.tree_hash: str = ""
        self.generated_at: str = ""
        self._load_cache()

    def build(self, force: bool = False) -> dict[str, Any]:
        """Discover and index files incrementally or forcefully."""
        discovered_paths = self.discovery.discover_files(self.max_files)
        new_tree_hash = self.discovery.calculate_tree_hash(discovered_paths)

        if not force and self.tree_hash == new_tree_hash and self.files:
            return {"indexed": 0, "reused": len(self.files), "tree_hash": self.tree_hash}

        updated_files: dict[str, RepositoryFile] = {}
        indexed_count = 0
        reused_count = 0

        for path in discovered_paths:
            try:
                rel_path = path.relative_to(self.root).as_posix()
                stat = path.stat()
            except (ValueError, OSError):
                continue

            try:
                content_hash = self.discovery.calculate_file_hash(path)
            except OSError:
                continue
            prior = self.files.get(rel_path) if not force else None
            if prior and prior.content_hash == content_hash:
                # Keep diagnostics fresh without trusting metadata for validity.
                prior.mtime_ns = stat.st_mtime_ns
                prior.size_bytes = stat.st_size
                updated_files[rel_path] = prior
                reused_count += 1
                continue

            record = self._index_single_file(path, rel_path, stat, content_hash=content_hash)
            updated_files[rel_path] = record
            indexed_count += 1

        self.files = updated_files
        self.tree_hash = new_tree_hash
        self.generated_at = datetime.now(timezone.utc).isoformat()
        self._save_cache()

        return {
            "indexed": indexed_count,
            "reused": reused_count,
            "total": len(self.files),
            "tree_hash": self.tree_hash,
        }

    def update_paths(self, paths: Iterable[str | Path]) -> None:
        """Refresh index for specific mutated or added files."""
        for raw in paths:
            path = Path(raw).expanduser()
            if not path.is_absolute():
                path = self.root / path
            path = path.resolve()

            try:
                rel_path = path.relative_to(self.root).as_posix()
            except ValueError:
                continue

            if not path.exists() or not path.is_file():
                self.files.pop(rel_path, None)
                continue

            try:
                stat = path.stat()
                content_hash = self.discovery.calculate_file_hash(path)
                record = self._index_single_file(path, rel_path, stat, content_hash=content_hash)
                self.files[rel_path] = record
            except OSError:
                continue

        self.tree_hash = self.discovery.calculate_tree_hash([self.root / p for p in self.files])
        self.generated_at = datetime.now(timezone.utc).isoformat()
        self._save_cache()

    def context_bundle(
        self,
        query: str,
        *,
        explicit_files: list[str] | None = None,
        failing_stack_files: list[str] | None = None,
        max_files: int = 12,
        max_total_tokens: int = 24000,
        max_graph_hops: int = 3,
        candidate_multiplier: int = 3,
    ) -> ContextBundle:
        """Assemble a typed, explainable ContextBundle for model consumption."""
        if not self.files:
            self.build(force=False)

        task_intent = TaskIntentClassifier.classify(query)
        changed_git = self.git_changed_files()

        # Candidate generation and explainable ranking
        candidates = self.ranker.rank_candidates(
            query,
            self.files,
            changed_git,
            explicit_user_files=explicit_files,
            failing_stack_files=failing_stack_files,
            limit=max_files * max(2, int(candidate_multiplier)),
        )

        # Filter low-relevance candidates if decisive candidates exist.
        if candidates and candidates[0].score >= 20.0:
            top_score = candidates[0].score
            candidates = [c for c in candidates if c.score >= top_score * 0.3]

        adaptive = AdaptiveContextSelector(self.root, self.files).select(
            query,
            candidates,
            explicit_files=explicit_files or (),
            max_candidates=max(max_files * max(2, int(candidate_multiplier)), 18),
            max_hops=max(1, min(8, int(max_graph_hops))),
        )
        candidates = list(adaptive.candidates)

        search_terms = {t.lower() for t in query.split() if len(t) > 2}

        # Budget assembly
        self.budget_manager.max_files = max_files
        self.budget_manager.max_total_tokens = max_total_tokens

        selected_files, omitted_candidates, token_count = (
            self.budget_manager.assemble_context_files(
                candidates, self.files, self.root, search_terms
            )
        )

        # Related tests, symbols, and architecture constraints
        test_rels = self._build_test_relationships([f.path for f in selected_files])
        symbols = self._extract_context_symbols(selected_files)
        boundaries = self._infer_architecture_boundaries()
        risks = self._collect_risk_annotations(selected_files)

        rationales = {f.path: [f.selection_reason] for f in selected_files}

        adaptive_relationships = [
            ContextRelationship(
                source=source,
                target=target,
                relationship_type=relationship,
                description="Selected by deterministic repository graph propagation.",
            )
            for source, target, relationship in adaptive.relationships
            if source in self.files and target in self.files
        ]
        limitations = list(adaptive.coverage.limitations)
        confidence = adaptive.coverage.confidence if selected_files else 0.4
        return ContextBundle(
            task_intent=task_intent,
            repository_tree_hash=self.tree_hash,
            files=selected_files,
            symbols=symbols,
            relationships=adaptive_relationships,
            tests=test_rels,
            constraints=boundaries,
            risks=risks,
            estimated_tokens=token_count,
            confidence=confidence,
            limitations=limitations,
            omitted_candidates=omitted_candidates,
            selection_rationales=rationales,
        )

    def expand_context(
        self,
        bundle: ContextBundle,
        reason: str,
        additional_files: list[str] | None = None,
        *,
        evidence: object = None,
        risk_level: str = "medium",
    ) -> ContextBundle:
        """Expand from concrete failure evidence instead of a fixed file/token increment."""
        from nexus.intelligence.repository.evidence import EvidenceDrivenContextExpander

        return EvidenceDrivenContextExpander(self).expand(
            bundle,
            reason=reason,
            evidence=evidence,
            additional_files=additional_files or (),
            risk_level=risk_level,
        )

    def find_symbols(self, query: str, limit: int = 50) -> list[RepositorySymbol]:
        needle = query.strip().lower()
        if not needle:
            return []
        matches: list[RepositorySymbol] = []
        for file_record in self.files.values():
            for symbol in file_record.symbols:
                if needle in symbol.name.lower() or needle in symbol.qualified_name.lower():
                    matches.append(symbol)
        return matches[:limit]

    def find_callers(self, symbol_name: str, limit: int = 100) -> list[dict[str, Any]]:
        needle = symbol_name.strip()
        if not needle:
            return []
        results = []
        for file_record in self.files.values():
            if needle in file_record.references:
                results.append(
                    {
                        "path": file_record.path,
                        "language": file_record.language,
                        "is_test": file_record.is_test,
                    }
                )
        return sorted(results, key=lambda item: (not item["is_test"], item["path"]))[
            : max(1, limit)
        ]

    def resolve_import_targets(self, importer_path: str, import_name: str) -> list[str]:
        """Resolve a repository import to indexed file paths without executing code."""
        raw = import_name.strip().replace("\\", "/")
        if not raw:
            return []
        importer = Path(importer_path)
        candidates: list[str] = []

        if raw.startswith("."):
            dots = len(raw) - len(raw.lstrip("."))
            remainder = raw[dots:].replace(".", "/")
            base = importer.parent
            for _ in range(max(0, dots - 1)):
                base = base.parent
            prefix = (base / remainder).as_posix() if remainder else base.as_posix()
            candidates.extend((prefix, f"{prefix}.py", f"{prefix}/__init__.py"))
        else:
            normalized = raw.removeprefix("@/").removeprefix("~/")
            normalized = normalized.replace(".", "/") if "/" not in normalized else normalized
            normalized = normalized.lstrip("./")
            candidates.extend(
                (
                    normalized,
                    f"{normalized}.py",
                    f"{normalized}.pyi",
                    f"{normalized}/__init__.py",
                    f"{normalized}.js",
                    f"{normalized}.jsx",
                    f"{normalized}.ts",
                    f"{normalized}.tsx",
                    f"{normalized}/index.js",
                    f"{normalized}/index.ts",
                    f"{normalized}/index.tsx",
                )
            )
            relative = (importer.parent / raw).as_posix().lstrip("./")
            candidates.extend(
                (
                    relative,
                    f"{relative}.js",
                    f"{relative}.jsx",
                    f"{relative}.ts",
                    f"{relative}.tsx",
                    f"{relative}/index.js",
                    f"{relative}/index.ts",
                    f"{relative}/index.tsx",
                )
            )

        exact = [item for item in dict.fromkeys(candidates) if item in self.files]
        if exact:
            return exact

        # Package prefixes are useful for languages whose extractors preserve
        # package identifiers rather than exact paths. Keep this deterministic
        # and bounded to avoid fuzzy repository-wide matches.
        suffixes = tuple(item for item in dict.fromkeys(candidates) if item and len(item) >= 3)
        return sorted(
            path
            for path in self.files
            if any(path == suffix or path.endswith("/" + suffix) for suffix in suffixes)
        )[:8]

    def reverse_dependencies(
        self, paths: Iterable[str | Path], *, limit: int = 500
    ) -> list[dict[str, Any]]:
        """Return files that statically import any target path."""
        targets = {self._relative_key(path) for path in paths}
        results: list[dict[str, Any]] = []
        for record in self.files.values():
            matched_imports: list[str] = []
            matched_targets: set[str] = set()
            for import_name in record.imports:
                resolved = set(self.resolve_import_targets(record.path, import_name))
                overlap = targets.intersection(resolved)
                if overlap:
                    matched_imports.append(import_name)
                    matched_targets.update(overlap)
            if matched_targets:
                results.append(
                    {
                        "path": record.path,
                        "targets": sorted(matched_targets),
                        "imports": sorted(dict.fromkeys(matched_imports)),
                        "is_test": record.is_test,
                        "is_config": record.config_file,
                        "is_migration": record.migration_file,
                    }
                )
        return sorted(results, key=lambda item: (not item["is_test"], item["path"]))[
            : max(1, limit)
        ]

    def impact_closure(
        self,
        paths: Iterable[str | Path],
        *,
        symbols: Iterable[str] = (),
        max_hops: int = 6,
        limit: int = 500,
        include_tests: bool = True,
        include_configuration: bool = False,
    ) -> list[dict[str, Any]]:
        """Compute a bounded transitive impact closure with explicit reasons.

        The closure combines reverse imports, symbol references, impacted tests,
        and optional migration/configuration surfaces. It is used for completion
        obligations, not as a claim that every discovered file must be mutated.
        """
        seeds = {self._relative_key(path) for path in paths}
        seeds = {path for path in seeds if path in self.files}
        discovered: dict[str, dict[str, Any]] = {
            path: {
                "path": path,
                "depth": 0,
                "reasons": ["seed"],
                "is_test": self.files[path].is_test,
                "is_config": self.files[path].config_file,
                "is_migration": self.files[path].migration_file,
            }
            for path in seeds
        }
        frontier = set(seeds)
        visited = set(seeds)
        symbol_names = {item.strip().split(".")[-1] for item in symbols if item.strip()}

        for depth in range(1, max(1, min(8, int(max_hops))) + 1):
            if not frontier or len(discovered) >= limit:
                break
            next_frontier: set[str] = set()
            for dependency in self.reverse_dependencies(frontier, limit=limit):
                path = str(dependency["path"])
                reason = "reverse_import:" + ",".join(dependency["targets"])
                entry = discovered.setdefault(
                    path,
                    {
                        "path": path,
                        "depth": depth,
                        "reasons": [],
                        "is_test": bool(dependency["is_test"]),
                        "is_config": bool(dependency["is_config"]),
                        "is_migration": bool(dependency["is_migration"]),
                    },
                )
                if reason not in entry["reasons"]:
                    entry["reasons"].append(reason)
                if path not in visited and entry["depth"] == depth:
                    next_frontier.add(path)

            if depth == 1:
                for symbol in symbol_names:
                    for caller in self.find_callers(symbol, limit=limit):
                        path = str(caller["path"])
                        record = self.files.get(path)
                        if record is None:
                            continue
                        entry = discovered.setdefault(
                            path,
                            {
                                "path": path,
                                "depth": depth,
                                "reasons": [],
                                "is_test": record.is_test,
                                "is_config": record.config_file,
                                "is_migration": record.migration_file,
                            },
                        )
                        reason = f"symbol_reference:{symbol}"
                        if reason not in entry["reasons"]:
                            entry["reasons"].append(reason)
                        if path not in visited:
                            next_frontier.add(path)
            visited.update(next_frontier)
            frontier = next_frontier

        if include_tests:
            for path in self.impacted_tests(discovered, limit=limit):
                record = self.files[path]
                entry = discovered.setdefault(
                    path,
                    {
                        "path": path,
                        "depth": max_hops + 1,
                        "reasons": [],
                        "is_test": True,
                        "is_config": record.config_file,
                        "is_migration": record.migration_file,
                    },
                )
                if "impacted_test" not in entry["reasons"]:
                    entry["reasons"].append("impacted_test")

        if include_configuration:
            for record in self.files.values():
                if not (record.config_file or record.migration_file):
                    continue
                discovered.setdefault(
                    record.path,
                    {
                        "path": record.path,
                        "depth": max_hops + 1,
                        "reasons": [
                            "migration_surface"
                            if record.migration_file
                            else "configuration_surface"
                        ],
                        "is_test": record.is_test,
                        "is_config": record.config_file,
                        "is_migration": record.migration_file,
                    },
                )

        return sorted(
            discovered.values(),
            key=lambda item: (int(item["depth"]), not bool(item["is_test"]), item["path"]),
        )[: max(1, limit)]

    def impacted_tests(self, paths: Iterable[str | Path], limit: int = 100) -> list[str]:
        changed = {self._relative_key(path) for path in paths}
        frontier = set(changed)
        impacted = set()
        visited = set(changed)

        for _depth in range(4):
            next_frontier = set()
            frontier_stems = {Path(p).stem for p in frontier}
            for candidate in self.files.values():
                if candidate.path in visited:
                    continue
                deps = candidate.imports
                if any(
                    target in deps or any(target in dep for dep in deps)
                    for target in frontier_stems
                ):
                    if candidate.test_file or getattr(candidate, "is_test", False):
                        impacted.add(candidate.path)
                    else:
                        next_frontier.add(candidate.path)
                    visited.add(candidate.path)
            frontier = next_frontier
            if not frontier:
                break

        for path in changed:
            record = self.files.get(path)
            if record and (record.test_file or getattr(record, "is_test", False)):
                impacted.add(path)
        return sorted(impacted)[: max(1, limit)]

    def ownership(self, path: str | Path) -> list[str]:
        rel = self._relative_key(path)
        return self._owners_for(rel)

    def _owners_for(self, relative: str) -> list[str]:
        candidates = (
            self.root / ".github" / "CODEOWNERS",
            self.root / "CODEOWNERS",
            self.root / "docs" / "CODEOWNERS",
        )
        codeowners = next((path for path in candidates if path.is_file()), None)
        if not codeowners:
            return []
        owners: list[str] = []
        try:
            lines = codeowners.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        import fnmatch

        for raw in lines:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            pattern = parts[0].lstrip("/")
            normalized = pattern
            if pattern.endswith("/"):
                normalized += "**"
            if (
                fnmatch.fnmatch(relative, normalized)
                or fnmatch.fnmatch(relative, f"**/{normalized}")
                or fnmatch.fnmatch(f"/{relative}", pattern)
                or fnmatch.fnmatch(relative, pattern)
            ):
                owners = parts[1:]
        return owners

    def git_changed_files(self) -> list[str]:
        try:
            # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
            res = subprocess.run(
                ["git", "status", "--porcelain=v1", "--untracked-files=all"],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if res.returncode != 0:
                return []
            paths = []
            for line in res.stdout.splitlines():
                raw = line[3:].split(" -> ")[-1].strip()
                if raw:
                    paths.append(raw)
            return paths
        except (OSError, subprocess.TimeoutExpired):
            return []

    def summary(self) -> dict[str, Any]:
        total_symbols = sum(len(f.symbols) for f in self.files.values())
        tests_count = sum(1 for f in self.files.values() if f.is_test)
        configs_count = sum(1 for f in self.files.values() if f.config_file)
        return {
            "schema_version": GRAPH_SCHEMA_VERSION,
            "tree_hash": self.tree_hash,
            "root": str(self.root),
            "files_count": len(self.files),
            "symbols_count": total_symbols,
            "tests_count": tests_count,
            "configs_count": configs_count,
        }

    # ── Internal Helpers ──

    def _index_single_file(
        self,
        full_path: Path,
        rel_path: str,
        stat: os.stat_result,
        *,
        content_hash: str | None = None,
    ) -> RepositoryFile:
        classification = FileClassifier.classify(rel_path)

        try:
            source = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            source = ""
            classification["parse_error"] = str(exc)

        extracted = (
            self.extractor.extract(rel_path, source)
            if source
            else {
                "imports": [],
                "symbols": [],
                "references": [],
                "routes": [],
                "database_models": [],
                "parse_error": "",
            }
        )

        # Check for secrets
        _, has_secrets = SecretProtector.sanitize(source, rel_path)
        if has_secrets:
            classification["is_protected"] = True
            classification["risk_level"] = RiskLevel.HIGH

        return RepositoryFile(
            path=rel_path,
            language=classification.get("category"),
            size_bytes=stat.st_size,
            content_hash=content_hash or hashlib.sha256(full_path.read_bytes()).hexdigest(),
            mtime_ns=stat.st_mtime_ns,
            tracked=True,
            generated=classification["is_generated"],
            vendored=classification["is_vendored"],
            binary=classification["is_binary"],
            protected=classification["is_protected"],
            test_file=classification["is_test"],
            config_file=classification["is_config"],
            migration_file=classification["is_migration"],
            risk_level=classification["risk_level"],
            category=classification["category"],
            imports=extracted["imports"],
            exports=[
                s.name for s in extracted["symbols"] if s.kind in {"class", "function", "interface"}
            ],
            references=extracted.get("references", []),
            routes=extracted.get("routes", []),
            database_models=extracted.get("database_models", []),
            symbols=extracted["symbols"],
            parse_error=extracted["parse_error"] or classification.get("parse_error", ""),
        )

    def _build_test_relationships(self, selected_paths: list[str]) -> list[TestRelationship]:
        rels: list[TestRelationship] = []
        for path in selected_paths:
            for test_path in self.impacted_tests([path], limit=5):
                rels.append(
                    TestRelationship(
                        source_file=path,
                        test_file=test_path,
                        relationship_type="DIRECT_IMPORT",
                        confidence=0.9,
                        reason=f"{test_path} exercises {path}",
                    )
                )
        return rels

    def _extract_context_symbols(self, selected_files: Any) -> list[ContextSymbol]:
        ctx_symbols = []
        for ctx_file in selected_files:
            repo_file = self.files.get(ctx_file.path)
            if repo_file:
                for symbol in repo_file.symbols[:6]:
                    ctx_symbols.append(
                        ContextSymbol(
                            name=symbol.name,
                            kind=symbol.kind,
                            file_path=symbol.file_path,
                            line=symbol.line,
                            signature=symbol.signature,
                            relevance_reason=ctx_file.selection_reason,
                        )
                    )
        return ctx_symbols

    def _infer_architecture_boundaries(self) -> list[ArchitectureBoundary]:
        boundaries = [
            ArchitectureBoundary(
                layer_name="verification", files=[p for p in self.files if "verification" in p]
            ),
            ArchitectureBoundary(
                layer_name="provider", files=[p for p in self.files if "providers/" in p]
            ),
            ArchitectureBoundary(
                layer_name="execution", files=[p for p in self.files if "execution/" in p]
            ),
        ]
        return [b for b in boundaries if b.files]

    def _collect_risk_annotations(self, selected_files: Any) -> list[RiskAnnotation]:
        annotations = []
        for ctx_file in selected_files:
            repo_file = self.files.get(ctx_file.path)
            if repo_file and repo_file.risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                annotations.append(
                    RiskAnnotation(
                        path=ctx_file.path,
                        risk_level=repo_file.risk_level,
                        reasons=[f"High-risk component ({repo_file.category})"],
                    )
                )
        return annotations

    def _relative_key(self, path: str | Path) -> str:
        item = Path(path).expanduser()
        if item.is_absolute():
            try:
                return item.resolve().relative_to(self.root).as_posix()
            except ValueError:
                return item.as_posix()
        return item.as_posix().lstrip("./")

    def _save_cache(self) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": GRAPH_SCHEMA_VERSION,
            "tree_hash": self.tree_hash,
            "root": str(self.root),
            "generated_at": self.generated_at,
            "files": {
                p: {
                    **asdict(r),
                    "symbols": [asdict(s) for s in r.symbols],
                    "risk_level": r.risk_level.value
                    if isinstance(r.risk_level, RiskLevel)
                    else r.risk_level,
                }
                for p, r in sorted(self.files.items())
            },
        }
        tmp = self.cache_path.with_name(f".{self.cache_path.name}.{os.getpid()}.tmp")
        try:
            tmp.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
            )
            os.replace(tmp, self.cache_path)
        finally:
            tmp.unlink(missing_ok=True)

    def _load_cache(self) -> None:
        if not self.cache_path.is_file():
            return
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if payload.get("schema_version") != GRAPH_SCHEMA_VERSION:
            return
        self.tree_hash = str(payload.get("tree_hash", ""))
        self.generated_at = str(payload.get("generated_at", ""))
        for p, raw in payload.get("files", {}).items():
            try:
                symbols = [RepositorySymbol(**s) for s in raw.get("symbols", [])]
                risk = RiskLevel(raw.get("risk_level", "low"))
                raw_clean = {k: v for k, v in raw.items() if k not in {"symbols", "risk_level"}}
                self.files[p] = RepositoryFile(
                    symbols=symbols,
                    risk_level=risk,
                    **raw_clean,
                )
            except (TypeError, ValueError):
                continue
