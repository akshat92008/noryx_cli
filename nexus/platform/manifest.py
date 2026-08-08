"""Extension manifest system with schema validation."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexus.platform.capabilities import validate_capabilities

EXTENSION_API_VERSION = "nexus.extensions.v1"
MANIFEST_VERSION = "1.0.0"

EXTENSION_TYPES = frozenset({
    "tool",
    "provider",
    "context_source",
    "repository_analyzer",
    "verification_check",
    "planning_extension",
    "routing_policy",
    "event_subscriber",
    "mcp_server",
    "mcp_client",
    "plugin",
})

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{1,63}$")
_VERSION_PATTERN = re.compile(r"^\d+\.\d+\.\d+(-[\w.]+)?(\+[\w.]+)?$")


class ManifestValidationError(Exception):
    """Raised when an extension manifest fails validation."""


@dataclass(frozen=True)
class ExtensionManifest:
    """Validated extension manifest (extension.json or plugin.json)."""

    name: str
    version: str
    extension_type: str
    api_version: str = EXTENSION_API_VERSION
    description: str = ""
    author: str = ""
    entry_point: str = "__init__.py"
    capabilities: frozenset[str] = frozenset()
    required_tools: tuple[str, ...] = ()
    required_paths: tuple[str, ...] = ()
    required_env: tuple[str, ...] = ()
    network_access: bool = False
    min_nexus_version: str = "3.0.0"
    max_nexus_version: str = ""
    dependencies: tuple[str, ...] = ()
    permissions: tuple[str, ...] = ()
    content_hash: str = ""
    manifest_path: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any], *, manifest_path: str = "") -> "ExtensionManifest":
        """Parse and validate manifest data."""
        errors = validate_manifest(data)
        if errors:
            raise ManifestValidationError("; ".join(errors))

        ext_type = data.get("extension_type") or data.get("type", "plugin")
        caps = data.get("capabilities", [])
        perms = data.get("permissions", [])

        return cls(
            name=data["name"],
            version=str(data["version"]),
            extension_type=ext_type,
            api_version=data.get("api_version", EXTENSION_API_VERSION),
            description=data.get("description", ""),
            author=data.get("author", ""),
            entry_point=data.get("entry_point", "__init__.py"),
            capabilities=frozenset(caps) if caps else frozenset(),
            required_tools=tuple(data.get("required_tools", [])),
            required_paths=tuple(data.get("required_paths", [])),
            required_env=tuple(data.get("required_env", [])),
            network_access=bool(data.get("network_access", False)),
            min_nexus_version=data.get("min_nexus_version", "3.0.0"),
            max_nexus_version=data.get("max_nexus_version", ""),
            dependencies=tuple(data.get("dependencies", [])),
            permissions=tuple(perms) if perms else (),
            manifest_path=manifest_path,
        )

    @classmethod
    def from_file(cls, path: Path) -> "ExtensionManifest":
        """Load and validate manifest from file."""
        if not path.is_file():
            raise ManifestValidationError(f"Manifest not found: {path}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise ManifestValidationError(f"Invalid manifest JSON: {exc}") from exc
        return cls.from_dict(data, manifest_path=str(path.resolve()))

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "extension_type": self.extension_type,
            "api_version": self.api_version,
            "description": self.description,
            "author": self.author,
            "entry_point": self.entry_point,
            "capabilities": sorted(self.capabilities),
            "required_tools": list(self.required_tools),
            "required_paths": list(self.required_paths),
            "required_env": list(self.required_env),
            "network_access": self.network_access,
            "min_nexus_version": self.min_nexus_version,
            "max_nexus_version": self.max_nexus_version,
            "dependencies": list(self.dependencies),
            "permissions": list(self.permissions),
        }

    def display_summary(self) -> str:
        lines = [
            f"Extension: {self.name} v{self.version}",
            f"  Type: {self.extension_type}",
            f"  API: {self.api_version}",
        ]
        if self.description:
            lines.append(f"  Description: {self.description}")
        if self.capabilities:
            lines.append(f"  Capabilities: {', '.join(sorted(self.capabilities))}")
        if self.permissions:
            lines.append(f"  Permissions: {', '.join(sorted(self.permissions))}")
        if self.network_access:
            lines.append("  Network access: YES")
        return "\n".join(lines)


def validate_manifest(data: dict[str, Any]) -> list[str]:
    """Validate manifest data and return list of error messages."""
    errors: list[str] = []

    name = data.get("name", "")
    if not name or not isinstance(name, str):
        errors.append("Missing required 'name' field")
    elif not _NAME_PATTERN.match(name):
        errors.append(f"Invalid name '{name}': must match ^[a-z][a-z0-9_-]{{1,63}}$")

    version = data.get("version", "")
    if not version:
        errors.append("Missing required 'version' field")
    elif not _VERSION_PATTERN.match(str(version)):
        errors.append(f"Invalid version '{version}': must be semver (e.g. 1.0.0)")

    ext_type = data.get("extension_type") or data.get("type", "plugin")
    if ext_type not in EXTENSION_TYPES:
        errors.append(
            f"Invalid extension_type '{ext_type}': must be one of {sorted(EXTENSION_TYPES)}"
        )

    api_version = data.get("api_version", EXTENSION_API_VERSION)
    if api_version != EXTENSION_API_VERSION:
        errors.append(
            f"Unsupported api_version '{api_version}': expected {EXTENSION_API_VERSION}"
        )

    entry_point = data.get("entry_point", "__init__.py")
    if entry_point and (".." in entry_point or entry_point.startswith("/")):
        errors.append(f"Invalid entry_point '{entry_point}': path traversal not allowed")

    caps = data.get("capabilities", [])
    if caps and not isinstance(caps, list):
        errors.append("'capabilities' must be a list")

    for cap in caps or []:
        if not isinstance(cap, str) or not cap:
            errors.append(f"Invalid capability entry: {cap!r}")

    if isinstance(caps, list):
        errors.extend(validate_capabilities(caps))

    for field_name in ("required_tools", "required_paths", "required_env", "dependencies", "permissions"):
        values = data.get(field_name, [])
        if values and not isinstance(values, list):
            errors.append(f"'{field_name}' must be a list")
            continue
        for value in values or []:
            if not isinstance(value, str) or not value:
                errors.append(f"Invalid {field_name} entry: {value!r}")

    for path_field in ("required_paths",):
        for value in data.get(path_field, []) or []:
            if ".." in value or str(value).startswith("/"):
                errors.append(f"Invalid {path_field} entry '{value}': path traversal not allowed")

    return errors
