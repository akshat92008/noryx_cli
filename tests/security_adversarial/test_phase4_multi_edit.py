import pytest
from nexus.tools.tools_impl import tool_multi_edit, tool_context

def test_stale_read_lost_update_multi_edit(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("line1\nline2\nline3\n")
    
    import pathlib
    original_read_bytes = pathlib.Path.read_bytes
    
    call_count = [0]
    def hooked_read_bytes(self):
        if self.name == target.name:
            call_count[0] += 1
            if call_count[0] == 2:
                # Simulate external modification right before the final stale read check
                with open(self, "a") as f:
                    f.write("external_edit\n")
        return original_read_bytes(self)
        
    pathlib.Path.read_bytes = hooked_read_bytes
    
    try:
        with tool_context(tmp_path):
            edits = [
                {"path": str(target), "old_text": "line2", "new_text": "line2_edited"}
            ]
            res = tool_multi_edit(edits)
            assert "❌" in res
            assert "Stale read detected" in res
            
            # The external_edit should remain intact because the transaction aborted!
            final_content = target.read_text()
            assert "external_edit" in final_content, "Lost update! External modification was rolled back!"
            assert "line2_edited" not in final_content, "Atomic transaction failed, partial mutation remained!"
    finally:
        pathlib.Path.read_bytes = original_read_bytes

def test_multi_edit_atomicity_rollback(tmp_path):
    # Edit 1 succeeds, Edit 2 fails (e.g. stale read or permission error), verify rollback
    t1 = tmp_path / "t1.txt"
    t1.write_text("A\nB\n")
    t2 = tmp_path / "t2.txt"
    t2.write_text("C\nD\n")
    
    import nexus.tools.tools_impl as tools_impl
    original_replace = tools_impl.os.replace
    
    def hooked_replace(src, dst):
        if str(dst) == str(t2):
            # Trigger failure midway through transaction (after t1 is replaced)
            raise OSError("Injected disk failure")
        return original_replace(src, dst)
        
    tools_impl.os.replace = hooked_replace
    
    try:
        with tool_context(tmp_path):
            edits = [
                {"path": str(t1), "old_text": "A", "new_text": "A2"},
                {"path": str(t2), "old_text": "C", "new_text": "C2"}
            ]
            res = tool_multi_edit(edits)
            assert "❌" in res
            assert "Injected disk failure" in res
            
            # Verify rollback! t1 should be back to original
            assert t1.read_text() == "A\nB\n", "Multi-edit atomicity failed, partial edit remained!"
    finally:
        tools_impl.os.replace = original_replace
