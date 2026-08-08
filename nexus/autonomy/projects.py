"""Persisted long-horizon engineering project model."""

from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

AUTONOMY_STATE_VERSION = "nexus.autonomy.v1"


class ProjectState(str, Enum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    PREPARING = "preparing"
    RUNNING = "running"
    PAUSED = "paused"
    BLOCKED = "blocked"
    RECOVERING = "recovering"
    AWAITING_APPROVAL = "awaiting_approval"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ARCHIVED = "archived"


@dataclass(frozen=True)
class Requirement:
    requirement_id: str
    text: str
    covered: bool = False


@dataclass(frozen=True)
class Constraint:
    constraint_id: str
    text: str
    requires_approval: bool = False


@dataclass(frozen=True)
class AcceptanceCriterion:
    criterion_id: str
    text: str
    evidence_id: str = ""
    satisfied: bool = False


@dataclass(frozen=True)
class ProjectBudget:
    max_model_calls: int = 100
    max_tool_calls: int = 500
    max_cost_usd: float = 0.0
    max_wall_clock_seconds: int = 86_400


@dataclass(frozen=True)
class Milestone:
    milestone_id: str
    objective: str
    scope: tuple[str, ...]
    dependencies: tuple[str, ...] = ()
    deliverables: tuple[str, ...] = ()
    acceptance_criteria: tuple[AcceptanceCriterion, ...] = ()
    verification: tuple[str, ...] = ()
    risk: str = "medium"
    budget: ProjectBudget = field(default_factory=ProjectBudget)
    rollback_boundary: str = ""
    required_approvals: tuple[str, ...] = ()
    state: str = "pending"
    evidence_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Milestone":
        return cls(
            milestone_id=data["milestone_id"],
            objective=data["objective"],
            scope=tuple(data.get("scope", ())),
            dependencies=tuple(data.get("dependencies", ())),
            deliverables=tuple(data.get("deliverables", ())),
            acceptance_criteria=tuple(
                AcceptanceCriterion(**item) for item in data.get("acceptance_criteria", ())
            ),
            verification=tuple(data.get("verification", ())),
            risk=data.get("risk", "medium"),
            budget=ProjectBudget(**data.get("budget", {})),
            rollback_boundary=data.get("rollback_boundary", ""),
            required_approvals=tuple(data.get("required_approvals", ())),
            state=data.get("state", "pending"),
            evidence_ids=tuple(data.get("evidence_ids", ())),
        )


@dataclass(frozen=True)
class Workstream:
    workstream_id: str
    milestone_ids: tuple[str, ...]
    max_parallel_mutations: int = 1


@dataclass(frozen=True)
class Checkpoint:
    checkpoint_id: str
    project_id: str
    reason: str
    repository_revision: str = ""
    workspace_state_hash: str = ""
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class ScopeDrift:
    detected: bool
    reasons: tuple[str, ...] = ()
    requires_replan: bool = False


@dataclass(frozen=True)
class EngineeringProject:
    project_id: str
    objective: str
    requirements: tuple[Requirement, ...]
    milestones: tuple[Milestone, ...]
    workstreams: tuple[Workstream, ...]
    constraints: tuple[Constraint, ...]
    acceptance_criteria: tuple[AcceptanceCriterion, ...]
    budget: ProjectBudget
    state: ProjectState = ProjectState.PROPOSED
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    active_risks: tuple[str, ...] = ()
    decisions: tuple[str, ...] = ()
    completed_milestones: tuple[str, ...] = ()
    failed_approaches: tuple[str, ...] = ()
    verification_evidence: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["state"] = self.state.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "EngineeringProject":
        return cls(
            project_id=data["project_id"],
            objective=data["objective"],
            requirements=tuple(Requirement(**item) for item in data.get("requirements", ())),
            milestones=tuple(Milestone.from_dict(item) for item in data.get("milestones", ())),
            workstreams=tuple(Workstream(**item) for item in data.get("workstreams", ())),
            constraints=tuple(Constraint(**item) for item in data.get("constraints", ())),
            acceptance_criteria=tuple(
                AcceptanceCriterion(**item) for item in data.get("acceptance_criteria", ())
            ),
            budget=ProjectBudget(**data.get("budget", {})),
            state=ProjectState(data.get("state", "proposed")),
            created_at=data.get("created_at", time.time()),
            updated_at=data.get("updated_at", time.time()),
            active_risks=tuple(data.get("active_risks", ())),
            decisions=tuple(data.get("decisions", ())),
            completed_milestones=tuple(data.get("completed_milestones", ())),
            failed_approaches=tuple(data.get("failed_approaches", ())),
            verification_evidence=tuple(data.get("verification_evidence", ())),
        )


class AutonomyStore:
    def __init__(self, state_dir: Path | None = None):
        self.state_dir = state_dir or Path.home() / ".nexusai" / "autonomy"
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        return self.state_dir / f"{name}.json"

    def read(self, name: str, default: Any) -> Any:
        path = self._path(name)
        if not path.is_file():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default

    def write(self, name: str, data: Any) -> None:
        path = self._path(name)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)


class ProjectScheduler:
    """Select the next dependency-valid milestone without parallel mutations."""

    def next_milestone(self, project: EngineeringProject) -> Milestone | None:
        completed = set(project.completed_milestones)
        candidates = []
        for milestone in project.milestones:
            if milestone.state in {"completed", "cancelled"}:
                continue
            if all(dep in completed for dep in milestone.dependencies):
                candidates.append(milestone)
        if not candidates:
            return None
        risk_order = {"low": 0, "medium": 1, "high": 2, "critical": 3}
        return sorted(candidates, key=lambda item: (risk_order.get(item.risk, 1), item.milestone_id))[0]


