import os
import psutil
import pytest
from unittest.mock import MagicMock

from nexus.agent.core import Agent
from nexus.planner import ExecutionPlan, PlanStep, IntentType, Difficulty, PlanType
from nexus.pipeline import ExecutionPipeline

def get_process_metrics():
    process = psutil.Process(os.getpid())
    rss_mb = process.memory_info().rss / (1024 * 1024)
    fd_count = process.num_fds()
    children = process.children(recursive=True)
    return rss_mb, fd_count, len(children)

def test_phase7_repeated_sessions_memory_leak(tmp_path):
    """Run 100 agent sessions and track memory and FD leaks."""
    os.environ["NORYX_HOME"] = str(tmp_path / "noryx_home")
    
    baseline_rss, baseline_fds, baseline_children = get_process_metrics()
    
    rss_history = []
    
    for i in range(250):
        agent = Agent(api_key="test", model_key="dummy", working_dir=str(tmp_path))
        agent.run_ledger.begin(f"test_request_{i}")
        
        pipeline = ExecutionPipeline(agent)
        plan = ExecutionPlan(
            id=f"test_{i}",
            goal="test",
            intent=IntentType.BUILD,
            difficulty=Difficulty.TRIVIAL,
            plan_type=PlanType.PLANNED,
            steps=[PlanStep(id=0, title="test", description="test")]
        )
        agent.planner.current_plan = plan
        
        # Mock provider
        agent.client = MagicMock()
        def mock_chat(*args, **kwargs):
            class MockChunk:
                content = "Done"
            def stream():
                yield MockChunk()
            return stream()
        agent.client.chat = mock_chat
        
        response, events = pipeline._run_hosted_execution(
            f"test_{i}",
            {"intent": MagicMock()},
            plan,
            interactive=False,
            emit_ui=False
        )
        
        if i % 25 == 0:
            rss, fds, children = get_process_metrics()
            rss_history.append((i, rss, fds, children))
            
    final_rss, final_fds, final_children = get_process_metrics()
    
    print(f"\nBaseline: {baseline_rss:.2f} MB, {baseline_fds} FDs")
    for i, rss, fds, children in rss_history:
        print(f"Iter {i}: {rss:.2f} MB, {fds} FDs")
    print(f"Final: {final_rss:.2f} MB, {final_fds} FDs")
    
    # Assertions
    # A tiny leak might happen due to caches, but if RSS grows > 50MB for 100 empty sessions, it's a real leak.
    assert final_rss - baseline_rss < 60, f"Memory leak detected: {final_rss - baseline_rss:.2f} MB"
    assert final_fds - baseline_fds < 10, f"FD leak detected: {final_fds - baseline_fds} FDs"
    assert final_children == 0, f"Zombie/orphan processes detected: {final_children}"
