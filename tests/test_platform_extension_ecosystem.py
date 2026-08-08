import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from nexus.platform.capabilities import EXTENSION_CAPABILITIES, FORBIDDEN_CAPABILITIES
from nexus.platform.compatibility import CompatibilityManager
from nexus.platform.lifecycle import ExtensionLifecycleManager
from nexus.platform.manifest import EXTENSION_TYPES, ExtensionManifest, ManifestValidationError
from nexus.platform.mcp_gateway import MCPGateway
from nexus.platform.mcp_permissions import MCPPermissionLayer
from nexus.platform.permissions import PermissionScope, PermissionStore
from nexus.platform.registry import PlatformExtensionRegistry
from nexus.platform.runtime import RuntimeContext, SecureExtensionRuntime
from nexus.platform.sdk import EXTENSION_TEMPLATES, ExtensionSDK
from nexus.platform.verification import PackageVerifier
from nexus.plugins.worker import PluginWorkerResult


def write_extension(
    base: Path,
    name: str = "sample_tool",
    *,
    version: str = "1.0.0",
    extension_type: str = "tool",
    capabilities: list[str] | None = None,
) -> Path:
    ext_dir = base / name
    ext_dir.mkdir(parents=True)
    entry_point = EXTENSION_TEMPLATES.get(extension_type, EXTENSION_TEMPLATES["tool"])[
        "entry_point"
    ]
    manifest = {
        "name": name,
        "version": version,
        "extension_type": extension_type,
        "api_version": "nexus.extensions.v1",
        "entry_point": entry_point,
        "capabilities": capabilities if capabilities is not None else ["pure"],
        "min_nexus_version": "3.0.0",
    }
    (ext_dir / "extension.json").write_text(json.dumps(manifest), encoding="utf-8")
    (ext_dir / entry_point).write_text(
        "from nexus.plugins.base import BasePlugin\n\n"
        "class SampleToolPlugin(BasePlugin):\n"
        f"    name = {name!r}\n"
        "    version = '1.0.0'\n"
        "    def get_tools(self):\n"
        "        return []\n",
        encoding="utf-8",
    )
    return ext_dir


@pytest.mark.parametrize("extension_type", sorted(EXTENSION_TYPES))
def test_sdk_creates_valid_manifest_for_every_extension_type(extension_type):
    manifest = ExtensionSDK.create_manifest(f"{extension_type}_ext", extension_type)
    parsed = ExtensionManifest.from_dict(manifest)
    assert parsed.extension_type == extension_type
    assert parsed.api_version == "nexus.extensions.v1"


@pytest.mark.parametrize("capability", sorted(EXTENSION_CAPABILITIES))
def test_manifest_accepts_every_known_capability(capability):
    manifest = {
        "name": "capability_ext",
        "version": "1.0.0",
        "extension_type": "tool",
        "capabilities": [capability],
    }
    parsed = ExtensionManifest.from_dict(manifest)
    assert capability in parsed.capabilities


@pytest.mark.parametrize("capability", sorted(FORBIDDEN_CAPABILITIES))
def test_manifest_rejects_forbidden_capabilities(capability):
    manifest = {
        "name": "forbidden_ext",
        "version": "1.0.0",
        "extension_type": "tool",
        "capabilities": [capability],
    }
    with pytest.raises(ManifestValidationError):
        ExtensionManifest.from_dict(manifest)


@pytest.mark.parametrize(
    "field,value",
    [
        ("required_tools", ["run_process"]),
        ("required_paths", ["src"]),
        ("required_env", ["NEXUS_TOKEN"]),
        ("dependencies", ["other_extension"]),
        ("permissions", ["fs_read:src"]),
    ],
)
def test_manifest_accepts_string_list_fields(field, value):
    manifest = {
        "name": "list_ext",
        "version": "1.0.0",
        "extension_type": "tool",
        field: value,
    }
    assert ExtensionManifest.from_dict(manifest).to_dict()[field] == value


