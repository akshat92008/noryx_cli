"""List, inspect, and replay durable Nexus runs."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from nexus.paths import nexus_home


@dataclass(frozen=True)
class RunReference:
    session_id: str
    turn_id: str
    status: str
    request: str
    working_dir: str
    updated_at: str
    path: str


class RunCatalog:
    def __init__(self, root: str | Path | None = None):
        base = Path(root).expanduser().resolve() if root else nexus_home()
        self.runs_dir = base / "runs"

    def list(self, *, working_dir: str | Path | None = None, limit: int = 50) -> list[RunReference]:
        target = str(Path(working_dir).expanduser().resolve()) if working_dir else ""
        records: list[RunReference] = []
        if not self.runs_dir.is_dir():
            return records
        for session_dir in self.runs_dir.iterdir():
            session = self._read_json(session_dir / "session.json")
            if not session or (target and session.get("working_dir") != target):
                continue
            for turn in session.get("turns", []):
                turn_id = str(turn.get("turn_id", ""))
                turn_dir = session_dir / turn_id
                request = self._read_json(turn_dir / "request.json")
                state = self._read_json(turn_dir / "state.json")
                records.append(
                    RunReference(
                        session_id=session_dir.name,
                        turn_id=turn_id,
                        status=str((state or {}).get("status", turn.get("status", ""))),
                        request=str((request or {}).get("request", turn.get("request", ""))),
                        working_dir=str(session.get("working_dir", "")),
                        updated_at=str(
                            (state or {}).get("updated_at", session.get("updated_at", ""))
                        ),
                        path=str(turn_dir),
                    )
                )
        records.sort(key=lambda item: item.updated_at, reverse=True)
        return records[: max(1, int(limit))]

    def inspect(self, run_id: str) -> dict[str, Any]:
        turn_dir = self.resolve(run_id)
        return {
            "request": self._read_json(turn_dir / "request.json") or {},
            "plan": self._read_json(turn_dir / "plan.json") or {},
            "tasks": self._read_json(turn_dir / "tasks.json") or {},
            "state": self._read_json(turn_dir / "state.json") or {},
            "costs": self._read_json(turn_dir / "costs.json") or {},
            "checkpoint": self._latest_json(turn_dir / "checkpoints"),
            "final_report": (
                self._read_json(turn_dir / "final_report.json")
                or self._read_json(turn_dir / "final-report.json")
                or {}
            ),
            "paths": {
                "run": str(turn_dir),
                "events": str(turn_dir / "events.jsonl"),
                "model_calls": str(turn_dir / "model_calls.jsonl"),
                "tool_calls": str(turn_dir / "tool_calls.jsonl"),
                "patches": str(turn_dir / "patches"),
                "tests": str(turn_dir / "tests"),
            },
        }

    def replay(self, run_id: str) -> Iterator[dict[str, Any]]:
        turn_dir = self.resolve(run_id)
        path = turn_dir / "events.jsonl"
        if not path.is_file():
            return
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                yield value

    def resolve(self, run_id: str) -> Path:
        raw = run_id.strip()
        if not raw:
            candidates = self.list(limit=1)
            if not candidates:
                raise FileNotFoundError("No Nexus runs exist")
            return Path(candidates[0].path)
        if "/" in raw:
            parts = raw.split("/")
            if len(parts) != 2:
                raise FileNotFoundError(f"Invalid Nexus run id: {raw}")
            session_id, turn_id = parts
            self._validate_identifier(session_id)
            self._validate_identifier(turn_id)
            candidate = self.runs_dir / session_id / turn_id
        else:
            self._validate_identifier(raw)
            session_dir = self.runs_dir / raw
            if session_dir.is_dir():
                turns = sorted(path for path in session_dir.glob("turn-*") if path.is_dir())
                if not turns:
                    raise FileNotFoundError(f"Session has no turns: {raw}")
                candidate = turns[-1]
            else:
                matches = list(self.runs_dir.glob(f"*/{raw}"))
                if len(matches) != 1:
                    raise FileNotFoundError(f"Run id is missing or ambiguous: {raw}")
                candidate = matches[0]
        try:
            candidate.resolve().relative_to(self.runs_dir.resolve())
        except ValueError as exc:
            raise FileNotFoundError(f"Invalid Nexus run id: {raw}") from exc
        if not candidate.is_dir():
            raise FileNotFoundError(f"Nexus run not found: {raw}")
        return candidate

    @staticmethod
    def _validate_identifier(value: str) -> None:
        if value in {"", ".", ".."} or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value):
            raise FileNotFoundError(f"Invalid Nexus run identifier: {value}")

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return value if isinstance(value, dict) else None

    def _latest_json(self, directory: Path) -> dict[str, Any]:
        candidates = sorted(directory.glob("*.json")) if directory.is_dir() else []
        return self._read_json(candidates[-1]) if candidates else {}
