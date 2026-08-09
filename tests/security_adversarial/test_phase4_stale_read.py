import pytest
import threading
import time
from nexus.tools.tools_impl import tool_edit_file, tool_patch_file, tool_context

def test_stale_read_lost_update_edit(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("line1\nline2\nline3\n")
    
    # We simulate the TOCTOU gap in tool_edit_file or tool_patch_file.
    # Noryx reads the file, calculates the new content, then writes it.
    # If an external process modifies the file between read and write, 
    # Noryx overwrites the external modification because it writes the whole file back.
    
    # Let's test this by running a hook inside MutationController.write_file if we can,
    # or just simulating the steps. Since we can't easily hook the exact line in Python without mocking,
    # we can mock MutationController.write_file to sleep and modify the file externally.
    
    from nexus.mutation import MutationController
    original_write = MutationController.write_file
    
    def hooked_write(self, p, content, **kwargs):
        # External process modifies the file!
        with open(p, "a") as f:
            f.write("external_edit\n")
        # Now Noryx finishes its write
        return original_write(self, p, content, **kwargs)
        
    MutationController.write_file = hooked_write
        
    try:
        with tool_context(tmp_path):
            res = tool_edit_file(str(target), "line2", "line2_edited")
            assert "❌" in res
            assert "Stale read detected" in res
            
            final_content = target.read_text()
            assert "external_edit" in final_content, "Lost update! External modification was silently overwritten."
            assert "line2_edited" not in final_content
    finally:
        MutationController.write_file = original_write

def test_stale_read_lost_update_patch(tmp_path):
    target = tmp_path / "target.txt"
    target.write_text("line1\nline2\nline3\n")
    
    from nexus.mutation import MutationController
    original_write = MutationController.write_file
    
    def hooked_write(self, p, content, **kwargs):
        # External process modifies the file!
        with open(p, "a") as f:
            f.write("external_edit\n")
        return original_write(self, p, content, **kwargs)
        
    MutationController.write_file = hooked_write
        
    try:
        with tool_context(tmp_path):
            res = tool_patch_file(str(target), 2, 2, "line2_patched")
            assert "❌" in res
            assert "Stale read detected" in res
            
            final_content = target.read_text()
            assert "external_edit" in final_content, "Lost update in patch_file! External modification was silently overwritten."
            assert "line2_patched" not in final_content
    finally:
        MutationController.write_file = original_write
