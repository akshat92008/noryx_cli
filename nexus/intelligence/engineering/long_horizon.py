"""Durable, HMAC-authenticated phase controller for long-running engineering tasks."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from nexus.intelligence.engineering.integrity import StateAuthenticator
from nexus.storage import exclusive_file_lock


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LongHorizonIntegrityError(ValueError):
    """Raised when persisted long-horizon state is corrupt or has been modified."""


class LongHorizonConflictError(RuntimeError):
    """Raised when a stale controller attempts to overwrite a newer checkpoint."""


class LongHorizonPhase(str, Enum):
    RESEARCH = "RESEARCH"
    PLAN = "PLAN"
    IMPLEMENT = "IMPLEMENT"
    VERIFY = "VERIFY"
    REVIEW = "REVIEW"
    COMPLETE = "COMPLETE"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"


_ALLOWED = {
    LongHorizonPhase.RESEARCH: {
        LongHorizonPhase.PLAN,
        LongHorizonPhase.BLOCKED,
        LongHorizonPhase.FAILED,
    },
    LongHorizonPhase.PLAN: {
        LongHorizonPhase.RESEARCH,
        LongHorizonPhase.IMPLEMENT,
        LongHorizonPhase.BLOCKED,
        LongHorizonPhase.FAILED,
    },
    LongHorizonPhase.IMPLEMENT: {
        LongHorizonPhase.VERIFY,
        LongHorizonPhase.PLAN,
        LongHorizonPhase.BLOCKED,
        LongHorizonPhase.FAILED,
    },
    LongHorizonPhase.VERIFY: {
        LongHorizonPhase.REVIEW,
        LongHorizonPhase.IMPLEMENT,
        LongHorizonPhase.BLOCKED,
        LongHorizonPhase.FAILED,
    },
    LongHorizonPhase.REVIEW: {
        LongHorizonPhase.COMPLETE,
        LongHorizonPhase.IMPLEMENT,
        LongHorizonPhase.BLOCKED,
        LongHorizonPhase.FAILED,
    },
    LongHorizonPhase.COMPLETE: set(),
    LongHorizonPhase.BLOCKED: {
        LongHorizonPhase.RESEARCH,
        LongHorizonPhase.PLAN,
        LongHorizonPhase.FAILED,
    },
    LongHorizonPhase.FAILED: set(),
}


@dataclass
class PhaseCheckpoint:
    phase: str
    summary: str
    evidence_ids: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=_now)


@dataclass
class LongHorizonState:
    task_id: str
    objective: str
    phase: LongHorizonPhase = LongHorizonPhase.RESEARCH
    phase_attempts: dict[str, int] = field(default_factory=dict)
    checkpoints: list[PhaseCheckpoint] = field(default_factory=list)
    completed_steps: list[str] = field(default_factory=list)
    remaining_steps: list[str] = field(default_factory=list)
    last_error: str = ""
    updated_at: str = field(default_factory=_now)
    sequence: int = 0
    integrity_hmac_sha256: str = ""
    integrity_key_id: str = ""
    integrity_scheme: str = "hmac-sha256-v1"

    def payload(self) -> dict[str, Any]:
        data = asdict(self)
        data["phase"] = self.phase.value
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
            raise LongHorizonIntegrityError(
                f"Long-horizon state authentication failed for {self.task_id}."
            )

    def to_dict(self) -> dict[str, Any]:
        data = self.payload()
        data["integrity_hmac_sha256"] = self.integrity_hmac_sha256
        data["integrity_key_id"] = self.integrity_key_id
        data["integrity_scheme"] = self.integrity_scheme
        return data

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], authenticator: StateAuthenticator
    ) -> "LongHorizonState":
        state = cls(
            task_id=str(data["task_id"]),
            objective=str(data.get("objective", "")),
            phase=LongHorizonPhase(str(data.get("phase", LongHorizonPhase.RESEARCH.value))),
            phase_attempts={
                str(k): int(v) for k, v in dict(data.get("phase_attempts", {})).items()
            },
            checkpoints=[PhaseCheckpoint(**item) for item in data.get("checkpoints", [])],
            completed_steps=[str(item) for item in data.get("completed_steps", [])],
            remaining_steps=[str(item) for item in data.get("remaining_steps", [])],
            last_error=str(data.get("last_error", "")),
            updated_at=str(data.get("updated_at", _now())),
            sequence=int(data.get("sequence", 0)),
            integrity_hmac_sha256=str(data.get("integrity_hmac_sha256", "")),
            integrity_key_id=str(data.get("integrity_key_id", "")),
            integrity_scheme=str(data.get("integrity_scheme", "hmac-sha256-v1")),
        )
        state.verify(authenticator)
        return state


class LongHorizonController:
    """Coordinates resumable phases without accepting stale or corrupt state."""

    def __init__(self, repository_root: str | Path, task_id: str, objective: str):
        root = Path(repository_root).expanduser().resolve()
        self.authenticator = StateAuthenticator.for_repository(root)
        self.path = root / ".noryx" / "long-horizon" / f"{task_id}.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.path):
            if self.path.exists():
                self.state = self._load_unlocked()
            else:
                self.state = LongHorizonState(task_id=task_id, objective=objective)
                self._write_unlocked(self.state)

    def _load_unlocked(self) -> LongHorizonState:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise LongHorizonIntegrityError("Long-horizon state root must be an object.")
            return LongHorizonState.from_dict(data, self.authenticator)
        except LongHorizonIntegrityError:
            raise
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise LongHorizonIntegrityError(
                f"Long-horizon state is corrupt or unreadable: {self.path.name}."
            ) from exc

    def _write_unlocked(self, state: LongHorizonState) -> None:
        state.seal(self.authenticator)
        payload = json.dumps(state.to_dict(), indent=2, sort_keys=True) + "\n"
        fd, temp = tempfile.mkstemp(
            prefix=f".{self.path.name}.", dir=str(self.path.parent), text=True
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, self.path)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def save(self) -> None:
        with exclusive_file_lock(self.path):
            if self.path.exists():
                current = self._load_unlocked()
                if current.sequence > self.state.sequence:
                    raise LongHorizonConflictError(
                        f"Long-horizon state has a newer sequence "
                        f"({current.sequence}>{self.state.sequence})."
                    )
            self._write_unlocked(self.state)

    def transition(
        self,
        phase: LongHorizonPhase,
        *,
        summary: str,
        evidence_ids: list[str] | None = None,
        error: str = "",
    ) -> LongHorizonState:
        with exclusive_file_lock(self.path):
            current = self._load_unlocked()
            if current.sequence > self.state.sequence:
                self.state = current
            if phase not in _ALLOWED[self.state.phase]:
                raise ValueError(
                    f"Invalid long-horizon transition {self.state.phase.value} -> {phase.value}"
                )
            if (
                phase
                in {
                    LongHorizonPhase.VERIFY,
                    LongHorizonPhase.REVIEW,
                    LongHorizonPhase.COMPLETE,
                }
                and not evidence_ids
            ):
                raise ValueError(f"Transition to {phase.value} requires evidence identifiers")
            self.state.phase = phase
            self.state.phase_attempts[phase.value] = (
                self.state.phase_attempts.get(phase.value, 0) + 1
            )
            self.state.last_error = error
            self.state.checkpoints.append(
                PhaseCheckpoint(
                    phase=phase.value,
                    summary=summary[:2000],
                    evidence_ids=list(evidence_ids or []),
                )
            )
            self._write_unlocked(self.state)
        return self.state

    def resume_context(self) -> str:
        last = self.state.checkpoints[-1] if self.state.checkpoints else None
        return (
            "[LONG-HORIZON EXECUTION STATE]\n"
            f"Phase: {self.state.phase.value}\n"
            f"Completed steps: {', '.join(self.state.completed_steps) or 'none'}\n"
            f"Remaining steps: {', '.join(self.state.remaining_steps) or 'not recorded'}\n"
            f"Last checkpoint: {last.summary if last else 'none'}\n"
            f"Last error: {self.state.last_error or 'none'}"
        )
