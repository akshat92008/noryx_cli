"""Extension registry with safe discovery and lazy loading."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexus.platform.manifest import ExtensionManifest, ManifestValidationError
from nexus.platform.permissions import PermissionStore

logger = logging.getLogger(__name__)

GLOBAL_EXTENSIONS_DIR = Path.home() / ".nexusai" / "extensions" / "installed"
LOCAL_EXTENSIONS_DIR_NAME = "nexus_extensions"
REGISTRY_FILE = "registry.json"


@dataclass
class ExtensionRecord:
    """A registered extension entry."""

    manifest: ExtensionManifest
    install_path: str
    enabled: bool = False
    source: str = "global"  # global | local | package
    content_hash: str = ""
    installed_at: float = 0.0
    last_health_check: float = 0.0
    health_status: str = "unknown"
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.manifest.name,
            "version": self.manifest.version,
            "extension_type": self.manifest.extension_type,
            "install_path": self.install_path,
            "enabled": self.enabled,
            "source": self.source,
            "content_hash": self.content_hash,
            "installed_at": self.installed_at,
            "health_status": self.health_status,
            "error": self.error,
        }


class PlatformExtensionRegistry:
    """Safe extension registry with offline support and lazy loading."""

    def __init__(
        self,
        *,
        working_dir: str = "",
        extensions_dir: Path | None = None,
    ):
        self.working_dir = Path(working_dir).resolve() if working_dir else Path.cwd()
        self.extensions_dir = extensions_dir or GLOBAL_EXTENSIONS_DIR
        self.extensions_dir.mkdir(parents=True, exist_ok=True)
        self._registry_path = self.extensions_dir.parent / REGISTRY_FILE
        self._records: dict[str, ExtensionRecord] = {}
        self._loaded_instances: dict[str, Any] = {}
        self._permission_store = PermissionStore(self.extensions_dir.parent)
        self._load_registry()

    def _load_registry(self) -> None:
        if not self._registry_path.is_file():
            return
        try:
            data = json.loads(self._registry_path.read_text(encoding="utf-8"))
            for entry in data.get("extensions", []):
                manifest_path = Path(entry.get("install_path", "")) / "extension.json"
                if not manifest_path.is_file():
                    manifest_path = Path(entry.get("install_path", "")) / "plugin.json"
                if manifest_path.is_file():
                    try:
                        manifest = ExtensionManifest.from_file(manifest_path)
                        self._records[manifest.name] = ExtensionRecord(
                            manifest=manifest,
                            install_path=entry.get("install_path", ""),
                            enabled=entry.get("enabled", False),
                            source=entry.get("source", "global"),
                            content_hash=entry.get("content_hash", ""),
                            installed_at=entry.get("installed_at", 0.0),
                            health_status=entry.get("health_status", "unknown"),
                        )
                    except ManifestValidationError as exc:
                        logger.warning("Skipping invalid registry entry: %s", exc)
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Failed to load extension registry: %s", exc)

    def _save_registry(self) -> None:
        data = {
            "version": "1.0.0",
            "extensions": [r.to_dict() for r in self._records.values()],
        }
        self._registry_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def discover(self) -> list[ExtensionRecord]:
        """Scan install directories and update registry."""
        discovered: list[ExtensionRecord] = []

        search_dirs = [self.extensions_dir]
        local_dir = self.working_dir / LOCAL_EXTENSIONS_DIR_NAME
        if local_dir.is_dir():
            search_dirs.append(local_dir)

        for base_dir in search_dirs:
            if not base_dir.is_dir():
                continue
            source = "local" if base_dir == local_dir else "global"
            for item in sorted(base_dir.iterdir()):
                if not item.is_dir() or item.name.startswith("_"):
                    continue
                record = self._discover_extension_dir(item, source)
                if record:
                    discovered.append(record)
                    self._records[record.manifest.name] = record

        self._save_registry()
        return discovered

    def _discover_extension_dir(self, ext_dir: Path, source: str) -> ExtensionRecord | None:
        manifest_file = ext_dir / "extension.json"
        if not manifest_file.is_file():
            manifest_file = ext_dir / "plugin.json"
        if not manifest_file.is_file():
            return None

        try:
            manifest = ExtensionManifest.from_file(manifest_file)
        except ManifestValidationError as exc:
            logger.warning("Invalid manifest in %s: %s", ext_dir, exc)
            return None

        from nexus.plugins.worker import PluginManifest, compute_plugin_hash

        plugin_manifest = PluginManifest(
            name=manifest.name,
            version=manifest.version,
            description=manifest.description,
            author=manifest.author,
            entry_point=manifest.entry_point,
            capabilities=manifest.capabilities,
            required_tools=list(manifest.required_tools),
            required_paths=list(manifest.required_paths),
            required_env=list(manifest.required_env),
            network_access=manifest.network_access,
        )
        content_hash = compute_plugin_hash(ext_dir, plugin_manifest)

        existing = self._records.get(manifest.name)
        enabled = existing.enabled if existing else False

        return ExtensionRecord(
            manifest=manifest,
            install_path=str(ext_dir.resolve()),
            enabled=enabled,
            source=source,
            content_hash=content_hash,
        )

    def list_extensions(self, *, enabled_only: bool = False) -> list[ExtensionRecord]:
        if enabled_only:
            return [r for r in self._records.values() if r.enabled]
        return list(self._records.values())

    def get(self, name: str) -> ExtensionRecord | None:
        return self._records.get(name)

    def register(self, record: ExtensionRecord) -> None:
        self._records[record.manifest.name] = record
        self._save_registry()

    def enable(self, name: str) -> bool:
        record = self._records.get(name)
        if not record:
            return False
        record.enabled = True
        self._save_registry()
        return True

    def disable(self, name: str) -> bool:
        record = self._records.get(name)
        if not record:
            return False
        record.enabled = False
        self._loaded_instances.pop(name, None)
        self._save_registry()
        return True

    def remove(self, name: str) -> bool:
        if name not in self._records:
            return False
        del self._records[name]
        self._loaded_instances.pop(name, None)
        self._permission_store.revoke(name)
        self._save_registry()
        return True

    @property
    def permission_store(self) -> PermissionStore:
        return self._permission_store

    def get_enabled_by_type(self, extension_type: str) -> list[ExtensionRecord]:
        return [
            r for r in self._records.values()
            if r.enabled and r.manifest.extension_type == extension_type
        ]
