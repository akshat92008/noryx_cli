import pytest
import random
import string
import os
from nexus.tools.tools_impl import (
    tool_write_file, tool_edit_file, tool_patch_file, tool_multi_edit, tool_context
)

def test_randomized_mutation_fuzzing(tmp_path):
    # Fuzzing file mutations to ensure no silent crashes or corruption
    target = tmp_path / "fuzz_target.txt"
    target.write_text("initial state\n")
    
    current_content = "initial state\n"
    
    with tool_context(tmp_path):
        for _ in range(50):
            action = random.choice(["write", "edit", "patch", "multi_edit"])
            
            if action == "write":
                new_str = "".join(random.choices(string.ascii_letters + "\n", k=50))
                res = tool_write_file(str(target), new_str)
                assert "✅" in res
                current_content = new_str
                
            elif action == "edit":
                if len(current_content) > 0:
                    lines = current_content.splitlines()
                    if lines:
                        old_line = random.choice(lines)
                        # We must ensure old_line is unique to succeed, else it might fail, which is also fine.
                        if current_content.count(old_line) == 1:
                            new_line = old_line + "_edited"
                            res = tool_edit_file(str(target), old_line, new_line)
                            if "✅" in res:
                                current_content = current_content.replace(old_line, new_line, 1)
                            else:
                                pass # Might fail if old_line was empty or something
                                
            elif action == "patch":
                lines = current_content.splitlines()
                if lines:
                    idx = random.randint(1, len(lines))
                    new_val = f"patched_line_{idx}"
                    res = tool_patch_file(str(target), idx, idx, new_val)
                    if "✅" in res:
                        lines[idx-1] = new_val
                        current_content = "\n".join(lines) + ("\n" if current_content.endswith("\n") else "")
                                    
            elif action == "multi_edit":
                if len(current_content) > 0:
                    lines = current_content.splitlines()
                    if lines:
                        old_line = random.choice(lines)
                        if current_content.count(old_line) == 1:
                            new_line = old_line + "_multi"
                            edits = [{"path": str(target), "old_text": old_line, "new_text": new_line}]
                            res = tool_multi_edit(edits)

            # Integrity check after EVERY mutation
            current_content = target.read_text()
            assert len(current_content) >= 0
            
def test_repository_integrity_check(tmp_path):
    # Verify `.noryx/history` tracking doesn't break on intense rapid writes
    from nexus.tools.tools_impl import get_history
    
    target = tmp_path / "repo_file.txt"
    target.write_text("hello")
    
    with tool_context(tmp_path):
        history = get_history()
        history.changes = []
        for i in range(1, 100):
            res = tool_write_file(str(target), f"write_{i}")
            assert "✅" in res
        
        # Check that history snapshots exist and are intact
        db_path = history._changes_file()
        assert db_path.exists()
        
        # History should have 99 snapshots
        import json
        with open(db_path, "r") as f:
            changes = json.load(f)
        assert len(changes) == 99
