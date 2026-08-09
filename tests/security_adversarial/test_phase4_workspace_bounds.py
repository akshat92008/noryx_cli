import pytest
from pathlib import Path
import os
from nexus.mutation import MutationController

def test_workspace_escape_parent(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    controller = MutationController(workspace=workspace)
    
    escape_path = "../escaped.txt"
    # Even if it's evaluated from CWD, we want to ensure it's blocked.
    # We can chdir into workspace to simulate normal execution.
    original_cwd = os.getcwd()
    os.chdir(workspace)
    try:
        result = controller.write_file(escape_path, "hacked")
        assert not result.success, "Silent workspace escape allowed!"
        assert "escapes workspace" in result.error
    finally:
        os.chdir(original_cwd)

def test_workspace_escape_absolute(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    controller = MutationController(workspace=workspace)
    
    # Try to write to tmp_path / outside.txt which is absolute and outside workspace
    outside_file = tmp_path / "outside.txt"
    result = controller.write_file(str(outside_file), "hacked")
    assert not result.success
    assert "escapes workspace" in result.error

def test_workspace_symlink_escape(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "target.txt").write_text("safe")
    
    # Create symlink in workspace pointing outside
    symlink_path = workspace / "link_to_outside"
    os.symlink(str(outside_dir), str(symlink_path))
    
    controller = MutationController(workspace=workspace)
    result = controller.write_file("link_to_outside/target.txt", "hacked")
    
    assert not result.success
    assert "escapes workspace" in result.error
    assert (outside_dir / "target.txt").read_text() == "safe"
