"""Nexus Extension Platform — production-ready plugin SDK and MCP ecosystem.

Provides versioned extension SDK, manifest validation, capability-based
permissions, secure isolated runtime, registry, lifecycle management,
MCP gateway, and developer tooling.
"""

from nexus.platform.audit import AuditLogger, AuditRecord
from nexus.platform.capabilities import (
    EXTENSION_CAPABILITIES,
    Capability,
    CapabilitySet,
    validate_capabilities,
)
from nexus.platform.compatibility import CompatibilityManager, CompatibilityResult
from nexus.platform.health import ExtensionHealthMonitor, HealthStatus
from nexus.platform.lifecycle import ExtensionLifecycleManager, ExtensionState
from nexus.platform.manifest import (
    EXTENSION_API_VERSION,
    EXTENSION_TYPES,
    ExtensionManifest,
    ManifestValidationError,
    validate_manifest,
)
from nexus.platform.permissions import (
    PermissionGrant,
    PermissionScope,
    PermissionStore,
)
from nexus.platform.quarantine import QuarantineManager
from nexus.platform.registry import PlatformExtensionRegistry
from nexus.platform.runtime import SecureExtensionRuntime
from nexus.platform.verification import PackageVerifier, VerificationResult

__all__ = [
    "EXTENSION_API_VERSION",
    "EXTENSION_TYPES",
    "EXTENSION_CAPABILITIES",
    "AuditLogger",
    "AuditRecord",
    "Capability",
    "CapabilitySet",
    "CompatibilityManager",
    "CompatibilityResult",
    "ExtensionHealthMonitor",
    "ExtensionLifecycleManager",
    "ExtensionManifest",
    "ExtensionState",
    "HealthStatus",
    "ManifestValidationError",
    "PackageVerifier",
    "PermissionGrant",
    "PermissionScope",
    "PermissionStore",
    "PlatformExtensionRegistry",
    "QuarantineManager",
    "SecureExtensionRuntime",
    "VerificationResult",
    "validate_capabilities",
    "validate_manifest",
]
