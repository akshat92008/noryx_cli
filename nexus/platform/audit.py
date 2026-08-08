"""Audit logging for extension operations."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class AuditAction(str, Enum):
    INSTALL = "install"
    UNINSTALL = "uninstall"
    ENABLE = "enable"
    DISABLE = "disable"
    UPDATE = "update"
    PERMISSION_GRANT = "permission_grant"
    PERMISSION_REVOKE = "permission_revoke"
    TOOL_INVOKE = "tool_invoke"
    CAPABILITY_CHECK = "capability_check"
    HEALTH_CHECK = "health_check"
    QUARANTINE = "quarantine"
    RELEASE = "release"
    MCP_CONNECT = "mcp_connect"
    MCP_TOOL_CALL = "mcp_tool_call"
    VALIDATION = "validation"
    PACKAGE = "package"


@dataclass
class AuditRecord:
    """Single audit log entry."""

    action: AuditAction
    extension_name: str
    timestamp: float = field(default_factory=time.time)
    actor: str = "system"
    details: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "extension_name": self.extension_name,
            "timestamp": self.timestamp,
            "actor": self.actor,
            "details": self.details,
            "success": self.success,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AuditRecord":
        return cls(
            action=AuditAction(data["action"]),
            extension_name=data.get("extension_name", ""),
            timestamp=data.get("timestamp", time.time()),
            actor=data.get("actor", "system"),
            details=data.get("details", {}),
            success=data.get("success", True),
            error=data.get("error", ""),
        )


class AuditLogger:
    """Append-only audit log for extension platform operations."""

    MAX_RECORDS = 10000

    def __init__(self, state_dir: Path | None = None):
        if state_dir is None:
            state_dir = Path.home() / ".nexusai" / "extensions"
        self._state_dir = Path(state_dir)
        self._state_dir.mkdir(parents=True, exist_ok=True)
        self._path = self._state_dir / "audit.jsonl"
        self._records: list[AuditRecord] = self._load_recent()

    def _load_recent(self) -> list[AuditRecord]:
        if not self._path.is_file():
            return []
        records: list[AuditRecord] = []
        try:
            lines = self._path.read_text(encoding="utf-8").strip().split("\n")
            for line in lines[-self.MAX_RECORDS:]:
                if line.strip():
                    records.append(AuditRecord.from_dict(json.loads(line)))
        except (json.JSONDecodeError, OSError):
            pass
        return records

    def log(
        self,
        action: AuditAction,
        extension_name: str,
        *,
        actor: str = "system",
        details: dict[str, Any] | None = None,
        success: bool = True,
        error: str = "",
    ) -> AuditRecord:
        record = AuditRecord(
            action=action,
            extension_name=extension_name,
            actor=actor,
            details=details or {},
            success=success,
            error=error,
        )
        self._records.append(record)
        if len(self._records) > self.MAX_RECORDS:
            self._records = self._records[-self.MAX_RECORDS:]

        with open(self._path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record.to_dict()) + "\n")

        return record

    def query(
        self,
        *,
        extension_name: str = "",
        action: AuditAction | None = None,
        limit: int = 100,
    ) -> list[AuditRecord]:
        results = self._records
        if extension_name:
            results = [r for r in results if r.extension_name == extension_name]
        if action:
            results = [r for r in results if r.action == action]
        return results[-limit:]

    def export(self, output_path: Path) -> int:
        """Export audit log to a file."""
        count = 0
        with open(output_path, "w", encoding="utf-8") as f:
            for record in self._records:
                f.write(json.dumps(record.to_dict()) + "\n")
                count += 1
        return count
