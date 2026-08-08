"""Plugin Worker — isolated subprocess execution for untrusted plugin code.

Plugins MUST NOT run in the primary Nexus process.  This module provides
a subprocess-based RPC worker with a narrow protocol:

1. Parent sends ``{"action": "setup"}`` → worker loads and initialises the plugin.
2. Parent sends ``{"action": "call", "method": ..., "args": ...}``
   → worker calls the method and returns the result.
3. Parent sends ``{"action": "teardown"}`` → worker shuts down.

The worker receives only explicitly granted paths, tools, env vars, and
network access.
"""

from __future__ import annotations

import hashlib
import json
import logging
import subprocess
import sys
import textwrap
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nexus.process_io import filtered_subprocess_env, readline_with_timeout
from nexus.runtime.process_state import ProcessStateRegistry
from nexus.sandbox import CommandSpec, SandboxRunner

logger = logging.getLogger(__name__)

_WORKER_TIMEOUT = 30  # seconds per RPC call


@dataclass(frozen=True)
class PluginManifest:
    """Parsed and validated plugin manifest (plugin.json)."""

    name: str
    version: str
    description: str = ""
    author: str = ""
    entry_point: str = "__init__.py"
    capabilities: frozenset[str] = frozenset()
    required_tools: list[str] = field(default_factory=list)
    required_paths: list[str] = field(default_factory=list)
    required_env: list[str] = field(default_factory=list)
    network_access: bool = False
    content_hash: str = ""  # SHA-256 of manifest + all referenced files

    @classmethod
    def from_file(cls, manifest_path: Path) -> "PluginManifest":
        """Parse a plugin.json manifest."""
        if not manifest_path.is_file():
            raise PluginLoadError(f"Manifest not found: {manifest_path}")

        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            raise PluginLoadError(f"Invalid manifest: {manifest_path}: {exc}") from exc

        name = data.get("name", "")
        if not name or not isinstance(name, str):
            raise PluginLoadError(f"Manifest missing required 'name' field: {manifest_path}")

        version = data.get("version", "0.0.0")
        caps = frozenset(data.get("capabilities", []))

        return cls(
            name=name,
            version=str(version),
            description=data.get("description", ""),
            author=data.get("author", ""),
            entry_point=data.get("entry_point", "__init__.py"),
            capabilities=caps,
            required_tools=list(data.get("required_tools", [])),
            required_paths=list(data.get("required_paths", [])),
            required_env=list(data.get("required_env", [])),
            network_access=bool(data.get("network_access", False)),
        )

    def display_capabilities(self) -> str:
        """Human-readable capability summary for approval prompt."""
        lines = [f"Plugin: {self.name} v{self.version}"]
        if self.description:
            lines.append(f"  Description: {self.description}")
        if self.capabilities:
            lines.append(f"  Capabilities: {', '.join(sorted(self.capabilities))}")
        if self.required_tools:
            lines.append(f"  Required tools: {', '.join(self.required_tools)}")
        if self.required_paths:
            lines.append(f"  Required paths: {', '.join(self.required_paths)}")
        if self.required_env:
            lines.append(f"  Required env: {', '.join(self.required_env)}")
        if self.network_access:
            lines.append("  Network access: YES")
        return "\n".join(lines)


class PluginLoadError(Exception):
    """Raised when a plugin cannot be loaded safely."""


def compute_plugin_hash(plugin_dir: Path, manifest: PluginManifest) -> str:
    """Compute a content-addressed hash of the manifest and all referenced files.

    Any byte change in any file invalidates the previous approval.
    """
    hasher = hashlib.sha256()

    # Hash the manifest itself
    manifest_path = plugin_dir / "plugin.json"
    if manifest_path.is_file():
        hasher.update(manifest_path.read_bytes())

    # Hash the entry point
    entry_path = plugin_dir / manifest.entry_point
    if entry_path.is_file():
        hasher.update(entry_path.read_bytes())

    # Hash all .py files in the plugin directory (sorted for determinism)
    for py_file in sorted(plugin_dir.rglob("*.py")):
        try:
            hasher.update(py_file.read_bytes())
        except OSError:
            continue

    return hasher.hexdigest()


