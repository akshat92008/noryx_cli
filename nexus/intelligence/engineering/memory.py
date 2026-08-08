"""Durable, HMAC-authenticated engineering task memory.

Conversation transcripts are not sufficient for long-running engineering work.  This
module stores the task goal, constraints, decisions, changed files, verification
state, remaining work, and failure lessons in a compact machine-readable record.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nexus.storage import exclusive_file_lock
from nexus.intelligence.engineering.integrity import StateAuthenticator


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()



class MemoryIntegrityError(ValueError):
    """Raised when persisted task memory has been modified or corrupted."""


class MemoryConflictError(RuntimeError):
    """Raised when a stale writer attempts to replace newer task memory."""


@dataclass
class EngineeringDecision:
    statement: str
    rationale: str = ""
    evidence: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now)


@dataclass
class EngineeringChange:
    path: str
    reason: str
    sha256: str = ""
    lines_changed: int = 0
    created_at: str = field(default_factory=_now)


@dataclass
class EngineeringFailure:
    phase: str
    category: str
    summary: str
    strategy: str = ""
    occurrence: int = 1
    created_at: str = field(default_factory=_now)


@dataclass
class EngineeringTaskMemory:
    task_id: str
    objective: str
    repository_root: str
    repository_tree_hash: str = ""
    task_type: str = "unknown"
    risk_level: str = "medium"
    constraints: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    decisive_files: list[str] = field(default_factory=list)
    related_tests: list[str] = field(default_factory=list)
    decisions: list[EngineeringDecision] = field(default_factory=list)
    changes: list[EngineeringChange] = field(default_factory=list)
    failures: list[EngineeringFailure] = field(default_factory=list)
    completed_phases: list[str] = field(default_factory=list)
    remaining_work: list[str] = field(default_factory=list)
    verification_summary: dict[str, Any] = field(default_factory=dict)
    status: str = "RUNNING"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    sequence: int = 0
    integrity_hmac_sha256: str = ""
    integrity_key_id: str = ""
    integrity_scheme: str = "hmac-sha256-v1"

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("integrity_hmac_sha256", None)
        data.pop("integrity_key_id", None)
        data.pop("integrity_scheme", None)
        return data

    def seal(self, authenticator: StateAuthenticator) -> None:
        self.updated_at = _now()
        self.sequence += 1
        self.integrity_key_id = authenticator.key_id
        self.integrity_scheme = authenticator.scheme
        self.integrity_hmac_sha256 = authenticator.sign(self.payload())

    def verify(self, authenticator: StateAuthenticator) -> None:
        if not authenticator.verify(
            self.payload(),
            self.integrity_hmac_sha256,
            key_id=self.integrity_key_id,
            scheme=self.integrity_scheme,
        ):
            raise MemoryIntegrityError(
                f"Engineering task memory authentication failed for {self.task_id}."
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], authenticator: StateAuthenticator
    ) -> "EngineeringTaskMemory":
        memory = cls(
            task_id=str(data["task_id"]),
            objective=str(data.get("objective", "")),
            repository_root=str(data.get("repository_root", "")),
            repository_tree_hash=str(data.get("repository_tree_hash", "")),
            task_type=str(data.get("task_type", "unknown")),
            risk_level=str(data.get("risk_level", "medium")),
            constraints=[str(item) for item in data.get("constraints", [])],
            non_goals=[str(item) for item in data.get("non_goals", [])],
            decisive_files=[str(item) for item in data.get("decisive_files", [])],
            related_tests=[str(item) for item in data.get("related_tests", [])],
            decisions=[EngineeringDecision(**item) for item in data.get("decisions", [])],
            changes=[EngineeringChange(**item) for item in data.get("changes", [])],
            failures=[EngineeringFailure(**item) for item in data.get("failures", [])],
            completed_phases=[str(item) for item in data.get("completed_phases", [])],
            remaining_work=[str(item) for item in data.get("remaining_work", [])],
            verification_summary=dict(data.get("verification_summary", {})),
            status=str(data.get("status", "RUNNING")),
            created_at=str(data.get("created_at", _now())),
            updated_at=str(data.get("updated_at", _now())),
            sequence=int(data.get("sequence", 0)),
            integrity_hmac_sha256=str(data.get("integrity_hmac_sha256", "")),
            integrity_key_id=str(data.get("integrity_key_id", "")),
            integrity_scheme=str(data.get("integrity_scheme", "hmac-sha256-v1")),
        )
        memory.verify(authenticator)
        return memory

    def prompt_context(self, *, max_items: int = 12) -> str:
        decisions = "\n".join(
            f"- {item.statement}" + (f" — {item.rationale}" if item.rationale else "")
            for item in self.decisions[-max_items:]
        ) or "- None recorded"
        changes = "\n".join(
            f"- {item.path}: {item.reason}" for item in self.changes[-max_items:]
        ) or "- None"
        failures = "\n".join(
            f"- [{item.phase}/{item.category}] {item.summary}; next={item.strategy or 're-diagnose'}"
            for item in self.failures[-max_items:]
        ) or "- None"
        remaining = "\n".join(f"- {item}" for item in self.remaining_work[-max_items:]) or "- None"
        return (
            "[PERSISTENT ENGINEERING TASK MEMORY]\n"
            f"Goal: {self.objective}\n"
            f"Task type: {self.task_type}; risk: {self.risk_level}; status: {self.status}\n"
            f"Repository tree: {self.repository_tree_hash or 'unknown'}\n"
            f"Constraints: {', '.join(self.constraints) or 'none'}\n"
            f"Non-goals: {', '.join(self.non_goals) or 'none'}\n"
            f"Decisive files: {', '.join(self.decisive_files) or 'not established'}\n"
            f"Related tests: {', '.join(self.related_tests) or 'not established'}\n"
            f"Decisions:\n{decisions}\n"
            f"Changes:\n{changes}\n"
            f"Failures and lessons:\n{failures}\n"
            f"Remaining work:\n{remaining}"
        )


class EngineeringMemoryStore:
    """Atomic repository-local persistence for engineering task memory."""

    def __init__(self, repository_root: str | Path, *, state_root: str | Path | None = None):
        self.repository_root = Path(repository_root).expanduser().resolve()
        self.authenticator = StateAuthenticator.for_repository(self.repository_root)
        root = Path(state_root).expanduser().resolve() if state_root else self.repository_root / ".nexus"
        self.root = root / "task-memory"
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, task_id: str) -> Path:
        safe = "".join(ch for ch in task_id if ch.isalnum() or ch in {"-", "_"})
        if not safe:
            raise ValueError("task_id must contain at least one safe character")
        return self.root / f"{safe}.json"

    def create(
        self,
        task_id: str,
        objective: str,
        *,
        repository_tree_hash: str = "",
        task_type: str = "unknown",
        risk_level: str = "medium",
        constraints: list[str] | None = None,
        non_goals: list[str] | None = None,
        decisive_files: list[str] | None = None,
        related_tests: list[str] | None = None,
    ) -> EngineeringTaskMemory:
        memory = EngineeringTaskMemory(
            task_id=task_id,
            objective=objective.strip(),
            repository_root=str(self.repository_root),
            repository_tree_hash=repository_tree_hash,
            task_type=task_type,
            risk_level=risk_level,
            constraints=list(constraints or []),
            non_goals=list(non_goals or []),
            decisive_files=list(decisive_files or []),
            related_tests=list(related_tests or []),
        )
        self.save(memory)
        return memory

    def save(self, memory: EngineeringTaskMemory) -> Path:
        path = self.path_for(memory.task_id)
        with exclusive_file_lock(path):
            if path.is_file():
                try:
                    current_data = json.loads(path.read_text(encoding="utf-8"))
                    current = EngineeringTaskMemory.from_dict(current_data, self.authenticator)
                except (OSError, json.JSONDecodeError, KeyError, TypeError, MemoryIntegrityError) as exc:
                    raise MemoryIntegrityError(
                        f"Existing task memory is unreadable; refusing to overwrite {path.name}."
                    ) from exc
                if current.sequence > memory.sequence:
                    raise MemoryConflictError(
                        f"Task memory {memory.task_id} has a newer sequence "
                        f"({current.sequence}>{memory.sequence})."
                    )
            memory.seal(self.authenticator)
            payload = json.dumps(memory.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
            fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp_name, path)
            finally:
                if os.path.exists(temp_name):
                    os.unlink(temp_name)
        return path

    def load(self, task_id: str) -> EngineeringTaskMemory:
        path = self.path_for(task_id)
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise MemoryIntegrityError("Engineering task memory root must be an object.")
        return EngineeringTaskMemory.from_dict(data, self.authenticator)

    def latest(self) -> EngineeringTaskMemory | None:
        candidates = sorted(self.root.glob("*.json"), key=lambda p: p.stat().st_mtime_ns, reverse=True)
        if not candidates:
            return None
        path = candidates[0]
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return EngineeringTaskMemory.from_dict(data, self.authenticator)
        except (OSError, json.JSONDecodeError, MemoryIntegrityError, KeyError, TypeError) as exc:
            raise MemoryIntegrityError(
                f"Latest engineering task memory is corrupt or unreadable: {path.name}."
            ) from exc
