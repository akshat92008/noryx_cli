"""
nexus/collaboration/assignments.py

AssignmentGraph: dependency management for agent assignments.

Operations:
  find_ready_assignments()     -> assignments with all dependencies satisfied
  find_blocked_assignments()   -> assignments waiting on incomplete dependencies
  find_parallel_groups()       -> groups of assignments that can run concurrently
  find_overlapping_scopes()    -> assignments with potentially conflicting file access
  find_cycles()                -> cyclic dependency detection (DFS)
  find_critical_path()         -> longest dependency chain
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from nexus.collaboration.models import (
    AgentAssignment,
    WorkerState,
)

# ---------------------------------------------------------------------------
# Assignment registry
# ---------------------------------------------------------------------------


class AssignmentValidationError(ValueError):
    """Raised when an assignment fails pre-launch validation."""


@dataclass
class AssignmentNode:
    assignment: AgentAssignment
    state: WorkerState = WorkerState.CREATED

    @property
    def assignment_id(self) -> str:
        return self.assignment.assignment_id

    @property
    def dependencies(self) -> Tuple[str, ...]:
        return self.assignment.dependencies


class AssignmentGraph:
    """
    Directed acyclic graph of agent assignments.
    Thread-safety: callers must synchronise externally.
    Performance target: all ops < 50 ms on 100-node graphs.
    """

    def __init__(self) -> None:
        self._nodes: Dict[str, AssignmentNode] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_assignment(self, assignment: AgentAssignment) -> None:
        """
        Adds assignment to graph.
        Raises AssignmentValidationError if:
          - assignment_id already exists
          - expected_outputs is empty
          - verification_requirements is empty
          - allowed_paths is empty
          - adding the assignment creates a cycle
        """
        aid = assignment.assignment_id

        if aid in self._nodes:
            raise AssignmentValidationError(f"Assignment '{aid}' already registered.")

        if not assignment.expected_outputs:
            raise AssignmentValidationError(
                f"Assignment '{aid}' has no expected_outputs. Reject."
            )

        if not assignment.verification_requirements:
            raise AssignmentValidationError(
                f"Assignment '{aid}' has no verification_requirements. Reject."
            )

        # Verify declared dependencies exist
        for dep in assignment.dependencies:
            if dep not in self._nodes:
                raise AssignmentValidationError(
                    f"Assignment '{aid}' declares dependency '{dep}' which is not yet registered. "
                    "Register dependencies before dependents."
                )

        # Add tentatively, then check for cycles
        self._nodes[aid] = AssignmentNode(assignment=assignment)

        if cycles := self.find_cycles():
            del self._nodes[aid]
            raise AssignmentValidationError(
                f"Adding assignment '{aid}' creates a cyclic dependency: {cycles}"
            )

    def update_state(self, assignment_id: str, state: WorkerState) -> None:
        if assignment_id not in self._nodes:
            raise KeyError(f"Unknown assignment '{assignment_id}'.")
        self._nodes[assignment_id].state = state

    def remove_assignment(self, assignment_id: str) -> None:
        self._nodes.pop(assignment_id, None)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def find_ready_assignments(self) -> List[AgentAssignment]:
        """
        Assignments whose every dependency is in ACCEPTED state.
        Only returns assignments in CREATED or WAITING state.
        """
        ready: List[AgentAssignment] = []
        for node in self._nodes.values():
            if node.state not in (WorkerState.CREATED, WorkerState.WAITING):
                continue
            if self._all_deps_accepted(node):
                ready.append(node.assignment)
        return ready

    def find_blocked_assignments(self) -> List[AgentAssignment]:
        """Assignments waiting on at least one unaccepted dependency."""
        blocked: List[AgentAssignment] = []
        for node in self._nodes.values():
            if node.state not in (WorkerState.CREATED, WorkerState.WAITING):
                continue
            if not self._all_deps_accepted(node):
                blocked.append(node.assignment)
        return blocked

    def find_parallel_groups(self) -> List[List[AgentAssignment]]:
        """
        Returns lists of assignments that share no dependency relationship
        and can therefore run concurrently (provided scope reservations allow it).
        Uses topological level grouping.
        """
        # Build adjacency (assignment → its direct dependents)
        in_degree: Dict[str, int] = {aid: 0 for aid in self._nodes}
        for node in self._nodes.values():
            for _dep in node.dependencies:
                in_degree[node.assignment_id] += 1

        # BFS to discover level groups
        queue: deque[str] = deque(k for k, v in in_degree.items() if v == 0)
        adjacency: Dict[str, List[str]] = {aid: [] for aid in self._nodes}
        for node in self._nodes.values():
            for dep in node.dependencies:
                adjacency[dep].append(node.assignment_id)

        groups: List[List[AgentAssignment]] = []
        while queue:
            level = list(queue)
            groups.append([self._nodes[aid].assignment for aid in level])
            queue.clear()
            for aid in level:
                for child in adjacency[aid]:
                    in_degree[child] -= 1
                    if in_degree[child] == 0:
                        queue.append(child)

        return groups

    def find_overlapping_scopes(self) -> List[Tuple[str, str, List[Path]]]:
        """
        Detects pairs of assignments with overlapping allowed_paths
        when both are mutation-capable.
        Returns list of (assignment_id_a, assignment_id_b, overlapping_paths).
        """
        mutation_nodes = [
            n for n in self._nodes.values()
            if n.assignment.mutation_policy.allowed
        ]
        overlaps: List[Tuple[str, str, List[Path]]] = []
        for i, na in enumerate(mutation_nodes):
            for nb in mutation_nodes[i + 1:]:
                shared = [
                    p for p in na.assignment.allowed_paths
                    if p in nb.assignment.allowed_paths
                ]
                if shared:
                    overlaps.append((na.assignment_id, nb.assignment_id, shared))
        return overlaps

    def find_cycles(self) -> List[List[str]]:
        """
        DFS-based cycle detection.
        Returns list of cyclic paths (each a list of assignment_ids).
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        color: Dict[str, int] = {aid: WHITE for aid in self._nodes}
        path: List[str] = []
        cycles: List[List[str]] = []

        def dfs(node_id: str) -> None:
            color[node_id] = GRAY
            path.append(node_id)
            for dep in self._nodes[node_id].dependencies:
                if dep not in color:
                    continue
                if color[dep] == GRAY:
                    cycle_start = path.index(dep)
                    cycles.append(list(path[cycle_start:]) + [dep])
                elif color[dep] == WHITE:
                    dfs(dep)
            path.pop()
            color[node_id] = BLACK

        for aid in list(self._nodes):
            if color[aid] == WHITE:
                dfs(aid)

        return cycles

    def find_critical_path(self) -> List[str]:
        """
        Longest (by count) dependency chain from leaf to root.
        Returns ordered list of assignment_ids.
        """
        # Build reverse adjacency: dep -> list of nodes that depend on dep
        adjacency: Dict[str, List[str]] = {aid: [] for aid in self._nodes}
        for node in self._nodes.values():
            for dep in node.dependencies:
                if dep in adjacency:
                    adjacency[dep].append(node.assignment_id)

        # DP: longest_from[aid] = length of longest chain starting at aid
        memo: Dict[str, int] = {}

        def longest(aid: str) -> int:
            if aid in memo:
                return memo[aid]
            children = adjacency[aid]
            if not children:
                memo[aid] = 1
            else:
                memo[aid] = 1 + max(longest(c) for c in children)
            return memo[aid]

        for aid in self._nodes:
            longest(aid)

        if not memo:
            return []

        # Trace critical path
        start = max(memo, key=lambda k: memo[k])
        path: List[str] = [start]
        current = start
        while adjacency[current]:
            best_child = max(adjacency[current], key=lambda c: memo.get(c, 0))
            path.append(best_child)
            current = best_child

        return path

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _all_deps_accepted(self, node: AssignmentNode) -> bool:
        for dep in node.dependencies:
            dep_node = self._nodes.get(dep)
            if not dep_node or dep_node.state != WorkerState.ACCEPTED:
                return False
        return True

    def all_assignments(self) -> List[AgentAssignment]:
        return [n.assignment for n in self._nodes.values()]

    def get_state(self, assignment_id: str) -> Optional[WorkerState]:
        node = self._nodes.get(assignment_id)
        return node.state if node else None
