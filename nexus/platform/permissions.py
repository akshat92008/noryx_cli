"""Permission grants with scoped approval (Once / Run / Repository / Global)."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class PermissionScope(str, Enum):
    """Scope of a permission grant."""

    ONCE = "once"           # Single invocation
    RUN = "run"             # Current agent run
    REPOSITORY = "repository"  # Current repository
    GLOBAL = "global"       # All repositories (requires explicit opt-in)


@dataclass
class PermissionGrant:
    """A granted permission for an extension capability."""

    extension_name: str
    capability: str
    scope: PermissionScope
    granted_at: float = field(default_factory=time.time)
    granted_by: str = "user"
    content_hash: str = ""
    repository: str = ""
    expires_at: float = 0.0

    def is_valid(self, *, repository: str = "", content_hash: str = "") -> bool:
        """Check if this grant is still valid."""
        if self.expires_at and time.time() > self.expires_at:
            return False
        if self.scope == PermissionScope.REPOSITORY and repository:
            if self.repository and self.repository != repository:
                return False
        if self.content_hash and content_hash and self.content_hash != content_hash:
            return False  # Content changed since approval
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "extension_name": self.extension_name,
            "capability": self.capability,
            "scope": self.scope.value,
            "granted_at": self.granted_at,
            "granted_by": self.granted_by,
            "content_hash": self.content_hash,
            "repository": self.repository,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PermissionGrant":
        return cls(
            extension_name=data["extension_name"],
            capability=data["capability"],
            scope=PermissionScope(data.get("scope", "once")),
            granted_at=data.get("granted_at", time.time()),
            granted_by=data.get("granted_by", "user"),
            content_hash=data.get("content_hash", ""),
            repository=data.get("repository", ""),
            expires_at=data.get("expires_at", 0.0),
        )


class PermissionStore:
    """Persistent store for extension permission grants."""

    def __init__(self, state_dir: Path | None = None):
        if state_dir is None:
            state_dir = Path.home() / ".nexusai" / "extensions"
        self.state_dir = Path(state_dir)
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self._path = self.state_dir / "permissions.json"
        self._grants: list[PermissionGrant] = self._load()

    def _load(self) -> list[PermissionGrant]:
        if not self._path.is_file():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return [PermissionGrant.from_dict(g) for g in data.get("grants", [])]
        except (json.JSONDecodeError, OSError, KeyError):
            return []

    def _save(self) -> None:
        data = {"grants": [g.to_dict() for g in self._grants]}
        self._path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def grant(
        self,
        extension_name: str,
        capability: str,
        scope: PermissionScope,
        *,
        content_hash: str = "",
        repository: str = "",
        granted_by: str = "user",
    ) -> PermissionGrant:
        """Grant a permission."""
        grant = PermissionGrant(
            extension_name=extension_name,
            capability=capability,
            scope=scope,
            content_hash=content_hash,
            repository=repository,
            granted_by=granted_by,
        )
        self._grants.append(grant)
        self._save()
        return grant

    def revoke(self, extension_name: str, capability: str = "") -> int:
        """Revoke permissions. Returns count revoked."""
        before = len(self._grants)
        if capability:
            self._grants = [
                g for g in self._grants
                if not (g.extension_name == extension_name and g.capability == capability)
            ]
        else:
            self._grants = [g for g in self._grants if g.extension_name != extension_name]
        self._save()
        return before - len(self._grants)

    def check(
        self,
        extension_name: str,
        capability: str,
        *,
        repository: str = "",
        content_hash: str = "",
    ) -> PermissionGrant | None:
        """Check if a valid grant exists."""
        for grant in reversed(self._grants):
            if grant.extension_name != extension_name or grant.capability != capability:
                continue
            if grant.is_valid(repository=repository, content_hash=content_hash):
                return grant
        return None

    def consume_once(
        self,
        extension_name: str,
        capability: str,
        *,
        repository: str = "",
        content_hash: str = "",
    ) -> bool:
        """Consume a ONCE-scoped grant. Returns True if consumed."""
        for i, grant in enumerate(self._grants):
            if (
                grant.extension_name == extension_name
                and grant.capability == capability
                and grant.scope == PermissionScope.ONCE
                and grant.is_valid(repository=repository, content_hash=content_hash)
            ):
                self._grants.pop(i)
                self._save()
                return True
        return False

    def list_grants(self, extension_name: str = "") -> list[PermissionGrant]:
        if extension_name:
            return [g for g in self._grants if g.extension_name == extension_name]
        return list(self._grants)

    def list_for_extension(self, extension_name: str) -> list[dict[str, Any]]:
        return [g.to_dict() for g in self.list_grants(extension_name)]
