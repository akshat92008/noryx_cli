from unittest.mock import MagicMock

from nexus.mcp.registry import MCPToolRegistry


def test_mcp_tool_registry():
    client = MagicMock()
    client.get_all_tool_definitions.return_value = [{"name": "foo"}]
    client.call_tool.return_value = "bar"
    client.is_mcp_tool.return_value = True

    registry = MCPToolRegistry(client)
    
    assert registry.get_tool_definitions() == [{"name": "foo"}]
    assert registry.handle_tool_call("foo", {"arg": 1}) == "bar"
    assert registry.is_mcp_tool("foo") is True
