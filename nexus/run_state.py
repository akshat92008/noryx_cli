"""Canonical, crash-resilient state for Nexus engineering runs.

Conversation history is useful for a model, but it is not an execution
ledger.  This module persists each user turn as a versioned run containing
the request, plan, tool events, checkpoints, and final verification report.
Writes use ``os.replace`` so an interrupted process cannot leave half-written
JSON state behind.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from nexus.paths import nexus_home
from nexus.storage import exclusive_file_lock, read_jsonl_prefix, recover_jsonl_suffix

RUN_SCHEMA_VERSION = "nexus.run.v2"


class RunStatus(str, Enum):
    """Lifecycle state for one user-request run."""

    RUNNING = "RUNNING"
    VERIFIED = "VERIFIED"
    PARTIALLY_VERIFIED = "PARTIALLY_VERIFIED"
    UNVERIFIED = "UNVERIFIED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    ROLLED_BACK = "ROLLED_BACK"


class CriterionStatus(str, Enum):
    """Evidence status for one acceptance criterion."""

    SATISFIED = "SATISFIED"
    UNSATISFIED = "UNSATISFIED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"
    UNVERIFIED = "UNVERIFIED"


@dataclass
class CriterionResult:
    """Machine-readable outcome for one acceptance criterion."""

    criterion: str
    status: CriterionStatus
    evidence_ids: list[str] = field(default_factory=list)
    detail: str = ""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_default(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    return str(value)


def _atomic_write_json(path: Path, value: Any) -> None:
    """Atomically replace *path* with formatted JSON."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, indent=2, ensure_ascii=False, default=_json_default) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class RunLedger:
    """Persistent run directory for a Nexus conversation.

    Directory layout::

        runs/<session>/
          session.json
          turn-0001/
            request.json
            plan.json
            events.jsonl
            state.json
            checkpoints/0001-*.json
            final_report.json
    """

    def __init__(
        self,
        session_id: str,
        working_dir: str | Path,
        root: str | Path | None = None,
    ):
        self.session_id = session_id
        self.working_dir = str(Path(working_dir).expanduser().resolve())
        state_root = Path(root).expanduser().resolve() if root else nexus_home()
        self.session_dir = state_root / "runs" / session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self.session_path = self.session_dir / "session.json"
        self.turn_id = ""
        self.turn_dir: Path | None = None
        self._event_counter = 0
        self._checkpoint_counter = 0
        self._ensure_session()

    def _ensure_session(self) -> None:
        existing = self._read_json(self.session_path) or {}
        payload = {
            "schema_version": RUN_SCHEMA_VERSION,
            "session_id": self.session_id,
            "working_dir": self.working_dir,
            "created_at": existing.get("created_at", _utc_now()),
            "updated_at": _utc_now(),
            "turns": existing.get("turns", []),
        }
        _atomic_write_json(self.session_path, payload)

    def begin(
        self,
        request: str,
        *,
        analysis: dict[str, Any] | None = None,
        plan: Any = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Start and persist the next turn in this conversation."""
        with exclusive_file_lock(self.session_path):
            session = self._read_json(self.session_path) or {"turns": []}
            existing_numbers = [
                int(path.name.removeprefix("turn-"))
                for path in self.session_dir.glob("turn-*")
                if path.is_dir() and path.name.removeprefix("turn-").isdigit()
            ]
            turn_number = max([len(session.get("turns", [])), *existing_numbers], default=0) + 1
            self.turn_id = f"turn-{turn_number:04d}"
            self.turn_dir = self.session_dir / self.turn_id
            self.turn_dir.mkdir(parents=True, exist_ok=False)
            (self.turn_dir / "checkpoints").mkdir()
            (self.turn_dir / "patches").mkdir()
            (self.turn_dir / "tests").mkdir()
            self._event_counter = 0
            self._checkpoint_counter = 0
            normalized_analysis = {
                key: item.value if isinstance(item, Enum) else item
                for key, item in (analysis or {}).items()
            }
            request_record = {
                "schema_version": RUN_SCHEMA_VERSION,
                "session_id": self.session_id,
                "turn_id": self.turn_id,
                "request": request,
                "working_dir": self.working_dir,
                "analysis": normalized_analysis,
                "metadata": metadata or {},
                "created_at": _utc_now(),
            }
            _atomic_write_json(self.turn_dir / "request.json", request_record)
            if plan is not None:
                self.record_plan(plan)
                plan_steps = (
                    getattr(plan, "steps", [])
                    if not isinstance(plan, dict)
                    else plan.get("steps", [])
                )
                self.record_tasks(plan_steps)
            else:
                self.record_tasks([])
            _atomic_write_json(self.turn_dir / "costs.json", {})
            for filename in ("events.jsonl", "model_calls.jsonl", "tool_calls.jsonl"):
                (self.turn_dir / filename).touch()

            state = {
                "schema_version": RUN_SCHEMA_VERSION,
                "session_id": self.session_id,
                "turn_id": self.turn_id,
                "status": RunStatus.RUNNING.value,
                "started_at": request_record["created_at"],
                "updated_at": request_record["created_at"],
                "event_count": 0,
                "checkpoint_count": 0,
            }
            _atomic_write_json(self.turn_dir / "state.json", state)

            session.setdefault("turns", []).append(
                {
                    "turn_id": self.turn_id,
                    "request": request[:300],
                    "status": RunStatus.RUNNING.value,
                    "started_at": request_record["created_at"],
                }
            )
            session["updated_at"] = _utc_now()
            _atomic_write_json(self.session_path, session)
        return self.turn_id

    def record_plan(self, plan: Any) -> None:
        """Persist the current execution plan snapshot."""
        turn_dir = self._require_turn()
        data = plan.to_dict() if hasattr(plan, "to_dict") else plan
        _atomic_write_json(turn_dir / "plan.json", data)

    def record_tasks(self, tasks: Any) -> None:
        """Persist task DAG state independently from the planner snapshot."""
        turn_dir = self._require_turn()
        normalized = []
        for task in tasks or []:
            if hasattr(task, "to_dict"):
                normalized.append(task.to_dict())
            elif hasattr(task, "__dataclass_fields__"):
                normalized.append(asdict(task))
            elif isinstance(task, dict):
                normalized.append(dict(task))
            else:
                normalized.append({"value": str(task)})
        _atomic_write_json(
            turn_dir / "tasks.json",
            {
                "schema_version": RUN_SCHEMA_VERSION,
                "session_id": self.session_id,
                "turn_id": self.turn_id,
                "tasks": normalized,
                "updated_at": _utc_now(),
            },
        )

    def record_costs(self, costs: dict[str, Any]) -> None:
        """Persist the current cost and token snapshot."""
        turn_dir = self._require_turn()
        _atomic_write_json(
            turn_dir / "costs.json",
            {
                "schema_version": RUN_SCHEMA_VERSION,
                "session_id": self.session_id,
                "turn_id": self.turn_id,
                "costs": costs,
                "updated_at": _utc_now(),
            },
        )

    def append_model_call(
        self,
        *,
        role: str,
        model: str,
        status: str,
        usage: dict[str, Any] | None = None,
        task_id: int | str | None = None,
        detail: str = "",
        provider: str = "",
        request_id: str = "",
        started_at: str = "",
        completed_at: str = "",
        duration_ms: int = 0,
        attempt: int = 1,
        physical_attempt: int = 1,
        retry_number: int = 0,
        fallback_from: str = "",
        estimated_cost_usd: float = 0.0,
        has_tool_calls: bool = False,
        error_category: str = "",
    ) -> str:
        """Append redacted model-call metadata without persisting prompts or secrets."""
        return self._append_jsonl(
            "model_calls.jsonl",
            {
                "role": role,
                "model": model,
                "status": status,
                "usage": usage or {},
                "task_id": task_id,
                "detail": detail,
                "provider": provider,
                "request_id": request_id,
                "started_at": started_at,
                "completed_at": completed_at,
                "duration_ms": max(0, int(duration_ms or 0)),
                "attempt": max(1, int(attempt or 1)),
                "physical_attempt": max(1, int(physical_attempt or 1)),
                "retry_number": max(0, int(retry_number or 0)),
                "fallback_from": fallback_from,
                "estimated_cost_usd": max(0.0, float(estimated_cost_usd or 0.0)),
                "has_tool_calls": bool(has_tool_calls),
                "error_category": error_category,
            },
            prefix="model",
        )

    def append_tool_call(
        self,
        *,
        tool: str,
        status: str,
        arguments: dict[str, Any] | None = None,
        task_id: int | str | None = None,
        evidence_id: str = "",
        duration_ms: int = 0,
    ) -> str:
        """Append a machine-readable tool call separate from the event narrative."""
        return self._append_jsonl(
            "tool_calls.jsonl",
            {
                "tool": tool,
                "status": status,
                "arguments": arguments or {},
                "task_id": task_id,
                "evidence_id": evidence_id,
                "duration_ms": duration_ms,
            },
            prefix="tool",
        )

    def store_artifact(
        self,
        category: str,
        name: str,
        content: str | bytes,
    ) -> Path:
        """Store a patch or test artifact under the active run directory."""
        if category not in {"patches", "tests"}:
            raise ValueError("Run artifacts must use the patches or tests category")
        turn_dir = self._require_turn()
        safe_name = "".join(
            char if char.isalnum() or char in "._-" else "-" for char in name
        ).strip(".-")
        if not safe_name:
            raise ValueError("Artifact name is empty after normalization")
        target = turn_dir / category / safe_name[:160]
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
        return target

    def append_event(
        self,
        kind: str,
        *,
        status: str,
        detail: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Append one durable event and return its stable identifier."""
        event_id = self._append_jsonl(
            "events.jsonl",
            {
                "kind": kind,
                "status": status,
                "detail": detail,
                "metadata": metadata or {},
            },
            prefix="event",
        )
        self._event_counter = max(self._event_counter, int(event_id.rsplit("-", 1)[-1]))
        self._update_state(event_count=self._event_counter)
        return event_id

    def checkpoint(
        self,
        label: str,
        *,
        plan: Any = None,
        evidence_count: int = 0,
        history_count: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> Path:
        """Persist a verified recovery point for the active turn."""
        turn_dir = self._require_turn()
        self._checkpoint_counter += 1
        safe_label = "".join(char if char.isalnum() or char in "-_" else "-" for char in label)
        path = turn_dir / "checkpoints" / f"{self._checkpoint_counter:04d}-{safe_label[:48]}.json"
        record = {
            "schema_version": RUN_SCHEMA_VERSION,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "checkpoint": self._checkpoint_counter,
            "label": label,
            "timestamp_utc": _utc_now(),
            "evidence_count": evidence_count,
            "history_count": history_count,
            "plan": plan.to_dict() if hasattr(plan, "to_dict") else plan,
            "metadata": metadata or {},
        }
        _atomic_write_json(path, record)
        self._update_state(checkpoint_count=self._checkpoint_counter)
        return path

    def finalize(
        self,
        status: RunStatus,
        *,
        objective: str,
        outcome: str = "",
        criteria: list[CriterionResult] | None = None,
        files_changed: list[str] | None = None,
        checks: list[dict[str, Any]] | None = None,
        costs: dict[str, Any] | None = None,
        risks: list[str] | None = None,
        work_completed: list[str] | None = None,
        checks_skipped: list[str] | None = None,
        dependencies_added: list[str] | None = None,
        permissions_used: list[str] | None = None,
        network_calls: list[str] | None = None,
        model_providers: list[str] | None = None,
        assumptions: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Write the final report and close the current turn."""
        turn_dir = self._require_turn()
        report = {
            "schema_version": RUN_SCHEMA_VERSION,
            "session_id": self.session_id,
            "turn_id": self.turn_id,
            "status": status.value,
            "outcome": outcome or status.value,
            "objective": objective,
            "acceptance_criteria": [
                {
                    **asdict(item),
                    "status": item.status.value,
                }
                for item in (criteria or [])
            ],
            "work_completed": work_completed or [],
            "files_changed": files_changed or [],
            "checks": checks or [],
            "checks_skipped": checks_skipped or [],
            "dependencies_added": dependencies_added or [],
            "permissions_used": permissions_used or [],
            "network_calls": network_calls or [],
            "model_providers": model_providers or [],
            "costs": costs or {},
            "assumptions": assumptions or [],
            "remaining_risks": risks or [],
            "metadata": metadata or {},
            "completed_at": _utc_now(),
        }
        _atomic_write_json(turn_dir / "final_report.json", report)
        self.record_costs(costs or {})
        self._update_state(status=status.value, completed_at=report["completed_at"])

        with exclusive_file_lock(self.session_path):
            session = self._read_json(self.session_path) or {}
            for item in session.get("turns", []):
                if item.get("turn_id") == self.turn_id:
                    item["status"] = status.value
                    item["completed_at"] = report["completed_at"]
                    break
            session["updated_at"] = report["completed_at"]
            _atomic_write_json(self.session_path, session)
        return report

    def mark_rolled_back(self, detail: str) -> None:
        """Change the active run status after a complete rollback."""
        self.append_event("rollback", status="verified", detail=detail)
        completed_at = _utc_now()
        self._update_state(
            status=RunStatus.ROLLED_BACK.value,
            completed_at=completed_at,
        )
        turn_dir = self._require_turn()
        report_path = turn_dir / "final_report.json"
        if not report_path.is_file():
            report_path = turn_dir / "final-report.json"
        report = self._read_json(report_path)
        if report is not None:
            report["status"] = RunStatus.ROLLED_BACK.value
            report["rolled_back_at"] = completed_at
            report.setdefault("metadata", {})["rollback_detail"] = detail
            _atomic_write_json(report_path, report)
        session = self._read_json(self.session_path) or {}
        for item in session.get("turns", []):
            if item.get("turn_id") == self.turn_id:
                item["status"] = RunStatus.ROLLED_BACK.value
                item["completed_at"] = completed_at
                break
        session["updated_at"] = completed_at
        _atomic_write_json(self.session_path, session)

    def latest_checkpoint(self) -> dict[str, Any] | None:
        """Return the most recent checkpoint from the current or latest turn."""
        turn_dir = self.turn_dir or self._latest_turn_dir()
        if not turn_dir:
            return None
        paths = sorted((turn_dir / "checkpoints").glob("*.json"))
        return self._read_json(paths[-1]) if paths else None

    def resume_summary(self) -> dict[str, Any]:
        """Return the latest durable execution state for a resumed session."""
        turn_dir = self._latest_turn_dir()
        if not turn_dir:
            return {}
        self.turn_dir = turn_dir
        self.turn_id = turn_dir.name
        events_path = turn_dir / "events.jsonl"
        self._event_counter = len(read_jsonl_prefix(events_path)[0])
        self._checkpoint_counter = len(list((turn_dir / "checkpoints").glob("*.json")))
        return {
            "request": self._read_json(turn_dir / "request.json") or {},
            "plan": self._read_json(turn_dir / "plan.json") or {},
            "tasks": self._read_json(turn_dir / "tasks.json") or {},
            "state": self._read_json(turn_dir / "state.json") or {},
            "costs": self._read_json(turn_dir / "costs.json") or {},
            "checkpoint": self.latest_checkpoint() or {},
            "final_report": (
                self._read_json(turn_dir / "final_report.json")
                or self._read_json(turn_dir / "final-report.json")
                or {}
            ),
            "resumable": (
                (self._read_json(turn_dir / "state.json") or {}).get("status")
                in {
                    RunStatus.RUNNING.value,
                    RunStatus.FAILED.value,
                    RunStatus.BLOCKED.value,
                    RunStatus.PARTIALLY_VERIFIED.value,
                }
            ),
        }

    def current_paths(self) -> dict[str, str]:
        """Expose user-facing state paths without leaking unrelated files."""
        turn_dir = self._require_turn()
        return {
            "run_directory": str(turn_dir),
            "request": str(turn_dir / "request.json"),
            "plan": str(turn_dir / "plan.json"),
            "events": str(turn_dir / "events.jsonl"),
            "tasks": str(turn_dir / "tasks.json"),
            "model_calls": str(turn_dir / "model_calls.jsonl"),
            "tool_calls": str(turn_dir / "tool_calls.jsonl"),
            "costs": str(turn_dir / "costs.json"),
            "patches": str(turn_dir / "patches"),
            "tests": str(turn_dir / "tests"),
            "state": str(turn_dir / "state.json"),
            "final_report": str(turn_dir / "final_report.json"),
        }

    def _latest_turn_dir(self) -> Path | None:
        candidates = sorted(path for path in self.session_dir.glob("turn-*") if path.is_dir())
        return candidates[-1] if candidates else None

    def _require_turn(self) -> Path:
        if self.turn_dir is None:
            raise RuntimeError("No active Nexus run. Call begin() first.")
        return self.turn_dir

    def _update_state(self, **updates: Any) -> None:
        turn_dir = self._require_turn()
        state_path = turn_dir / "state.json"
        with exclusive_file_lock(state_path):
            state = self._read_json(state_path) or {}
            state.update(updates)
            state["updated_at"] = _utc_now()
            _atomic_write_json(state_path, state)

    def _append_jsonl(
        self,
        filename: str,
        payload: dict[str, Any],
        *,
        prefix: str,
    ) -> str:
        turn_dir = self._require_turn()
        path = turn_dir / filename
        with exclusive_file_lock(path):
            records, corruption = read_jsonl_prefix(path)
            recovery: dict[str, Any] | None = None
            if corruption:
                backup = recover_jsonl_suffix(path, records)
                recovery = {**corruption, "backup": str(backup)}
            record_id = f"{prefix}-{len(records) + 1:06d}"
            record = {
                "schema_version": RUN_SCHEMA_VERSION,
                "id": record_id,
                "timestamp_utc": _utc_now(),
                **payload,
            }
            if recovery:
                record["storage_recovery"] = recovery
            encoded = (json.dumps(record, ensure_ascii=False, default=_json_default) + "\n").encode(
                "utf-8"
            )
            descriptor = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
            try:
                os.write(descriptor, encoded)
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        return record_id

    def read_jsonl(self, filename: str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """Read a run JSONL artifact while preserving a valid prefix on corruption."""

        if filename not in {"events.jsonl", "model_calls.jsonl", "tool_calls.jsonl"}:
            raise ValueError(f"Unsupported run JSONL artifact: {filename}")
        turn_dir = self.turn_dir or self._latest_turn_dir()
        return read_jsonl_prefix(turn_dir / filename) if turn_dir else ([], None)

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None
