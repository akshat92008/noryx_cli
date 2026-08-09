import pytest
from unittest.mock import MagicMock
from nexus.agent.core import Agent
from nexus.planner import ExecutionPlan, PlanStep
from nexus.providers.base import Provider

class MockFailingProvider(Provider):
    def __init__(self, failure_count=1):
        self.failure_count = failure_count
        self.attempts = 0

    @property
    def model_id(self) -> str:
        return "mock-model"

    @property
    def id(self) -> str:
        return "mock_failing"

    @property
    def name(self) -> str:
        return "Mock Failing"

    def count_tokens(self, text: str) -> int:
        return 0

    def chat_sync(self, *args, **kwargs):
        pass

    def chat(self, *args, **kwargs):
        self.attempts += 1
        if self.attempts <= self.failure_count:
            raise ConnectionError(f"Mock Connection Refused (attempt {self.attempts})")
        def mock_stream():
            yield {"type": "content", "content": "Success!"}
        return mock_stream()

def test_pipeline_infinite_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("NORYX_HOME", str(tmp_path / "noryx_home"))
    
    agent = Agent(api_key="test", model_key="dummy", working_dir=str(tmp_path))
    provider = MockFailingProvider(failure_count=50) # Fail a lot
    
    # Inject our failing provider
    agent.client = provider
    agent.budget.active_provider = provider
    
    # Set max turns to 3 so it shouldn't loop forever, BUT if the turn counter
    # doesn't increment on failure, it will loop infinitely.
    agent.max_turns = 3
    
    from nexus.pipeline import ExecutionPipeline
    pipeline = ExecutionPipeline(agent)
    
    from nexus.planner import ExecutionPlan, PlanStep, IntentType, Difficulty, PlanType
    plan = ExecutionPlan(
        id="test",
        goal="test",
        intent=IntentType.BUILD,
        difficulty=Difficulty.TRIVIAL,
        plan_type=PlanType.PLANNED,
        steps=[PlanStep(id=0, title="test", description="test")]
    )
    agent.planner.current_plan = plan
    
    try:
        import time
        agent.run_ledger.begin("test_model")
        t0 = time.time()
        # Call _run_hosted_execution directly to simulate running a plan
        response, events = pipeline._run_hosted_execution(
            "test",
            {"intent": MagicMock()},
            plan,
            interactive=False,
            emit_ui=False
        )
        t1 = time.time()
        
        # If it retried 50 times and then succeeded, or loops infinitely...
        # Wait, if we set failure_count to 5, and it retries infinitely, it will succeed on the 6th try!
        # If it succeeds on the 6th try, that means it bypassed max_turns=3 limit!
        # Let's see how many attempts it makes.
        print(f"Provider attempts: {provider.attempts}")
        print(f"Response: {response}")
        
        # It should not exceed max_turns (3).
        assert provider.attempts <= 3, f"Unbounded retries! Made {provider.attempts} attempts despite max_turns=3"
        
    except RuntimeError as e:
        pytest.fail(f"Agent crashed on provider error: {e}")
