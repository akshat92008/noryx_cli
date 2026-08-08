"""Secure extension runtime with process isolation."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexus.platform.manifest import ExtensionManifest
from nexus.platform.permissions import PermissionStore
from nexus.platform.registry import ExtensionRecord
from nexus.plugins.worker import (
    PluginManifest,
    PluginWorker,
    PluginWorkerResult,
)

logger = logging.getLogger(__name__)


@dataclass
class RuntimeContext:
    """Execution context for an extension."""

    extension_name: str
    working_dir: str
    repository: str = ""
    session_id: str = ""
    granted_capabilities: frozenset[str] = field(default_factory=frozenset)


class SecureExtensionRuntime:
    """Run extensions in isolated subprocess workers with capability enforcement.

    Extensions NEVER run in the main Nexus process. All tool invocations
    go through the Tool Gateway. All mutations go through the Transaction Engine.
    """

    def __init__(
        self,
        permission_store: PermissionStore | None = None,
        *,
        working_dir: str = "",
    ):
        self.working_dir = working_dir
        self._permission_store = permission_store or PermissionStore()
        self._workers: dict[str, PluginWorker] = {}
        self._manifests: dict[str, ExtensionManifest] = {}

    def start(self, record: ExtensionRecord) -> tuple[bool, str]:
        """Start an isolated worker for an extension."""
        ext_dir = Path(record.install_path)
        manifest = record.manifest

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

        workspace = Path(self.working_dir) if self.working_dir else None
        worker = PluginWorker(
            ext_dir,
            plugin_manifest,
            workspace_root=workspace,
        )
        result = worker.start()
        if not result.success:
            return False, result.error or "Worker start failed"

        self._workers[manifest.name] = worker
        self._manifests[manifest.name] = manifest
        return True, f"Started {manifest.name} in isolated worker"

    def stop(self, name: str) -> None:
        worker = self._workers.pop(name, None)
        if worker:
            worker.stop()
        self._manifests.pop(name, None)

    def stop_all(self) -> None:
        for name in list(self._workers):
            self.stop(name)

    def call(
        self,
        name: str,
        method: str,
        *,
        required_capability: str = "",
        context: RuntimeContext | None = None,
        **kwargs: Any,
    ) -> PluginWorkerResult:
        """Call a method on an extension worker with capability check."""
        worker = self._workers.get(name)
        if not worker:
            return PluginWorkerResult(False, error=f"Extension '{name}' not running")

        if required_capability:
            manifest = self._manifests.get(name)
            if manifest and required_capability not in manifest.capabilities:
                return PluginWorkerResult(
                    False,
                    error=f"Extension '{name}' lacks capability '{required_capability}'",
                )

            grant = self._permission_store.check(
                name,
                required_capability,
                repository=context.repository if context else "",
            )
            if not grant:
                return PluginWorkerResult(
                    False,
                    error=f"Permission denied: '{required_capability}' not granted for '{name}'",
                )

            if grant.scope.value == "once":
                self._permission_store.consume_once(
                    name,
                    required_capability,
                    repository=context.repository if context else "",
                )

        return worker.call(method, **kwargs)

    def execute_tool(
        self,
        name: str,
        tool_name: str,
        arguments: dict[str, Any],
        *,
        context: RuntimeContext | None = None,
    ) -> PluginWorkerResult:
        """Execute a tool provided by an extension."""
        return self.call(
            name,
            "execute_tool",
            required_capability="tool_invoke",
            context=context,
            tool_name=tool_name,
            arguments=arguments,
        )

    def get_tools(self, name: str) -> list[dict]:
        result = self.call(name, "get_tools")
        if result.success and isinstance(result.data, list):
            return result.data
        return []

    def is_running(self, name: str) -> bool:
        worker = self._workers.get(name)
        return worker is not None and worker._process is not None and worker._process.poll() is None

    def running_extensions(self) -> list[str]:
        return [name for name in self._workers if self.is_running(name)]
