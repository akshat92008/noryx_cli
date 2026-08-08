import sys

from nexus.mcp.client import MCPConnection, MCPServerConfig


def test_mcp_actual_subprocess(tmp_path):
    # Create a real python script that implements a minimal MCP server
    server_script = tmp_path / "mcp_server.py"
    server_script.write_text("""
import sys
import json

def log(msg):
    pass

for line in sys.stdin:
    if not line.strip():
        continue
    try:
        req = json.loads(line)
        log(f"Received: {req}")
        if req.get("method") == "initialize":
            resp = {"jsonrpc": "2.0", "id": req.get("id"), "result": {"capabilities": {}}}
            sys.stdout.write(json.dumps(resp) + "\\n")
            sys.stdout.flush()
        elif req.get("method") == "tools/list":
            resp = {
                "jsonrpc": "2.0", 
                "id": req.get("id"), 
                "result": {
                    "tools": [
                        {
                            "name": "actual_tool",
                            "description": "An actual tool",
                            "inputSchema": {"type": "object", "properties": {}}
                        }
                    ]
                }
            }
            sys.stdout.write(json.dumps(resp) + "\\n")
            sys.stdout.flush()
        elif req.get("method") == "tools/call":
            resp = {
                "jsonrpc": "2.0",
                "id": req.get("id"),
                "result": {
                    "content": [{"type": "text", "text": "Actual tool execution result!"}]
                }
            }
            sys.stdout.write(json.dumps(resp) + "\\n")
            sys.stdout.flush()
        elif req.get("method") == "notifications/initialized":
            pass # no response needed
    except Exception as e:
        log(f"Error: {e}")
""")

    config = MCPServerConfig(
        name="actual_test_server",
        command=[sys.executable, str(server_script)],
        workspace=str(tmp_path),
        require_os_isolation=False,
        allow_unisolated_host_process=True,
    )
    conn = MCPConnection(config)

    assert conn.connect() is True
    assert conn.connected is True

    # Check that it actually discovered the tool via the subprocess stdout
    assert len(conn.tools) == 1
    assert conn.tools[0].name == "actual_tool"

    # Actually call the tool and get the result
    res = conn.call_tool("actual_tool", {})
    assert "Actual tool execution result!" in res.get(
        "text", ""
    ) or "Actual tool execution result!" in str(res)

    # Cleanup
    if conn._process:
        conn._process.terminate()
        conn._process.wait()
