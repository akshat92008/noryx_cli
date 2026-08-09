import pytest
from unittest.mock import MagicMock
from nexus.agent.core import Agent
from nexus.providers.base import Provider
from nexus.runtime.events import RunFailed

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
            raise ConnectionError("Mock Connection Refused")
        def mock_stream():
            yield {"type": "content", "content": "Success!"}
        return mock_stream()

def test_agent_provider_failure_recovery(tmp_path, monkeypatch):
    monkeypatch.setenv("NORYX_HOME", str(tmp_path / "noryx_home"))
    
    agent = Agent(api_key="test", model_key="dummy", working_dir=str(tmp_path))
    provider = MockFailingProvider(failure_count=1)
    
    # Inject our failing provider
    agent.client = provider
    agent.budget.active_provider = provider
    
    # Agent shouldn't crash! It should return the failure or retry.
    try:
        response, events = agent.run_non_interactive("hello")
    except RuntimeError as e:
        pytest.fail(f"Agent crashed on provider error: {e}")
    
    # Check if the pipeline retried
    print(f"Provider attempts: {provider.attempts}")
    print(f"Response: {response}")
    # If the pipeline successfully recovered, response would be "Success!"
    # If the pipeline bounded retries and gave up, it shouldn't crash.
