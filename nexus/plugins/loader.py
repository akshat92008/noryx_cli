"""
Plugin Loader — secure auto-discovery and loading of plugin packages.

Security model:
  1. Plugins are DISABLED by default.
  2. Every plugin requires a ``plugin.json`` manifest.
  3. Empty manifest discovery must NOT evaluate as trusted (no ``all([])`` bypass).
  4. Trust is content-addressed: any byte change invalidates approval.
  5. Plugin code executes in an isolated subprocess worker, NOT in the Nexus process.
  6. Loading failures produce structured diagnostics, never silent ``except Exception: pass``.

Discovers plugins in:
  1. global ``~/.nexusai/plugins/``
  2. project-local ``nexus_plugins/`` (requires explicit approval)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexus.plugins.base import BasePlugin
from nexus.plugins.worker import (
    PluginLoadError,
    PluginManifest,
    PluginWorker,
    compute_plugin_hash,
)

logger = logging.getLogger(__name__)


@dataclass
class PluginDiagnostic:
    """Structured diagnostic from plugin loading."""

    plugin_dir: str
    status: str  # "loaded", "rejected", "error", "no_manifest", "trust_required"
    message: str
    manifest: PluginManifest | None = None


class PluginLoader:
    """
    Secure plugin discovery and loading.

    Loads manifests, validates trust, and runs plugin code in isolated workers.
    Never executes plugin code in the Nexus process.
    """

    def __init__(
        self,
        working_dir: str = "",
        *,
        plugins_enabled: bool = False,
        trust_checker: Any = None,
    ):
        self.working_dir = working_dir
        self.plugins_enabled = plugins_enabled
        self._trust_checker = trust_checker  # callable(path) -> bool
        self.plugins: dict[str, BasePlugin] = {}
        self.diagnostics: list[PluginDiagnostic] = []
        self._workers: list[PluginWorker] = []

    def discover_and_load(self) -> list[BasePlugin]:
        """Scan plugin directories and load approved plugins.

        Returns a list of loaded plugin proxy objects.  Each plugin runs
        in an isolated subprocess — no plugin code runs in this process.
        """
        self.diagnostics.clear()

        if not self.plugins_enabled:
            logger.info("Plugin loading is disabled (use --enable-plugins to enable)")
            return []

        search_paths: list[tuple[Path, str]] = []

        # Global home path (user-installed plugins)
        global_plugins = Path.home() / ".nexusai" / "plugins"
        if global_plugins.is_dir():
            search_paths.append((global_plugins, "global"))

        loaded_plugins: list[BasePlugin] = []
        for path, source in search_paths:
            for item in sorted(path.iterdir()):
                if item.is_dir() and not item.name.startswith("_"):
                    result = self._load_plugin_dir(item, source)
                    if result is not None:
                        self.plugins[result.name] = result
                        loaded_plugins.append(result)

        return loaded_plugins

    def discover_local_plugins(
        self,
        project_dir: Path,
    ) -> list[PluginDiagnostic]:
        """Discover local project plugins and return diagnostics.

        Local plugins are NEVER loaded automatically.  This method only
        returns diagnostics showing what would need approval.
        """
        local_dir = project_dir / "nexus_plugins"
        if not local_dir.is_dir():
            return []

        results: list[PluginDiagnostic] = []
        for item in sorted(local_dir.iterdir()):
            if not item.is_dir() or item.name.startswith("_"):
                continue

            manifest_file = item / "plugin.json"
            if not manifest_file.is_file():
                diag = PluginDiagnostic(
                    plugin_dir=str(item),
                    status="no_manifest",
                    message=f"Plugin directory lacks required plugin.json manifest: {item.name}",
                )
                results.append(diag)
                self.diagnostics.append(diag)
                continue

            try:
                manifest = PluginManifest.from_file(manifest_file)
            except PluginLoadError as exc:
                diag = PluginDiagnostic(
                    plugin_dir=str(item),
                    status="error",
                    message=str(exc),
                )
                results.append(diag)
                self.diagnostics.append(diag)
                continue

            # Check content-addressed trust
            content_hash = compute_plugin_hash(item, manifest)
            if self._trust_checker and self._trust_checker(manifest_file, expected_digest=content_hash):
                # Check if the content hash matches what was approved
                diag = PluginDiagnostic(
                    plugin_dir=str(item),
                    status="trust_required",
                    message=f"Local plugin '{manifest.name}' requires explicit approval. "
                    f"Capabilities: {manifest.display_capabilities()}",
                    manifest=manifest,
                )
            else:
                diag = PluginDiagnostic(
                    plugin_dir=str(item),
                    status="trust_required",
                    message=f"Local plugin '{manifest.name}' is not approved. "
                    f"Run 'nexus trust approve' to review and approve. "
                    f"Content hash: {content_hash[:12]}...",
                    manifest=manifest,
                )

            results.append(diag)
            self.diagnostics.append(diag)

        return results

    def _load_plugin_dir(self, plugin_dir: Path, source: str) -> BasePlugin | None:
        """Load a plugin from a directory, requiring manifest and trust."""
        manifest_file = plugin_dir / "plugin.json"

        # ── SECURITY: Require explicit manifest ──────────────────────────
        if not manifest_file.is_file():
            diag = PluginDiagnostic(
                plugin_dir=str(plugin_dir),
                status="no_manifest",
                message=f"Rejected: No plugin.json manifest in {plugin_dir.name}",
            )
            self.diagnostics.append(diag)
            logger.warning("Plugin %s rejected: no manifest", plugin_dir.name)
            return None

        # ── Parse manifest ───────────────────────────────────────────────
        try:
            manifest = PluginManifest.from_file(manifest_file)
        except PluginLoadError as exc:
            diag = PluginDiagnostic(
                plugin_dir=str(plugin_dir),
                status="error",
                message=str(exc),
            )
            self.diagnostics.append(diag)
            logger.warning("Plugin %s manifest error: %s", plugin_dir.name, exc)
            return None

        # ── Verify entry point exists ────────────────────────────────────
        entry_file = plugin_dir / manifest.entry_point
        if not entry_file.is_file():
            diag = PluginDiagnostic(
                plugin_dir=str(plugin_dir),
                status="error",
                message=f"Entry point not found: {manifest.entry_point}",
                manifest=manifest,
            )
            self.diagnostics.append(diag)
            logger.warning(
                "Plugin %s: missing entry point %s", plugin_dir.name, manifest.entry_point
            )
            return None

        # ── Content-addressed trust check ────────────────────────────────
        content_hash = compute_plugin_hash(plugin_dir, manifest)
        if self._trust_checker and not self._trust_checker(manifest_file, expected_digest=content_hash):
            diag = PluginDiagnostic(
                plugin_dir=str(plugin_dir),
                status="trust_required",
                message=f"Plugin '{manifest.name}' requires trust approval. "
                f"Hash: {content_hash[:12]}...",
                manifest=manifest,
            )
            self.diagnostics.append(diag)
            logger.info("Plugin %s: trust approval required", manifest.name)
            return None

        # ── Start isolated worker ────────────────────────────────────────
        workspace = Path(self.working_dir) if self.working_dir else None
        worker = PluginWorker(
            plugin_dir,
            manifest,
            workspace_root=workspace,
        )
        result = worker.start()

        if not result.success:
            diag = PluginDiagnostic(
                plugin_dir=str(plugin_dir),
                status="error",
                message=f"Worker start failed: {result.error}",
                manifest=manifest,
            )
            self.diagnostics.append(diag)
            logger.warning("Plugin %s worker failed: %s", manifest.name, result.error)
            return None

        self._workers.append(worker)

        # Create a proxy plugin object that forwards calls to the worker
        proxy = _PluginProxy(manifest, worker)

        diag = PluginDiagnostic(
            plugin_dir=str(plugin_dir),
            status="loaded",
            message=f"Plugin '{manifest.name}' v{manifest.version} loaded in isolated worker",
            manifest=manifest,
        )
        self.diagnostics.append(diag)
        logger.info("Plugin %s v%s loaded (isolated)", manifest.name, manifest.version)
        return proxy

    def get_diagnostics_summary(self) -> str:
        """Human-readable summary of plugin loading diagnostics."""
        if not self.diagnostics:
            return "🔌 Plugins: none discovered"
        lines = [f"🔌 Plugins ({len(self.diagnostics)} discovered)"]
        for diag in self.diagnostics:
            icons = {
                "loaded": "✅",
                "rejected": "🚫",
                "error": "❌",
                "no_manifest": "⚠️",
                "trust_required": "🔒",
            }
            icon = icons.get(diag.status, "❓")
            lines.append(f"  {icon} {diag.message}")
        return "\n".join(lines)

    def shutdown(self):
        """Stop all plugin workers."""
        for worker in self._workers:
            worker.stop()
        self._workers.clear()

    def __del__(self):
        self.shutdown()


class _PluginProxy(BasePlugin):
    """A BasePlugin facade that forwards calls to an isolated worker process."""

    def __init__(self, manifest: PluginManifest, worker: PluginWorker):
        super().__init__()
        self.name = manifest.name
        self.description = manifest.description
        self.version = manifest.version
        self.author = manifest.author
        self._manifest = manifest
        self._worker = worker

    def get_skills(self):
        # Skills from isolated plugins are reported but not directly executable
        # in the main process — they go through the worker RPC
        return []

    def get_hooks(self):
        return []

    def get_tools(self) -> list[dict]:
        result = self._worker.call("get_tools")
        if result.success and isinstance(result.data, list):
            return result.data
        return []

    def get_tool_dispatch(self) -> dict[str, Any]:
        dispatch: dict[str, Any] = {}
        for definition in self.get_tools():
            function = definition.get("function", definition) if isinstance(definition, dict) else {}
            tool_name = str(function.get("name", "")) if isinstance(function, dict) else ""
            if not tool_name:
                continue

            def invoke(_tool_name: str = tool_name, **arguments: Any) -> str:
                result = self._worker.call(
                    "execute_tool",
                    tool_name=_tool_name,
                    arguments=arguments,
                )
                if not result.success:
                    raise RuntimeError(result.error or f"Plugin tool {_tool_name} failed")
                if isinstance(result.data, str):
                    return result.data
                return json.dumps(result.data, ensure_ascii=False)

            dispatch[tool_name] = invoke
        return dispatch

    def setup(self) -> bool:
        return True  # Setup happened in worker.start()

    def teardown(self):
        self._worker.stop()
