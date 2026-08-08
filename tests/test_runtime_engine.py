"""
Tests for the runtime Execution Engine and State Machine.
"""

from nexus.runtime.engine import ExecutionEngine
from nexus.runtime.events import EventType
from nexus.runtime.state_machine import RunState, StateMachine


class MockProvider:
    def __init__(self, responses):
        self.responses = responses
        self.call_count = 0
        self.id = "mock"
        self.name = "Mock Provider"

    def chat(
        self,
        model_id,
        messages,
        tools=None,
        stream=False,
        max_tokens=None,
        temperature=None,
        **kwargs,
    ):
        if self.call_count >= len(self.responses):
            raise RuntimeError("No more mock responses")

        response = self.responses[self.call_count]
        self.call_count += 1

        if stream:

            def _stream():
                class DummyChunk:
                    class Choice:
                        class Delta:
                            def __init__(self, content, tool_calls):
                                self.content = content
                                self.tool_calls = tool_calls

                        def __init__(self, content, tool_calls):
                            self.delta = self.Delta(content, tool_calls)

                    def __init__(self, content, tool_calls):
                        self.choices = [self.Choice(content, tool_calls)]

                yield DummyChunk(response.get("content"), response.get("tool_calls"))

            return _stream()
        return response


def test_state_machine():
    sm = StateMachine()
    assert sm.state == RunState.IDLE
    assert sm.transition_to(RunState.PLANNING)
    assert sm.state == RunState.PLANNING
    assert sm.transition_to(RunState.EXECUTING)
    assert not sm.transition_to(RunState.IDLE)  # Invalid
    assert sm.transition_to(RunState.COMPLETED)
    assert sm.is_terminal()


def test_execution_engine_simple_run():
    provider = MockProvider([{"content": "Hello, how can I help you?", "tool_calls": None}])
    engine = ExecutionEngine(provider, max_turns=2)

    events_received = []
    engine.add_event_handler(lambda e: events_received.append(e))

    messages = [{"role": "user", "content": "Hi"}]
    # Run the generator
    list(engine.run(messages))

    event_types = [e.type for e in events_received]
    assert EventType.RUN_STARTED in event_types
    assert EventType.MODEL_REQUEST_STARTED in event_types
    assert EventType.MODEL_STREAM_CHUNK in event_types
    assert EventType.RUN_COMPLETED in event_types

    assert engine.state_machine.state == RunState.COMPLETED


def test_execution_engine_tool_call():
    class DummyToolCall:
        def __init__(self, name, arguments):
            self.index = 0
            self.id = "call_123"
            self.function = type("obj", (object,), {"name": name, "arguments": arguments})

    provider = MockProvider(
        [
            # First turn: calls a tool
            {
                "content": "Let me check.",
                "tool_calls": [DummyToolCall("get_weather", '{"location": "Tokyo"}')],
            },
            # Second turn: observes result and answers
            {"content": "It is sunny in Tokyo.", "tool_calls": None},
        ]
    )

    engine = ExecutionEngine(provider, max_turns=3)

    # Mock tool executor
    tool_calls_made = []

    def mock_executor(name, args):
        tool_calls_made.append((name, args))
        return True, "Sunny"

    engine.tool_executor = mock_executor

    events = list(engine.run([{"role": "user", "content": "Weather in Tokyo?"}]))

    assert len(tool_calls_made) == 1
    assert tool_calls_made[0][0] == "get_weather"
    assert "Tokyo" in str(tool_calls_made[0][1])

    event_types = [e.type for e in events]
    assert EventType.TOOL_CALL_STARTED in event_types
    assert EventType.TOOL_CALL_COMPLETED in event_types
    assert engine.state_machine.state == RunState.COMPLETED
