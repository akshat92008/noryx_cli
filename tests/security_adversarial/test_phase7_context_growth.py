import os
import pytest
from unittest.mock import MagicMock

from nexus.agent.core import Agent
from nexus.planner import ExecutionPlan, PlanStep, IntentType, Difficulty, PlanType
from nexus.pipeline import ExecutionPipeline

def test_context_compaction_stress(tmp_path):
    """Stress test context growth and compaction limits."""
    os.environ["NORYX_HOME"] = str(tmp_path / "noryx_home")
    
    agent = Agent(api_key="test", model_key="dummy", working_dir=str(tmp_path))
    agent.run_ledger.begin("test_request")
    
    pipeline = ExecutionPipeline(agent)
    plan = ExecutionPlan(
        id="test_plan",
        goal="test",
        intent=IntentType.BUILD,
        difficulty=Difficulty.TRIVIAL,
        plan_type=PlanType.PLANNED,
        steps=[PlanStep(id=0, title="test", description="test")]
    )
    agent.planner.current_plan = plan
    
    # Let's generate a massive context in agent.messages
    for i in range(150):
        agent.messages.append({
            "role": "assistant",
            "content": "This is a very long response " * 50
        })
        agent.messages.append({
            "role": "user",
            "content": "A tool response " * 50
        })
        
    # Now simulate an unresolved tool call that MUST survive compaction
    agent.messages.append({
        "role": "assistant",
        "tool_calls": [{"id": "call_123", "type": "function", "function": {"name": "test", "arguments": "{}"}}]
    })
    
    # Push 15 messages after it so it falls OUT of the keep_recent=12 window
    for i in range(15):
        agent.messages.append({
            "role": "tool",
            "tool_call_id": f"call_other_{i}",
            "name": "other",
            "content": "done"
        })
    
    original_len = len(agent.messages)
    
    # Mock provider to just return a simple message
    agent.client = MagicMock()
    def mock_chat(*args, **kwargs):
        class MockChunk:
            content = "Done"
        def stream():
            yield MockChunk()
        return stream()
    agent.client.chat = mock_chat
    
    # Run a turn which should trigger context compaction because there are 300+ messages!
    # Or at least, the token counter will trigger context manager truncation.
    response, events = pipeline._run_hosted_execution(
        "test_request",
        {"intent": MagicMock()},
        plan,
        interactive=False,
        emit_ui=False
    )
    
    final_len = len(agent.messages)
    print(f"\nOriginal Messages: {original_len}, Final Messages: {final_len}")
    
    # Verify the unresolved tool call survived!
    tool_call_survived = False
    for msg in agent.messages:
        if "tool_calls" in msg and getattr(msg["tool_calls"], "__len__", lambda: 0)() > 0 or (isinstance(msg.get("tool_calls"), list) and len(msg["tool_calls"]) > 0):
            tool_call_survived = True
            
    assert tool_call_survived, "Unresolved tool call was incorrectly pruned during compaction!"
