"""Quarantine system for misbehaving extensions."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexus.platform.registry import PlatformExtensionRegistry


@dataclass
class QuarantineEntry:
    """A quarantined extension."""

    extension_name: str
    reason: str
    quarantined_at: float
    error_count: int = 0
    auto_release_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "extension_name": self.extension_name,
            "reason": self.reason,
            "quarantined_at": self.quarantined_at,
            "error_count": self.error_count,
            "auto_release_at": self.auto_release_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "QuarantineEntry":
        return cls(
            extension_name=data["extension_name"],
            reason=data["reason"],
            quarantined_at=data.get("quarantined_at", time.time()),
            error_count=data.get("error_count", 0),
            auto_release_at=data.get("auto_release_at", 0.0),
        )


class QuarantineManager:
    """Quarantine extensions that fail health checks or violate policies."""

    DEFAULT_QUARANTINE_DURATION = 3600.0  # 1 hour

    def __init__(
        self,
        registry: PlatformExtensionRegistry,
        state_dir: Path | None = None,
    ):
        self.registry = registry
        if state_dir is None:
            state_dir = registry.extensions_dir.parent
        self._state_dir = Path(state_dir)
        self._path = self._state_dir / "quarantine.json"
        self._entries: dict[str, QuarantineEntry] = self._load()

    def _load(self) -> dict[str, QuarantineEntry]:
        if not self._path.is_file():
            return {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return {
                e["extension_name"]: QuarantineEntry.from_dict(e)
                for e in data.get("entries", [])
            }
        except (json.JSONDecodeError, OSError, KeyError):
            return {}

    def _save(self) -> None:
        data = {"entries": [e.to_dict() for e in self._entries.values()]}
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def quarantine(
        self,
        name: str,
        reason: str,
        *,
        duration: float = 0.0,
    ) -> QuarantineEntry:
        """Quarantine an extension."""
        duration = duration or self.DEFAULT_QUARANTINE_DURATION
        entry = QuarantineEntry(
            extension_name=name,
            reason=reason,
            quarantined_at=time.time(),
            auto_release_at=time.time() + duration if duration > 0 else 0.0,
        )
        self._entries[name] = entry

        record = self.registry.get(name)
        if record:
            record.enabled = False
            record.health_status = "quarantined"
            record.error = reason

        self.registry.disable(name)
        self._save()
        return entry

    def release(self, name: str) -> bool:
        """Release an extension from quarantine."""
        if name not in self._entries:
            return False
        del self._entries[name]

        record = self.registry.get(name)
        if record:
            record.health_status = "unknown"
            record.error = ""

        self._save()
        return True

    def is_quarantined(self, name: str) -> bool:
        entry = self._entries.get(name)
        if not entry:
            return False
        if entry.auto_release_at and time.time() > entry.auto_release_at:
            self.release(name)
            return False
        return True

    def list_quarantined(self) -> list[QuarantineEntry]:
        self._check_auto_releases()
        return list(self._entries.values())

    def _check_auto_releases(self) -> None:
        now = time.time()
        to_release = [
            name for name, entry in self._entries.items()
            if entry.auto_release_at and now > entry.auto_release_at
        ]
        for name in to_release:
            self.release(name)