class ProjectService:
    def __init__(self, store: AutonomyStore | None = None):
        self.store = store or AutonomyStore()

    def create(
        self,
        objective: str,
        *,
        requirements: tuple[str, ...] = (),
        acceptance_criteria: tuple[str, ...] = (),
    ) -> EngineeringProject:
        project_id = f"project_{uuid.uuid4().hex[:10]}"
        reqs = tuple(
            Requirement(f"req_{index + 1}", text)
            for index, text in enumerate(requirements or (objective,))
        )
        criteria = tuple(
            AcceptanceCriterion(f"ac_{index + 1}", text)
            for index, text in enumerate(acceptance_criteria or ("All requirements have evidence",))
        )
        milestone = Milestone(
            milestone_id="m1",
            objective=objective,
            scope=("repository",),
            deliverables=("working_diff", "verification_evidence"),
            acceptance_criteria=criteria,
            verification=("deterministic_tests",),
            rollback_boundary="milestone",
        )
        project = EngineeringProject(
            project_id=project_id,
            objective=objective,
            requirements=reqs,
            milestones=(milestone,),
            workstreams=(Workstream("default", ("m1",)),),
            constraints=(),
            acceptance_criteria=criteria,
            budget=ProjectBudget(),
        )
        self._save(project)
        self.checkpoint(project_id, "project_start")
        return project

    def get(self, project_id: str) -> EngineeringProject | None:
        for item in self.store.read("projects", {"items": []})["items"]:
            if item["project_id"] == project_id:
                return EngineeringProject.from_dict(item)
        return None

    def list(self) -> list[EngineeringProject]:
        return [
            EngineeringProject.from_dict(item)
            for item in self.store.read("projects", {"items": []})["items"]
        ]

    def transition(self, project_id: str, state: ProjectState) -> EngineeringProject:
        project = self._require(project_id)
        updated = self._replace(project, state=state, updated_at=time.time())
        self._save(updated)
        if state in {ProjectState.APPROVED, ProjectState.PAUSED, ProjectState.RUNNING}:
            self.checkpoint(project_id, f"state_{state.value}")
        return updated

    def plan(self, project_id: str) -> dict[str, Any]:
        project = self._require(project_id)
        next_milestone = ProjectScheduler().next_milestone(project)
        return {
            "project_id": project.project_id,
            "state": project.state.value,
            "next_milestone": next_milestone.to_dict() if next_milestone else None,
            "coverage": self.progress(project_id),
        }

    def progress(self, project_id: str) -> dict[str, Any]:
        project = self._require(project_id)
        total_requirements = len(project.requirements)
        covered_requirements = sum(1 for item in project.requirements if item.covered)
        total_acceptance = len(project.acceptance_criteria)
        satisfied_acceptance = sum(1 for item in project.acceptance_criteria if item.satisfied)
        return {
            "requirements": {"covered": covered_requirements, "total": total_requirements},
            "acceptance": {"satisfied": satisfied_acceptance, "total": total_acceptance},
            "milestones": {
                "completed": len(project.completed_milestones),
                "total": len(project.milestones),
            },
            "evidence_complete": bool(project.verification_evidence),
        }

    def checkpoint(self, project_id: str, reason: str, *, repository_revision: str = "") -> Checkpoint:
        payload = json.dumps(self.get(project_id).to_dict() if self.get(project_id) else {}, sort_keys=True)
        checkpoint = Checkpoint(
            checkpoint_id=f"chk_{uuid.uuid4().hex[:10]}",
            project_id=project_id,
            reason=reason,
            repository_revision=repository_revision,
            workspace_state_hash=hashlib.sha256(payload.encode()).hexdigest(),
        )
        data = self.store.read("checkpoints", {"items": []})
        data["items"].append(asdict(checkpoint))
        self.store.write("checkpoints", data)
        return checkpoint

    def checkpoints(self, project_id: str) -> list[Checkpoint]:
        return [
            Checkpoint(**item)
            for item in self.store.read("checkpoints", {"items": []})["items"]
            if item["project_id"] == project_id
        ]

    def detect_scope_drift(
        self,
        project_id: str,
        *,
        changed_paths: tuple[str, ...],
        new_dependencies: tuple[str, ...] = (),
        changed_acceptance: bool = False,
    ) -> ScopeDrift:
        project = self._require(project_id)
        allowed_scopes = {scope for milestone in project.milestones for scope in milestone.scope}
        reasons = []
        if "repository" not in allowed_scopes:
            for path in changed_paths:
                if not any(path.startswith(scope.rstrip("/") + "/") or path == scope for scope in allowed_scopes):
                    reasons.append(f"unplanned_path:{path}")
        if new_dependencies:
            reasons.append("new_dependencies")
        if changed_acceptance:
            reasons.append("changed_acceptance")
        return ScopeDrift(bool(reasons), tuple(reasons), bool(reasons))

    def _save(self, project: EngineeringProject) -> None:
        data = self.store.read("projects", {"version": AUTONOMY_STATE_VERSION, "items": []})
        data["items"] = [item for item in data["items"] if item["project_id"] != project.project_id]
        data["items"].append(project.to_dict())
        self.store.write("projects", data)

    def _require(self, project_id: str) -> EngineeringProject:
        project = self.get(project_id)
        if not project:
            raise KeyError(project_id)
        return project

    def _replace(self, project: EngineeringProject, **updates: Any) -> EngineeringProject:
        data = project.to_dict()
        data.update(updates)
        if isinstance(data.get("state"), ProjectState):
            data["state"] = data["state"].value
        return EngineeringProject.from_dict(data)
