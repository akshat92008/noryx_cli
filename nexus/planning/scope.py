"""Mutation Scope & Blast Radius Estimator (Sprint 6)."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from nexus.intelligence.repository.engine import RepositoryIntelligence


@dataclass
class MutationScope:
    allowed_paths: List[str] = field(default_factory=list)
    read_only_paths: List[str] = field(default_factory=list)
    protected_paths: List[str] = field(default_factory=list)
    expected_files: List[str] = field(default_factory=list)
    possible_expansion_paths: List[str] = field(default_factory=list)
    maximum_files_modified: Optional[int] = 10

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> MutationScope:
        return cls(**data)


class ScopeEstimator:
    """Uses RepositoryIntelligence graph to compute tight blast radius for proposed changes."""

    def __init__(self, repo_intelligence: Optional[RepositoryIntelligence] = None):
        self.repo_intel = repo_intelligence or RepositoryIntelligence(Path.cwd())

    def estimate_scope(
        self, target_files: List[str], task_description: str = ""
    ) -> MutationScope:
        expected = set(target_files)
        expansion: Set[str] = set()
        read_only: Set[str] = set()
        protected = ["pyproject.toml", "setup.py", "SECURITY.md", "nexus/config/"]

        # Use repo graph callers & test relationships if available
        if hasattr(self.repo_intel, "graph") and self.repo_intel.graph:
            for tf in target_files:
                # Find callers or dependent files
                try:
                    deps = self.repo_intel.graph.get_dependencies(tf)
                    for d in deps:
                        read_only.add(d)

                    reverse_deps = self.repo_intel.graph.get_dependents(tf)
                    for rd in reverse_deps:
                        expansion.add(rd)
                except Exception:
                    pass

        allowed = list(expected.union(expansion))

        return MutationScope(
            allowed_paths=allowed,
            read_only_paths=list(read_only.difference(expected)),
            protected_paths=protected,
            expected_files=list(expected),
            possible_expansion_paths=list(expansion.difference(expected)),
            maximum_files_modified=max(len(allowed) + 2, 5),
        )
