from nexus.extensions import ExtensionRegistry, NexusTool, ToolContext
from nexus.sdk import FunctionTool, ToolRegistry


def test_legacy_function_tool_implements_active_extension_protocol():
    tool = FunctionTool(
        {
            "type": "function",
            "function": {
                "name": "sum_values",
                "description": "sum two values",
                "parameters": {
                    "type": "object",
                    "properties": {"a": {"type": "integer"}, "b": {"type": "integer"}},
                    "required": ["a", "b"],
                },
            },
        },
        lambda a, b: {"total": a + b},
    )

    assert isinstance(tool, NexusTool)
    ExtensionRegistry._validate("tools", tool)
    assert tool.invoke({"a": 2, "b": 3}, ToolContext("/tmp", "session")) == {"total": 5}


def test_sdk_registry_rejects_duplicate_names_and_invalid_json():
    tool = FunctionTool(
        {
            "type": "function",
            "function": {"name": "echo", "description": "", "parameters": {}},
        },
        lambda value="": value,
    )
    registry = ToolRegistry()
    registry.register(tool)
    ok, result = registry.execute("echo", "not-json")

    assert ok is False
    assert "not valid JSON" in result
