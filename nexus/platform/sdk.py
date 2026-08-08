"""Developer SDK for extension authors."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

from nexus.extensions import EXTENSION_API_VERSION
from nexus.platform.manifest import EXTENSION_TYPES, validate_manifest

EXTENSION_TEMPLATES = {
    "tool": {
        "extension_type": "tool",
        "capabilities": ["pure"],
        "entry_point": "tool.py",
    },
    "provider": {
        "extension_type": "provider",
        "capabilities": ["provider_call"],
        "entry_point": "provider.py",
    },
    "context_source": {
        "extension_type": "context_source",
        "capabilities": ["context_read"],
        "entry_point": "context.py",
    },
    "repository_analyzer": {
        "extension_type": "repository_analyzer",
        "capabilities": ["fs_read", "context_read"],
        "entry_point": "analyzer.py",
    },
    "verification_check": {
        "extension_type": "verification_check",
        "capabilities": ["verification_run", "shell"],
        "entry_point": "check.py",
    },
    "planning_extension": {
        "extension_type": "planning_extension",
        "capabilities": ["planning_read", "planning_write"],
        "entry_point": "planner.py",
    },
    "routing_policy": {
        "extension_type": "routing_policy",
        "capabilities": ["routing_read"],
        "entry_point": "policy.py",
    },
    "event_subscriber": {
        "extension_type": "event_subscriber",
        "capabilities": ["event_subscribe"],
        "entry_point": "subscriber.py",
    },
    "mcp_server": {
        "extension_type": "mcp_server",
        "capabilities": ["mcp_serve"],
        "entry_point": "server.py",
    },
    "mcp_client": {
        "extension_type": "mcp_client",
        "capabilities": ["mcp_connect", "network"],
        "entry_point": "client.py",
    },
    "plugin": {
        "extension_type": "plugin",
        "capabilities": ["pure"],
        "entry_point": "__init__.py",
    },
}


class ExtensionSDK:
    """Developer SDK for creating, validating, and testing extensions."""

    @staticmethod
    def create_manifest(
        name: str,
        extension_type: str = "tool",
        *,
        version: str = "1.0.0",
        description: str = "",
        author: str = "",
        capabilities: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a valid extension manifest."""
        template = EXTENSION_TEMPLATES.get(extension_type, EXTENSION_TEMPLATES["tool"])
        manifest = {
            "name": name,
            "version": version,
            "extension_type": extension_type,
            "api_version": EXTENSION_API_VERSION,
            "description": description or f"A Nexus {extension_type} extension",
            "author": author,
            "entry_point": template["entry_point"],
            "capabilities": capabilities or list(template["capabilities"]),
            "min_nexus_version": "3.0.0",
        }
        errors = validate_manifest(manifest)
        if errors:
            raise ValueError(f"Invalid manifest: {'; '.join(errors)}")
        return manifest

    @staticmethod
    def generate_extension(
        output_dir: Path,
        name: str,
        extension_type: str = "tool",
        *,
        description: str = "",
        author: str = "",
    ) -> Path:
        """Generate a new extension project from template."""
        output_dir = Path(output_dir) / name
        output_dir.mkdir(parents=True, exist_ok=True)

        manifest = ExtensionSDK.create_manifest(
            name, extension_type, description=description, author=author,
        )
        (output_dir / "extension.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8",
        )

        template = EXTENSION_TEMPLATES.get(extension_type, EXTENSION_TEMPLATES["tool"])
        entry_file = output_dir / template["entry_point"]
        entry_file.write_text(
            ExtensionSDK._generate_entry_point(name, extension_type),
            encoding="utf-8",
        )

        test_file = output_dir / "test_extension.py"
        test_file.write_text(
            ExtensionSDK._generate_test_file(name, extension_type),
            encoding="utf-8",
        )

        return output_dir

    @staticmethod
    def _generate_entry_point(name: str, extension_type: str) -> str:
        if extension_type == "tool":
            return textwrap.dedent(f'''\
                """{name} — Nexus tool extension."""

                from nexus.plugins.base import BasePlugin
                from nexus.sdk.tools import FunctionTool


                class {name.title().replace("-", "").replace("_", "")}Plugin(BasePlugin):
                    name = "{name}"
                    description = "A Nexus tool extension"
                    version = "1.0.0"

                    def get_tools(self):
                        return [FunctionTool(
                            {{
                                "type": "function",
                                "function": {{
                                    "name": "{name}_echo",
                                    "description": "Echo input text",
                                    "parameters": {{
                                        "type": "object",
                                        "properties": {{
                                            "text": {{"type": "string", "description": "Text to echo"}},
                                        }},
                                        "required": ["text"],
                                    }},
                                }},
                            }},
                            lambda text: f"Echo: {{text}}",
                            capabilities=("pure",),
                        ).to_schema()]

                    def get_tool_dispatch(self):
                        tool = FunctionTool(
                            {{
                                "type": "function",
                                "function": {{
                                    "name": "{name}_echo",
                                    "description": "Echo input text",
                                    "parameters": {{
                                        "type": "object",
                                        "properties": {{
                                            "text": {{"type": "string"}},
                                        }},
                                        "required": ["text"],
                                    }},
                                }},
                            }},
                            lambda text: f"Echo: {{text}}",
                            capabilities=("pure",),
                        )
                        return {{"{name}_echo": tool.execute}}
            ''')
        return textwrap.dedent(f'''\
            """{name} — Nexus {extension_type} extension."""

            from nexus.plugins.base import BasePlugin


            class {name.title().replace("-", "").replace("_", "")}Plugin(BasePlugin):
                name = "{name}"
                description = "A Nexus {extension_type} extension"
                version = "1.0.0"

                def setup(self) -> bool:
                    return True

                def teardown(self):
                    pass
        ''')

    @staticmethod
    def _generate_test_file(name: str, extension_type: str) -> str:
        return textwrap.dedent(f'''\
            """Tests for {name} extension."""

            import json
            from pathlib import Path


            def test_manifest_valid():
                manifest_path = Path(__file__).parent / "extension.json"
                data = json.loads(manifest_path.read_text())
                from nexus.platform.manifest import validate_manifest
                errors = validate_manifest(data)
                assert not errors, f"Manifest errors: {{errors}}"


            def test_extension_loads():
                from nexus.platform.verification import PackageVerifier
                verifier = PackageVerifier()
                result = verifier.verify_directory(Path(__file__).parent)
                assert result.valid, f"Verification errors: {{result.errors}}"
        ''')

    @staticmethod
    def validate_extension(ext_dir: Path) -> tuple[bool, list[str]]:
        """Validate an extension directory."""
        from nexus.platform.verification import PackageVerifier
        verifier = PackageVerifier()
        result = verifier.verify_directory(ext_dir)
        return result.valid, result.errors + result.warnings

    @staticmethod
    def package_extension(ext_dir: Path, output_path: Path) -> tuple[bool, str]:
        """Package an extension into a distributable archive."""
        import shutil

        valid, errors = ExtensionSDK.validate_extension(ext_dir)
        if not valid:
            return False, f"Validation failed: {'; '.join(errors)}"

        ext_dir = Path(ext_dir).resolve()
        output_path = Path(output_path).expanduser().resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        archive_path = Path(shutil.make_archive(str(output_path.with_suffix("")), "zip", ext_dir))
        return True, f"Packaged to {archive_path}"

    @staticmethod
    def supported_types() -> list[str]:
        return sorted(EXTENSION_TYPES)
