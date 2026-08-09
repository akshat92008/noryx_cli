import pytest
import os
from unittest.mock import MagicMock

from nexus.agent.core import Agent
from nexus.planner import ExecutionPlan, PlanStep, IntentType, Difficulty, PlanType
from nexus.pipeline import ExecutionPipeline


def mock_os_replace(src, dst):
    raise OSError(28, "No space left on device")

def test_persistence_failure_injection(tmp_path, monkeypatch):
    monkeypatch.setenv("NORYX_HOME", str(tmp_path / "noryx_home"))
    
    agent = Agent(api_key="test", model_key="dummy", working_dir=str(tmp_path))
    
    import os
    monkeypatch.setattr(os, "replace", mock_os_replace)
    monkeypatch.setattr(os, "write", mock_os_replace)
    monkeypatch.setattr(os, "fsync", mock_os_replace)
    
    # Try starting a run ledger
    agent.run_ledger.begin("test_request")

    # Let's see what happens during the run loop if os.replace fails
    
    pipeline = ExecutionPipeline(agent)
    plan = ExecutionPlan(
        id="test",
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
            content = "Hello"
        def stream():
            yield MockChunk()
        return stream()
    agent.client.chat = mock_chat

    # Call _run_hosted_execution
    try:
        response, events = pipeline._run_hosted_execution(
            "test",
            {"intent": MagicMock()},
            plan,
            interactive=False,
            emit_ui=False
        )
    except OSError as e:
        pytest.fail(f"Pipeline crashed due to persistence failure: {e}")
    
    print(f"Pipeline survived. Response: {response}")
