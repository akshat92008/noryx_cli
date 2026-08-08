"""Version compatibility manager for extensions."""

from __future__ import annotations

import re
from dataclasses import dataclass

from nexus import __version__ as NEXUS_VERSION
from nexus.platform.manifest import ExtensionManifest

_VERSION_PART = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True)
class CompatibilityResult:
    """Result of a compatibility check."""

    compatible: bool
    reason: str = ""
    nexus_version: str = ""
    extension_version: str = ""
    min_required: str = ""
    max_allowed: str = ""


def _parse_version(version: str) -> tuple[int, int, int]:
    match = _VERSION_PART.match(version.strip())
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


class CompatibilityManager:
    """Check extension compatibility with current Nexus version."""

    def __init__(self, nexus_version: str = NEXUS_VERSION):
        self.nexus_version = nexus_version
        self._nexus_tuple = _parse_version(nexus_version)

    def check(self, manifest: ExtensionManifest) -> CompatibilityResult:
        """Check if an extension is compatible with the current Nexus version."""
        min_tuple = _parse_version(manifest.min_nexus_version)
        ext_version = manifest.version

        if self._nexus_tuple < min_tuple:
            return CompatibilityResult(
                compatible=False,
                reason=(
                    f"Nexus {self.nexus_version} is below minimum required "
                    f"{manifest.min_nexus_version}"
                ),
                nexus_version=self.nexus_version,
                extension_version=ext_version,
                min_required=manifest.min_nexus_version,
            )

        if manifest.max_nexus_version:
            max_tuple = _parse_version(manifest.max_nexus_version)
            if self._nexus_tuple > max_tuple:
                return CompatibilityResult(
                    compatible=False,
                    reason=(
                        f"Nexus {self.nexus_version} exceeds maximum supported "
                        f"{manifest.max_nexus_version}"
                    ),
                    nexus_version=self.nexus_version,
                    extension_version=ext_version,
                    max_allowed=manifest.max_nexus_version,
                )

        if manifest.api_version != "nexus.extensions.v1":
            return CompatibilityResult(
                compatible=False,
                reason=f"Unsupported API version: {manifest.api_version}",
                nexus_version=self.nexus_version,
                extension_version=ext_version,
            )

        return CompatibilityResult(
            compatible=True,
            nexus_version=self.nexus_version,
            extension_version=ext_version,
            min_required=manifest.min_nexus_version,
            max_allowed=manifest.max_nexus_version,
        )

    def check_update(
        self,
        current: ExtensionManifest,
        updated: ExtensionManifest,
    ) -> CompatibilityResult:
        """Check if an update is safe (no silent permission escalation)."""
        base = self.check(updated)
        if not base.compatible:
            return base

        new_caps = updated.capabilities - current.capabilities
        new_perms = set(updated.permissions) - set(current.permissions)
        if new_caps or new_perms:
            return CompatibilityResult(
                compatible=False,
                reason=(
                    f"Update adds new capabilities/permissions requiring re-approval: "
                    f"capabilities={sorted(new_caps)}, permissions={sorted(new_perms)}"
                ),
                nexus_version=self.nexus_version,
                extension_version=updated.version,
            )

        return base