@pytest.mark.parametrize(
    "field,value",
    [
        ("name", "BadName"),
        ("version", "1"),
        ("extension_type", "kernel_patch"),
        ("api_version", "nexus.extensions.v0"),
        ("entry_point", "../escape.py"),
        ("required_paths", ["../outside"]),
    ],
)
def test_manifest_rejects_invalid_security_boundaries(field, value):
    manifest = {"name": "secure_ext", "version": "1.0.0", "extension_type": "tool"}
    manifest[field] = value
    with pytest.raises(ManifestValidationError):
        ExtensionManifest.from_dict(manifest)


@pytest.mark.parametrize("scope", list(PermissionScope))
def test_permission_store_persists_grants_by_scope(tmp_path, scope):
    store = PermissionStore(tmp_path)
    store.grant("sample_tool", "pure", scope, repository=str(tmp_path))

    reloaded = PermissionStore(tmp_path)
    grants = reloaded.list_grants("sample_tool")

    assert len(grants) == 1
    assert grants[0].scope == scope


@pytest.mark.parametrize(
    "scope,expected_valid_after_check",
    [
        (PermissionScope.ONCE, True),
        (PermissionScope.RUN, True),
        (PermissionScope.REPOSITORY, True),
        (PermissionScope.GLOBAL, True),
    ],
)
def test_permission_store_checks_every_scope(tmp_path, scope, expected_valid_after_check):
    store = PermissionStore(tmp_path)
    store.grant("sample_tool", "pure", scope, repository=str(tmp_path))

    grant = store.check("sample_tool", "pure", repository=str(tmp_path))

    assert (grant is not None) is expected_valid_after_check


@pytest.mark.parametrize("extension_type", ["tool", "provider", "mcp_server"])
def test_sdk_generated_templates_validate_for_core_extension_types(tmp_path, extension_type):
    generated = ExtensionSDK.generate_extension(tmp_path, f"{extension_type}_generated", extension_type)

    ok, messages = ExtensionSDK.validate_extension(generated)

    assert ok, messages


def test_once_permission_is_checkable_then_consumed(tmp_path):
    store = PermissionStore(tmp_path)
    store.grant("sample_tool", "tool_invoke", PermissionScope.ONCE)

    assert store.check("sample_tool", "tool_invoke") is not None
    assert store.consume_once("sample_tool", "tool_invoke") is True
    assert store.check("sample_tool", "tool_invoke") is None


def test_repository_permission_does_not_cross_repository_boundary(tmp_path):
    store = PermissionStore(tmp_path)
    store.grant(
        "sample_tool",
        "fs_read",
        PermissionScope.REPOSITORY,
        repository=str(tmp_path / "repo_a"),
    )

    assert store.check("sample_tool", "fs_read", repository=str(tmp_path / "repo_a"))
    assert store.check("sample_tool", "fs_read", repository=str(tmp_path / "repo_b")) is None


def test_permission_hash_binding_blocks_changed_package(tmp_path):
    store = PermissionStore(tmp_path)
    store.grant("sample_tool", "network", PermissionScope.GLOBAL, content_hash="abc")

    assert store.check("sample_tool", "network", content_hash="abc")
    assert store.check("sample_tool", "network", content_hash="def") is None


def test_package_verifier_rejects_missing_manifest(tmp_path):
    ext_dir = tmp_path / "broken"
    ext_dir.mkdir()

    result = PackageVerifier().verify_directory(ext_dir)

    assert result.valid is False
    assert "Missing extension.json or plugin.json manifest" in result.errors


def test_package_verifier_rejects_missing_entry_point(tmp_path):
    ext_dir = write_extension(tmp_path)
    (ext_dir / "tool.py").unlink()

    result = PackageVerifier().verify_directory(ext_dir)

    assert result.valid is False
    assert "Entry point not found: tool.py" in result.errors


