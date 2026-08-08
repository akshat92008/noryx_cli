"""
nexus/collaboration/persistence.py

JSON-file persistence for collaboration run state.
Allows resume after crash without repeating completed worker work.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Optional

from nexus.collaboration.models import (
    CollaborationBudget,
    CollaborationMode,
    CollaborationPolicyProfile,
    CollaborationRunState,
    CollaborationState,
    WorkerState,
)

logger = logging.getLogger(__name__)

_STATE_FILE = "collaboration_state.json"
_VERSION = "1"


def _default_serializer(obj: Any) -> Any:
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if hasattr(obj, "value"):
        return obj.value
    if hasattr(obj, "__dict__"):
        return obj.__dict__
    raise TypeError(f"Type {type(obj)} not serializable")


class CollaborationPersistence:
    """
    JSON persistence for collaboration state.
    """

    def __init__(self, state_dir: Path) -> None:
        self._dir = state_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, collaboration_id: Optional[str] = None) -> Path:
        if collaboration_id:
            return self._dir / f"collab_{collaboration_id}.json"
        return self._dir / _STATE_FILE

    def save(self, state: CollaborationRunState) -> None:
        """Persist the entire run state atomically."""
        path = self._path(state.collaboration_id)
        default_path = self._path(None)

        payload = {
            "version": _VERSION,
            "run_id": state.run_id,
            "collaboration_id": state.collaboration_id,
            "state": state.state.value,
            "mode": state.mode.value,
            "policy": state.policy.value,
            "cancelled": state.cancelled,
            "created_at": state.created_at.isoformat(),
            "updated_at": datetime.now(tz=timezone.utc).isoformat(),
            "worker_states": {
                wid: ws.value for wid, ws in state.worker_states.items()
            },
            "assignments": {
                aid: {"assignment_id": a.assignment_id, "role": a.role.value, "objective": a.objective, "dependencies": list(a.dependencies)}
                for aid, a in state.assignments.items()
            },
            "accepted_assignments": list(state.worker_reviews.keys()),
            "integration_done": state.integration_result is not None,
        }

        for target in (path, default_path):
            tmp_path = target.with_suffix(".tmp")
            try:
                with open(tmp_path, "w") as f:
                    json.dump(payload, f, indent=2, default=_default_serializer)
                os.replace(tmp_path, target)
            except Exception as exc:
                logger.error("CollaborationPersistence: save failed: %s", exc)

    def load(self, collaboration_id: Optional[str] = None) -> Optional[CollaborationRunState]:
        """Load persisted state into CollaborationRunState. Returns None if not found."""
        path = self._path(collaboration_id)
        if not path.exists() and collaboration_id is None:
            # Fallback to any collab_*.json
            files = list(self._dir.glob("collab_*.json"))
            if files:
                path = files[0]

        if not path.exists():
            return None

        try:
            with open(path) as f:
                data = json.load(f)

            b = CollaborationBudget(4, 2, 60, 240, 400000, Decimal("2.00"), 600, 2, 2)
            st_val = CollaborationState(data.get("state", "completed"))
            pol_val = CollaborationPolicyProfile(data.get("policy", "CONTROLLED_PARALLEL"))
            mode_val = CollaborationMode(data.get("mode", "SINGLE_AGENT"))

            state = CollaborationRunState(
                run_id=data.get("run_id", "run-0"),
                collaboration_id=data.get("collaboration_id", "collab-0"),
                state=st_val,
                policy=pol_val,
                budget=b,
                cancelled=data.get("cancelled", False),
                mode=mode_val,
            )
            return state

        except Exception as exc:
            logger.error("CollaborationPersistence: load failed for %s: %s",
                         collaboration_id, exc)
            return None

    def delete(self, collaboration_id: str) -> bool:
        path = self._path(collaboration_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_collaboration_ids(self) -> list[str]:
        return [
            f.stem.removeprefix("collab_")
            for f in self._dir.glob("collab_*.json")
        ]
