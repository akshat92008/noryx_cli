from nexus.extensions import ExtensionRegistry


def test_extensions_actual_load():
    # Load actual entry points from the environment
    registry = ExtensionRegistry()
    registry.discover()

    # We might not have any installed, but it should not crash
    assert isinstance(registry.loaded("providers"), list)
    assert isinstance(registry.loaded("tools"), list)
    assert isinstance(registry.loaded("policies"), list)


class _FilesystemExtension:
    name = "custom_read"
    description = "Read a declared target"
    input_schema = {
        "type": "object",
        "properties": {"target": {"type": "string"}},
        "required": ["target"],
    }
    capabilities = ("fs_read",)
    filesystem = {"read_arguments": ["target"], "write_arguments": []}

    def invoke(self, arguments, context):
        from pathlib import Path

        return Path(arguments["target"]).read_text(encoding="utf-8")


class _UndeclaredFilesystemExtension(_FilesystemExtension):
    name = "unsafe_custom_read"
    filesystem = {}


def test_extension_custom_path_field_is_scoped_by_declared_contract(tmp_path):
    from nexus.agent import Agent

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("secret", encoding="utf-8")
    agent = Agent(api_key="test", working_dir=str(workspace))
    agent.extensions.loaded = lambda group: [_FilesystemExtension()] if group == "tools" else []
    agent._register_external_tool_capabilities()

    pending, success = agent._execute_tool_with_safety(
        "custom_read", {"target": str(outside)}
    )

    assert success is False
    assert "PENDING_CONFIRMATION" in pending
    assert "outside the current workspace" in pending


def test_filesystem_extension_without_argument_contract_is_hidden(tmp_path):
    from nexus.agent import Agent

    agent = Agent(api_key="test", working_dir=str(tmp_path))
    agent.extensions.loaded = (
        lambda group: [_UndeclaredFilesystemExtension()] if group == "tools" else []
    )
    agent._register_external_tool_capabilities()

    assert "unsafe_custom_read" not in agent._tool_capabilities
    names = {
        item["function"]["name"]
        for item in (agent._get_tools() or [])
        if "function" in item
    }
    assert "unsafe_custom_read" not in names