def test_package_verifier_rejects_secret_files(tmp_path):
    ext_dir = write_extension(tmp_path)
    (ext_dir / ".env").write_text("TOKEN=secret", encoding="utf-8")

    result = PackageVerifier().verify_directory(ext_dir)

    assert result.valid is False
    assert any("Forbidden file pattern" in error for error in result.errors)


def test_lifecycle_install_enable_disable_remove_round_trip(tmp_path):
    source = write_extension(tmp_path / "src")
    registry = PlatformExtensionRegistry(
        working_dir=str(tmp_path),
        extensions_dir=tmp_path / "state" / "installed",
    )
    manager = ExtensionLifecycleManager(registry, working_dir=str(tmp_path))

    ok, message, record = manager.install(source, enable=False)
    assert ok, message
    assert record is not None
    assert registry.get("sample_tool") is not None

    ok, message = manager.enable("sample_tool")
    assert ok, message
    assert registry.get("sample_tool").enabled is True

    ok, message = manager.disable("sample_tool")
    assert ok, message
    assert registry.get("sample_tool").enabled is False

    ok, message = manager.remove("sample_tool")
    assert ok, message
    assert registry.get("sample_tool") is None


def test_lifecycle_update_blocks_silent_permission_escalation(tmp_path):
    original = write_extension(tmp_path / "original", capabilities=["pure"])
    updated = write_extension(tmp_path / "updated", capabilities=["pure", "network"])
    registry = PlatformExtensionRegistry(
        working_dir=str(tmp_path),
        extensions_dir=tmp_path / "state" / "installed",
    )
    manager = ExtensionLifecycleManager(registry, working_dir=str(tmp_path))

    ok, message, _ = manager.install(original)
    assert ok, message

    ok, message = manager.update("sample_tool", updated)
    assert ok is False
    assert "re-approval" in message


def test_registry_discovers_local_extensions_without_enabling(tmp_path):
    local = tmp_path / "nexus_extensions"
    write_extension(local)
    registry = PlatformExtensionRegistry(
        working_dir=str(tmp_path),
        extensions_dir=tmp_path / "state" / "installed",
    )

    records = registry.discover()

    assert len(records) == 1
    assert records[0].enabled is False
    assert records[0].source == "local"


def test_registry_persists_installed_record(tmp_path):
    source = write_extension(tmp_path / "src")
    extensions_dir = tmp_path / "state" / "installed"
    registry = PlatformExtensionRegistry(working_dir=str(tmp_path), extensions_dir=extensions_dir)
    manager = ExtensionLifecycleManager(registry, working_dir=str(tmp_path))
    ok, message, _ = manager.install(source, enable=True)
    assert ok, message

    reloaded = PlatformExtensionRegistry(working_dir=str(tmp_path), extensions_dir=extensions_dir)

    assert reloaded.get("sample_tool").enabled is True


def test_sdk_generate_validate_and_package_extension(tmp_path):
    generated = ExtensionSDK.generate_extension(tmp_path, "generated_tool", "tool")
    ok, messages = ExtensionSDK.validate_extension(generated)
    assert ok, messages

    ok, message = ExtensionSDK.package_extension(generated, tmp_path / "generated_tool.zip")
    assert ok, message
    archive_path = Path(message.removeprefix("Packaged to "))
    with zipfile.ZipFile(archive_path) as archive:
        assert "extension.json" in archive.namelist()


def test_compatibility_rejects_future_minimum_version():
    manifest = ExtensionManifest.from_dict({
        "name": "future_ext",
        "version": "1.0.0",
        "extension_type": "tool",
        "min_nexus_version": "999.0.0",
    })

    result = CompatibilityManager(nexus_version="3.2.1").check(manifest)

    assert result.compatible is False


def test_runtime_denies_unstarted_extension(tmp_path):
    runtime = SecureExtensionRuntime(PermissionStore(tmp_path), working_dir=str(tmp_path))

    result = runtime.call("missing", "get_tools", required_capability="pure")

    assert result.success is False
    assert "not running" in result.error


