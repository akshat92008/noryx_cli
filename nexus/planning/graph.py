"""Plan Dependency Graph and Step Ordering Engine (Sprint 6)."""

from __future__ import annotations

from typing import Dict, List, Set

from nexus.planning.engineering_plan import EngineeringPlan, PlanStep


class PlanDependencyGraph:
    """Builds topological execution graphs, detects cycles, and assesses parallelization safety."""

    def __init__(self, plan: EngineeringPlan):
        self.plan = plan
        self.steps_by_id: Dict[str, PlanStep] = {s.step_id: s for s in plan.steps}

    def detect_cycles(self) -> List[List[str]]:
        """Find any dependency cycles in the plan steps using Tarjan / DFS."""
        visited: Dict[str, int] = {}  # 0: unvisited, 1: visiting, 2: visited
        cycles: List[List[str]] = []
        path: List[str] = []

        def dfs(node_id: str):
            visited[node_id] = 1
            path.append(node_id)

            step = self.steps_by_id.get(node_id)
            if step:
                for dep in step.dependencies:
                    if dep not in self.steps_by_id:
                        continue
                    state = visited.get(dep, 0)
                    if state == 1:
                        # Found cycle
                        cycle_start = path.index(dep)
                        cycles.append(path[cycle_start:] + [dep])
                    elif state == 0:
                        dfs(dep)

            path.pop()
            visited[node_id] = 2

        for step_id in self.steps_by_id:
            if visited.get(step_id, 0) == 0:
                dfs(step_id)

        return cycles

    def get_execution_order(self) -> List[PlanStep]:
        """Return deterministic topological order of steps."""
        in_degree: Dict[str, int] = {s: 0 for s in self.steps_by_id}
        adj: Dict[str, List[str]] = {s: [] for s in self.steps_by_id}

        for step_id, step in self.steps_by_id.items():
            for dep in step.dependencies:
                if dep in adj:
                    adj[dep].append(step_id)
                    in_degree[step_id] += 1

        # Kahn's Algorithm
        queue = [s for s, deg in in_degree.items() if deg == 0]
        order: List[PlanStep] = []

        while queue:
            queue.sort()  # Ensure deterministic order
            curr = queue.pop(0)
            order.append(self.steps_by_id[curr])

            for nxt in adj[curr]:
                in_degree[nxt] -= 1
                if in_degree[nxt] == 0:
                    queue.append(nxt)

        if len(order) < len(self.steps_by_id):
            # Unreachable or cyclical steps remaining; fallback to original step order
            return self.plan.steps

        return order

    def analyze_parallelization(self) -> Dict[str, bool]:
        """Determine safety of parallel execution for each step."""
        safety: Dict[str, bool] = {}
        target_map: Dict[str, Set[str]] = {}

        for step in self.plan.steps:
            targets = set(step.intended_targets + step.mutation_scope)
            target_map[step.step_id] = targets

        for i, step_a in enumerate(self.plan.steps):
            is_safe = step_a.parallelizable
            if is_safe:
                # Check for overlap with any other concurrent step
                targets_a = target_map[step_a.step_id]
                for j, step_b in enumerate(self.plan.steps):
                    if i != j and step_b.parallelizable:
                        targets_b = target_map[step_b.step_id]
                        if targets_a.intersection(targets_b):
                            is_safe = False
                            break
            safety[step_a.step_id] = is_safe

        return safety
