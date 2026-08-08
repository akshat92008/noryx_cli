"""Extension lifecycle manager."""

from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from nexus.platform.compatibility import CompatibilityManager
from nexus.platform.manifest import ExtensionManifest, ManifestValidationError
from nexus.platform.registry import ExtensionRecord, PlatformExtensionRegistry
from nexus.platform.verification import PackageVerifier

logger = logging.getLogger(__name__)


class ExtensionState(str, Enum):
    """Lifecycle states for an extension."""

    DISCOVERED = "discovered"
    INSTALLED = "installed"
    ENABLED = "enabled"
    DISABLED = "disabled"
    QUARANTINED = "quarantined"
    ERROR = "error"
    REMOVED = "removed"


@dataclass
class LifecycleEvent:
    """Audit event for lifecycle transitions."""

    extension_name: str
    from_state: ExtensionState
    to_state: ExtensionState
    timestamp: float
    reason: str = ""


class ExtensionLifecycleManager:
    """Manage extension install, update, enable, disable, and remove."""

    def __init__(
        self,
        registry: PlatformExtensionRegistry | None = None,
        *,
        working_dir: str = "",
    ):
        self.registry = registry or PlatformExtensionRegistry(working_dir=working_dir)
        self.verifier = PackageVerifier()
        self.compatibility = CompatibilityManager()
        self._events: list[LifecycleEvent] = []

    def install(
        self,
        source_dir: Path,
        *,
        enable: bool = False,
        force: bool = False,
    ) -> tuple[bool, str, ExtensionRecord | None]:
        """Install an extension from a directory."""
        source_dir = source_dir.resolve()
        if not source_dir.is_dir():
            return False, f"Source not found: {source_dir}", None

        verification = self.verifier.verify_directory(source_dir)
        if not verification.valid:
            return False, f"Verification failed: {'; '.join(verification.errors)}", None

        manifest_file = source_dir / "extension.json"
        if not manifest_file.is_file():
            manifest_file = source_dir / "plugin.json"

        try:
            manifest = ExtensionManifest.from_file(manifest_file)
        except ManifestValidationError as exc:
            return False, str(exc), None

        compat = self.compatibility.check(manifest)
        if not compat.compatible:
            return False, compat.reason, None

        existing = self.registry.get(manifest.name)
        if existing and not force:
            if existing.manifest.version == manifest.version:
                return False, f"Extension '{manifest.name}' v{manifest.version} already installed", None

        dest = self.registry.extensions_dir / manifest.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(source_dir, dest)

        record = ExtensionRecord(
            manifest=manifest,
            install_path=str(dest),
            enabled=enable,
            source="global",
            content_hash=verification.content_hash,
            installed_at=time.time(),
            health_status="healthy",
        )
        self.registry.register(record)
        self._emit(manifest.name, ExtensionState.DISCOVERED, ExtensionState.INSTALLED)

        if enable:
            self.enable(manifest.name)

        return True, f"Installed {manifest.name} v{manifest.version}", record

    def update(
        self,
        name: str,
        source_dir: Path,
    ) -> tuple[bool, str]:
        """Update an installed extension."""
        existing = self.registry.get(name)
        if not existing:
            return False, f"Extension '{name}' not installed"

        manifest_file = source_dir / "extension.json"
        if not manifest_file.is_file():
            manifest_file = source_dir / "plugin.json"

        try:
            new_manifest = ExtensionManifest.from_file(manifest_file)
        except ManifestValidationError as exc:
            return False, str(exc)

        update_check = self.compatibility.check_update(existing.manifest, new_manifest)
        if not update_check.compatible:
            return False, update_check.reason

        was_enabled = existing.enabled
        self.registry.disable(name)
        ok, msg, _ = self.install(source_dir, enable=was_enabled, force=True)
        return ok, msg

    def enable(self, name: str) -> tuple[bool, str]:
        record = self.registry.get(name)
        if not record:
            return False, f"Extension '{name}' not found"

        compat = self.compatibility.check(record.manifest)
        if not compat.compatible:
            return False, compat.reason

        self.registry.enable(name)
        self._emit(name, ExtensionState.DISABLED, ExtensionState.ENABLED)
        return True, f"Enabled {name}"

    def disable(self, name: str) -> tuple[bool, str]:
        if not self.registry.disable(name):
            return False, f"Extension '{name}' not found"
        self._emit(name, ExtensionState.ENABLED, ExtensionState.DISABLED)
        return True, f"Disabled {name}"

    def remove(self, name: str) -> tuple[bool, str]:
        record = self.registry.get(name)
        if not record:
            return False, f"Extension '{name}' not found"

        install_path = Path(record.install_path)
        if install_path.is_dir():
            shutil.rmtree(install_path, ignore_errors=True)

        self.registry.remove(name)
        self._emit(name, ExtensionState.INSTALLED, ExtensionState.REMOVED)
        return True, f"Removed {name}"

    def get_state(self, name: str) -> ExtensionState:
        record = self.registry.get(name)
        if not record:
            return ExtensionState.REMOVED
        if record.health_status == "quarantined":
            return ExtensionState.QUARANTINED
        if record.health_status == "error":
            return ExtensionState.ERROR
        if record.enabled:
            return ExtensionState.ENABLED
        return ExtensionState.DISABLED

    def _emit(
        self,
        name: str,
        from_state: ExtensionState,
        to_state: ExtensionState,
        reason: str = "",
    ) -> None:
        self._events.append(LifecycleEvent(
            extension_name=name,
            from_state=from_state,
            to_state=to_state,
            timestamp=time.time(),
            reason=reason,
        ))

    @property
    def events(self) -> list[LifecycleEvent]:
        return list(self._events)
