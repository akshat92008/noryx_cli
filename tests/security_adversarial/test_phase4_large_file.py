import pytest
from nexus.tools.tools_impl import tool_edit_file, tool_patch_file, tool_multi_edit, tool_context

def test_large_file_stress(tmp_path):
    large_file = tmp_path / "large.txt"
    # Create a 3MB file
    large_file.write_bytes(b"A" * (3 * 1024 * 1024))
    
    with tool_context(tmp_path):
        res = tool_edit_file(str(large_file), "A", "B")
        assert "❌" in res
        assert "too large" in res
        
        res = tool_patch_file(str(large_file), 1, 1, "B")
        assert "❌" in res
        assert "too large" in res
        
        res = tool_multi_edit([{"path": str(large_file), "old_text": "A", "new_text": "B"}])
        assert "❌" in res
        assert "too large" in res
