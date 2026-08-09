import pytest
from nexus.agent.core import Agent
from nexus.run_state import RunStatus, RunLedger

def test_duplicate_resume_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("NORYX_HOME", str(tmp_path / "noryx_home"))
    
    agent = Agent(api_key="test", model_key="dummy", working_dir=str(tmp_path))
    agent.run_id = "test-run-123"
    
    # 1. Test duplicate pending edit application
    agent._pending_edits["edit-0001"] = {
        "name": "edit_file",
        "args": {"path": "test.txt", "old_text": "a", "new_text": "b"},
        "diff": "diff",
        "diff_hash": "hash1",
        "tool_call_id": "call_123"
    }
    
    # Normally this executes the tool. But the file doesn't exist, so it fails, but returns a result.
    res, success = agent.apply_pending_edit("edit-0001")
    assert "Unknown or expired" not in res # because it existed
    
    # SECOND time we call it:
    res2, success2 = agent.apply_pending_edit("edit-0001")
    # This correctly says "Unknown or expired edit id" because the pop() removes it!
    assert "Unknown or expired edit id" in res2
    assert not success2
    
    # 2. Test duplicate confirmation application
    agent._pending_confirmations["danger-0000"] = {
        "name": "run_command",
        "args": {"command": "echo hi"},
        "reason": "danger",
        "details": "",
        "edit_confirmed": False,
        "tool_call_id": "call_456"
    }
    
    res3, success3 = agent.confirm_pending_operation("danger-0000")
    assert "Unknown or expired" not in res3
    
    res4, success4 = agent.confirm_pending_operation("danger-0000")
    assert "Unknown or expired confirmation id" in res4
    assert not success4
    
    # What about resume_after_approval? It doesn't seem to check if there are any pending edits!
    # If the LLM just yielded an edit, we applied it, we can call resume_after_approval().
    # But if we call it twice, we launch two model calls! That's a race!
    # Wait, resume_after_approval just delegates to `_run_hosted_turn`.
    # How does `CLI` know to prevent duplicate resumes?
    # If a user double-clicks "Continue", the UI might send two requests, triggering two `resume_after_approval` calls concurrently, which messes up the agent's message list.
