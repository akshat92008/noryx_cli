import os
import pytest
from unittest.mock import MagicMock, patch

from nexus.agent.core import Agent
from nexus.planner import ExecutionPlan, PlanStep, IntentType, Difficulty, PlanType
from nexus.pipeline import ExecutionPipeline

def get_process_metrics():
    import psutil
    process = psutil.Process(os.getpid())
    rss_mb = process.memory_info().rss / (1024 * 1024)
    fd_count = process.num_fds()
    return rss_mb, fd_count

def test_long_run_tool_endurance(tmp_path):
    """Test 500 lightweight tool operations in a single session."""
    os.environ["NORYX_HOME"] = str(tmp_path / "noryx_home")
    
    agent = Agent(api_key="test", model_key="dummy", working_dir=str(tmp_path))
    agent.run_ledger.begin("test_request")
    
    baseline_rss, baseline_fds = get_process_metrics()
    
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
    
    # Mock provider to execute a tool 500 times, then stop
    call_count = [0]
    
    class MockClient:
        def chat(self, *args, **kwargs):
            class MockDelta:
                pass
            class MockChoice:
                def __init__(self, delta):
                    self.delta = delta
            class MockChunk:
                def __init__(self, choices):
                    self.choices = choices
                    self.usage = None
                    
            def stream():
                delta = MockDelta()
                if call_count[0] < 500:
                    delta.tool_calls = [
                        MagicMock(
                            index=0,
                            id=f"call_{call_count[0]}",
                            function=MagicMock(name="search_repository", arguments='{"query": "test"}')
                        )
                    ]
                    delta.content = None
                    call_count[0] += 1
                else:
                    delta.tool_calls = None
                    delta.content = "I am finally done."
                yield MockChunk([MockChoice(delta)])
            return stream()  

    agent.client = MockClient()
    response, events = pipeline._run_hosted_execution(
        "test_request",
        {"intent": MagicMock()},
        plan,
        interactive=False,
        emit_ui=False
    )
    final_rss, final_fds = get_process_metrics()
    
    print(f"\nOperations: {call_count[0]}")
    print(f"Baseline RSS: {baseline_rss:.2f} MB, Final RSS: {final_rss:.2f} MB")
    print(f"Baseline FDs: {baseline_fds}, Final FDs: {final_fds}")
    
    # Verify we hit the 500 mark
    assert call_count[0] == 500
    
    # Verify memory hasn't exploded (allow up to 60MB growth for 500 calls due to cache)
    assert final_rss - baseline_rss < 60, f"Memory leak detected: {final_rss - baseline_rss:.2f} MB"
    assert final_fds - baseline_fds < 10, f"FD leak detected: {final_fds - baseline_fds}"
