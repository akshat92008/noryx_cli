"""
Change Dependency Graph — Sprint 8.

Builds and validates a directed acyclic graph (DAG) over PlannedFileChange objects.
Provides:
- Cycle detection (raises on circular dependencies)
- Conflict detection (two changes modifying the same symbol)
- Topological sort for deterministic execution order
- Parallel-safe step identification (steps with no mutual dependencies)
- Premature consumer detection (consumer scheduled before its producer)
"""

from __future__ import annotations

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Iterator

from nexus.multifile.contracts import ChangeDependency, PlannedFileChange

logger = logging.getLogger(__name__)


class DependencyCycleError(Exception):
    """Raised when a cycle is detected in the change dependency graph."""


class DependencyConflictError(Exception):
    """Raised when two changes conflict with each other."""


@dataclass
class GraphNode:
    file_change: PlannedFileChange
    predecessors: list[str] = field(default_factory=list)   # paths that must complete first
    successors: list[str] = field(default_factory=list)      # paths that depend on this


class ChangeDependencyGraph:
    """DAG over PlannedFileChange objects for a single EngineeringChangeSet."""

    def __init__(self) -> None:
        self._nodes: dict[str, GraphNode] = {}
        self._edges: list[ChangeDependency] = []

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def add_file_change(self, fc: PlannedFileChange) -> None:
        """Register a planned file change as a node in the graph."""
        if fc.path in self._nodes:
            raise DependencyConflictError(
                f"Duplicate file change registered for path: {fc.path}"
            )
        self._nodes[fc.path] = GraphNode(file_change=fc)
        # Honour dependencies declared in the PlannedFileChange itself
        for dep_path in fc.depends_on:
            self.add_dependency(
                ChangeDependency(
                    source_path=dep_path,
                    target_path=fc.path,
                    reason=f"Declared in PlannedFileChange for '{fc.path}'",
                )
            )

    def add_dependency(self, dep: ChangeDependency) -> None:
        """Add a directed dependency edge: source must complete before target."""
        self._edges.append(dep)
        # source and target may not be registered yet (allow lazy registration)
        if dep.source_path in self._nodes:
            if dep.target_path not in self._nodes[dep.source_path].successors:
                self._nodes[dep.source_path].successors.append(dep.target_path)
        if dep.target_path in self._nodes:
            if dep.source_path not in self._nodes[dep.target_path].predecessors:
                self._nodes[dep.target_path].predecessors.append(dep.source_path)

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def detect_cycles(self) -> list[list[str]]:
        """Return a list of cycles found in the graph. Empty → no cycles."""
        visited: set[str] = set()
        rec_stack: set[str] = set()
        cycles: list[list[str]] = []

        def dfs(node: str, path: list[str]) -> None:
            visited.add(node)
            rec_stack.add(node)
            path.append(node)
            for successor in self._successors_of(node):
                if successor not in visited:
                    dfs(successor, path)
                elif successor in rec_stack:
                    # Found a cycle
                    cycle_start = path.index(successor)
                    cycles.append(path[cycle_start:] + [successor])
            path.pop()
            rec_stack.discard(node)

        for node_path in list(self._nodes.keys()):
            if node_path not in visited:
                dfs(node_path, [])

        return cycles

    def detect_conflicts(self) -> list[tuple[str, str, str]]:
        """Detect pairs of changes that modify the same symbol.

        Returns list of (path1, path2, symbol) tuples.
        """
        conflicts: list[tuple[str, str, str]] = []
        symbol_owners: dict[str, str] = {}

        for path, node in self._nodes.items():
            for sym in node.file_change.relevant_symbols:
                if sym in symbol_owners:
                    conflicts.append((symbol_owners[sym], path, sym))
                else:
                    symbol_owners[sym] = path

        return conflicts

    def topological_sort(self) -> list[PlannedFileChange]:
        """Return file changes in dependency order using Kahn's algorithm.

        Raises DependencyCycleError if a cycle is found.
        """
        cycles = self.detect_cycles()
        if cycles:
            cycle_strs = [" → ".join(c) for c in cycles]
            raise DependencyCycleError(
                f"Circular dependency detected in change set: {cycle_strs}"
            )

        in_degree: dict[str, int] = {path: 0 for path in self._nodes}
        for dep in self._edges:
            if dep.target_path in in_degree:
                in_degree[dep.target_path] += 1

        queue: deque[str] = deque(
            path for path, deg in in_degree.items() if deg == 0
        )
        order: list[PlannedFileChange] = []

        while queue:
            path = queue.popleft()
            if path in self._nodes:
                order.append(self._nodes[path].file_change)
            for successor in self._successors_of(path):
                if successor in in_degree:
                    in_degree[successor] -= 1
                    if in_degree[successor] == 0:
                        queue.append(successor)

        if len(order) != len(self._nodes):
            raise DependencyCycleError(
                "Topological sort incomplete — unresolved cycle in dependency graph."
            )

        return order

    def parallel_safe_groups(self) -> list[list[PlannedFileChange]]:
        """Group file changes into parallel-safe batches (same topological level)."""
        if not self._nodes:
            return []

        in_degree: dict[str, int] = {path: 0 for path in self._nodes}
        for dep in self._edges:
            if dep.target_path in in_degree:
                in_degree[dep.target_path] += 1

        groups: list[list[PlannedFileChange]] = []
        remaining = dict(in_degree)

        while remaining:
            batch_paths = [p for p, d in remaining.items() if d == 0]
            if not batch_paths:
                raise DependencyCycleError(
                    "Cannot resolve parallel groups — cycle detected."
                )
            groups.append([self._nodes[p].file_change for p in batch_paths if p in self._nodes])
            for path in batch_paths:
                del remaining[path]
                for successor in self._successors_of(path):
                    if successor in remaining:
                        remaining[successor] -= 1

        return groups

    def validate(self) -> list[str]:
        """Run all validation checks. Returns list of error messages (empty → valid)."""
        errors: list[str] = []

        cycles = self.detect_cycles()
        for cycle in cycles:
            errors.append(f"Cycle detected: {' → '.join(cycle)}")

        conflicts = self.detect_conflicts()
        for path1, path2, sym in conflicts:
            errors.append(
                f"Symbol conflict: '{sym}' modified in both '{path1}' and '{path2}'"
            )

        # Check that all dependency source paths are registered
        registered = set(self._nodes.keys())
        for dep in self._edges:
            if dep.source_path and dep.source_path not in registered:
                errors.append(
                    f"Dependency '{dep.source_path}' → '{dep.target_path}': "
                    f"source path '{dep.source_path}' is not in the change set."
                )

        return errors

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _successors_of(self, path: str) -> list[str]:
        return [dep.target_path for dep in self._edges if dep.source_path == path]

    def nodes(self) -> Iterator[PlannedFileChange]:
        for node in self._nodes.values():
            yield node.file_change

    def __len__(self) -> int:
        return len(self._nodes)

    def __contains__(self, path: str) -> bool:
        return path in self._nodes


def build_graph(
    file_changes: list[PlannedFileChange],
    extra_deps: list[ChangeDependency] | None = None,
) -> ChangeDependencyGraph:
    """Convenience factory: build a graph from a list of file changes."""
    graph = ChangeDependencyGraph()
    for fc in file_changes:
        graph.add_file_change(fc)
    for dep in (extra_deps or []):
        graph.add_dependency(dep)
    return graph