@dataclass
class PluginWorkerResult:
    """Result from a plugin worker RPC call."""

    success: bool
    data: Any = None
    error: str = ""


class PluginWorker:
    """Manages an isolated subprocess for a single plugin.

    The worker runs in a subprocess with a filtered environment and
    communicates via stdin/stdout JSON lines.
    """

    def __init__(
        self,
        plugin_dir: Path,
        manifest: PluginManifest,
        *,
        workspace_root: Path | None = None,
        allowed_env: dict[str, str] | None = None,
        timeout: float = _WORKER_TIMEOUT,
    ):
        self.plugin_dir = plugin_dir
        self.manifest = manifest
        self.workspace_root = workspace_root
        self.allowed_env = allowed_env or {}
        self.timeout = timeout
        self._process: subprocess.Popen | None = None
        self._cleanup_path: Path | None = None

    def start(self) -> PluginWorkerResult:
        """Start the worker subprocess and initialise the plugin."""
        worker_script = textwrap.dedent("""\
            import json
            import sys
            import importlib.util

            def main():
                plugin_dir = sys.argv[1]
                entry_point = sys.argv[2]

                # Load the plugin module
                spec = importlib.util.spec_from_file_location(
                    "nexus_plugin", f"{plugin_dir}/{entry_point}"
                )
                if not spec or not spec.loader:
                    print(json.dumps({"error": "Could not load plugin module"}))
                    sys.exit(1)

                module = importlib.util.module_from_spec(spec)
                try:
                    spec.loader.exec_module(module)
                except (OSError, ValueError) as e:
                    print(json.dumps({"error": f"Plugin load error: {e}"}))
                    sys.exit(1)

                # Find plugin class
                plugin_cls = None
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, type) and hasattr(attr, 'name') and attr_name != 'BasePlugin':
                        if hasattr(attr, 'setup'):
                            plugin_cls = attr
                            break

                if not plugin_cls:
                    print(json.dumps({"error": "No plugin class found"}))
                    sys.exit(1)

                try:
                    instance = plugin_cls()
                    if not instance.setup():
                        print(json.dumps({"error": "Plugin setup returned False"}))
                        sys.exit(1)
                except (TypeError, ValueError) as e:
                    print(json.dumps({"error": f"Plugin setup error: {e}"}))
                    sys.exit(1)

                # Signal ready
                print(json.dumps({"ready": True, "name": getattr(instance, 'name', '')}))
                sys.stdout.flush()

                def _json_safe(value):
                    try:
                        json.dumps(value)
                        return value
                    except (TypeError, ValueError):
                        return str(value)

                # RPC loop
                for line in sys.stdin:
                    try:
                        request = json.loads(line.strip())
                        action = request.get("action", "")
                        if action == "teardown":
                            try:
                                instance.teardown()
                            except (OSError, ValueError):
                                pass
                            print(json.dumps({"done": True}))
                            sys.stdout.flush()
                            break
                        elif action == "call":
                            method = request.get("method", "")
                            args = request.get("args", {})
                            if hasattr(instance, method) and callable(getattr(instance, method)):
                                result = getattr(instance, method)(**args)
                                print(json.dumps({"result": _json_safe(result)}))
                            else:
                                print(json.dumps({"error": f"Unknown method: {method}"}))
                        elif action == "execute_tool":
                            tool_name = request.get("tool_name", "")
                            args = request.get("arguments", {})
                            dispatch = (
                                instance.get_tool_dispatch()
                                if hasattr(instance, "get_tool_dispatch")
                                else {}
                            )
                            implementation = dispatch.get(tool_name) if isinstance(dispatch, dict) else None
                            if not callable(implementation):
                                print(json.dumps({"error": f"Unknown plugin tool: {tool_name}"}))
                            elif not isinstance(args, dict):
                                print(json.dumps({"error": "Plugin tool arguments must be an object"}))
                            else:
                                result = implementation(**args)
                                print(json.dumps({"result": _json_safe(result)}))
                        elif action == "get_skills":
                            skills = instance.get_skills() if hasattr(instance, 'get_skills') else []
                            print(json.dumps({"result": [{"name": getattr(s, 'name', '')} for s in skills]}))
                        elif action == "get_hooks":
                            hooks = instance.get_hooks() if hasattr(instance, 'get_hooks') else []
                            print(json.dumps({"result": [{"name": getattr(h, 'name', '')} for h in hooks]}))
                        elif action == "get_tools":
                            tools = instance.get_tools() if hasattr(instance, 'get_tools') else []
                            print(json.dumps({"result": tools}))
                        else:
                            print(json.dumps({"error": f"Unknown action: {action}"}))
                        sys.stdout.flush()
                    except json.JSONDecodeError:
                        print(json.dumps({"error": "Invalid JSON"}))
                        sys.stdout.flush()
                    except (LookupError, OSError, RuntimeError, TypeError, ValueError) as e:
                        print(json.dumps({"error": str(e)}))
                        sys.stdout.flush()

            main()
        """)

        # Build a filtered environment while retaining variables required to
        # start child processes on Windows (notably SystemRoot).
        env = filtered_subprocess_env()
        env.setdefault("LANG", "en_US.UTF-8")
        for key in self.manifest.required_env:
            if key in self.allowed_env:
                env[key] = self.allowed_env[key]

        argv = [
            sys.executable,
            "-c",
            worker_script,
            str(self.plugin_dir),
            self.manifest.entry_point,
        ]
        
        cwd_path = Path(self.workspace_root or self.plugin_dir).resolve()
        spec = CommandSpec(
            argv=argv,
            cwd=str(cwd_path),
            network=self.manifest.network_access,
            env=env,
            require_os_isolation=True,
        )
        
        runner = SandboxRunner(cwd_path)
        command, self._cleanup_path = runner.build_command(spec, cwd_path)

        try:
            self._process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=env,
                cwd=str(cwd_path),
            )
            ProcessStateRegistry.register_process(self._process)

            line = readline_with_timeout(self._process.stdout, self.timeout)
            if line is None:
                self.stop()
                return PluginWorkerResult(False, error="Plugin worker timed out during startup")
            if not line:
                self.stop()
                return PluginWorkerResult(False, error="Plugin worker produced no output")

            response = json.loads(line)
            if response.get("error"):
                self.stop()
                return PluginWorkerResult(False, error=response["error"])
            if response.get("ready"):
                return PluginWorkerResult(True, data=response)

            self.stop()
            return PluginWorkerResult(False, error="Unexpected worker response")

        except (OSError, json.JSONDecodeError) as exc:
            self.stop()
            return PluginWorkerResult(False, error=f"Worker start failed: {exc}")

    def call(self, action: str, **kwargs) -> PluginWorkerResult:
        """Send an RPC call to the worker."""
        if not self._process or self._process.poll() is not None:
            return PluginWorkerResult(False, error="Worker not running")

        request = {"action": action, **kwargs}
        try:
            self._process.stdin.write(json.dumps(request) + "\n")
            self._process.stdin.flush()

            line = readline_with_timeout(self._process.stdout, self.timeout)
            if line is None:
                self.stop()
                return PluginWorkerResult(False, error="Worker call timed out")
            if not line:
                return PluginWorkerResult(False, error="Worker produced no output")

            response = json.loads(line)
            if response.get("error"):
                return PluginWorkerResult(False, error=response["error"])
            return PluginWorkerResult(True, data=response.get("result"))

        except (BrokenPipeError, OSError, json.JSONDecodeError) as exc:
            return PluginWorkerResult(False, error=f"Worker call failed: {exc}")

    def stop(self):
        """Stop the worker subprocess."""
        if self._process:
            try:
                if self._process.poll() is None:
                    self._process.stdin.write(json.dumps({"action": "teardown"}) + "\n")
                    self._process.stdin.flush()
                    self._process.wait(timeout=2)
            except (BrokenPipeError, OSError, subprocess.TimeoutExpired):
                pass
            try:
                if self._process.poll() is None:
                    self._process.terminate()
                    self._process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
            try:
                if self._process.poll() is None:
                    self._process.kill()
                    self._process.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
            ProcessStateRegistry.unregister_process(self._process)
            self._process = None

        if self._cleanup_path:
            self._cleanup_path.unlink(missing_ok=True)
            self._cleanup_path = None

    def __del__(self):
        self.stop()