def test_runtime_denies_missing_permission_on_running_extension(tmp_path):
    runtime = SecureExtensionRuntime(PermissionStore(tmp_path), working_dir=str(tmp_path))
    runtime._workers["sample_tool"] = _FakeWorker()
    runtime._manifests["sample_tool"] = ExtensionManifest.from_dict({
        "name": "sample_tool",
        "version": "1.0.0",
        "extension_type": "tool",
        "capabilities": ["tool_invoke"],
    })

    result = runtime.call(
        "sample_tool",
        "execute_tool",
        required_capability="tool_invoke",
        context=RuntimeContext(extension_name="sample_tool", working_dir=str(tmp_path)),
    )

    assert result.success is False
    assert "Permission denied" in result.error


class _FakeWorker:
    def call(self, method, **kwargs):
        return PluginWorkerResult(True, data={"method": method, "kwargs": kwargs})


def test_mcp_permissions_require_explicit_grants(tmp_path):
    layer = MCPPermissionLayer(tmp_path)

    assert layer.check_tool_access("server", "tool") is False
    layer.approve_tool("server", "tool")
    assert layer.check_tool_access("server", "tool") is True


def test_mcp_gateway_add_enable_disable_remove_round_trip(tmp_path):
    gateway = MCPGateway(working_dir=str(tmp_path), state_dir=tmp_path / "mcp")

    gateway.add_server("local_server", [sys.executable, "-c", "print('ready')"])
    ok, message = gateway.enable_server("local_server")
    assert ok is False
    assert "requires permission" in message

    gateway.permissions.approve_server("local_server", all_tools=True)
    ok, message = gateway.enable_server("local_server")
    assert ok, message
    assert gateway.get_server("local_server").enabled is True

    ok, message = gateway.disable_server("local_server")
    assert ok, message
    assert gateway.get_server("local_server").enabled is False

    assert gateway.remove_server("local_server") is True


def run_cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    repo_root = str(Path(__file__).resolve().parents[1])
    env["PYTHONPATH"] = repo_root + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, "-m", "nexus", *args],
        cwd=cwd,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_extensions_cli_create_validate_install_list_inspect(tmp_path):
    result = run_cli(
        "extensions",
        "--working-dir",
        str(tmp_path),
        "create",
        "cli_tool",
        "--output",
        str(tmp_path),
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr

    ext_dir = tmp_path / "cli_tool"
    result = run_cli("extensions", "--working-dir", str(tmp_path), "validate", str(ext_dir), cwd=tmp_path)
    assert result.returncode == 0, result.stderr

    result = run_cli(
        "extensions",
        "--working-dir",
        str(tmp_path),
        "install",
        str(ext_dir),
        "--enable",
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr

    result = run_cli("extensions", "--working-dir", str(tmp_path), "list", cwd=tmp_path)
    assert "cli_tool" in result.stdout

    result = run_cli("extensions", "--working-dir", str(tmp_path), "inspect", "cli_tool", cwd=tmp_path)
    assert "Extension: cli_tool" in result.stdout


def test_mcp_cli_add_list_enable_disable_remove(tmp_path):
    result = run_cli(
        "mcp",
        "--working-dir",
        str(tmp_path),
        "add",
        "--approve",
        "--enable",
        "cli_mcp",
        "--",
        sys.executable,
        "-c",
        "print('ready')",
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr

    result = run_cli("mcp", "--working-dir", str(tmp_path), "list", cwd=tmp_path)
    assert "cli_mcp" in result.stdout

    result = run_cli("mcp", "--working-dir", str(tmp_path), "disable", "cli_mcp", cwd=tmp_path)
    assert result.returncode == 0, result.stderr

    result = run_cli("mcp", "--working-dir", str(tmp_path), "remove", "cli_mcp", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
