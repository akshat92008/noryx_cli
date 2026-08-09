"""
Tests for the runtime Execution Engine and State Machine.
"""

import time

from nexus.runtime.engine import ExecutionEngine
from nexus.runtime.events import EventType
from nexus.runtime.kernel import ExecutionKernel
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
                        self.id = "resp_mock"
                        self.usage = None

                yield DummyChunk(response.get("content"), response.get("tool_calls"))

            return _stream()
        return response


class StrictProtocolMockProvider:
    """Fix #14: A mock provider that strictly validates the OpenAI tool-calling
    message protocol.

    On turn 2 (after a tool_call was returned on turn 1), this provider asserts
    that the messages list contains a ``role=tool`` message with the matching
    ``tool_call_id``.  If the assertion fails the test immediately fails,
    proving that Fix #1 (tool-result propagation) is working correctly.
    """

    TOOL_CALL_ID = "call_abc123"

    def __init__(self):
        self.call_count = 0
        self.id = "strict_mock"
        self.name = "Strict Protocol Mock"
        self.received_messages_on_turn2 = None

    def chat(
        self,
        model_id,
        messages,
        tools=None,
        stream=True,
        max_tokens=None,
        temperature=None,
        **kwargs,
    ):
        self.call_count += 1

        if self.call_count == 1:
            # Turn 1: return a tool call

            def _stream_tool_call():
                class DummyToolCall:
                    def __init__(self):
                        self.index = 0
                        self.id = StrictProtocolMockProvider.TOOL_CALL_ID
                        self.function = type(
                            "F",
                            (),
                            {"name": "list_files", "arguments": '{"path": "."}'},
                        )()

                class DummyChunk:
                    class Choice:
                        class Delta:
                            def __init__(self):
                                self.content = None
                                self.tool_calls = [DummyToolCall()]

                        def __init__(self):
                            self.delta = self.Delta()

                    def __init__(self):
                        self.choices = [self.Choice()]
                        self.id = "resp_1"
                        self.usage = None

                yield DummyChunk()

            return _stream_tool_call()

        if self.call_count == 2:
            # Turn 2: ASSERT the message protocol is correct before responding.
            self.received_messages_on_turn2 = list(messages)
            tool_result_messages = [
                m
                for m in messages
                if m.get("role") == "tool"
                and m.get("tool_call_id") == self.TOOL_CALL_ID
            ]
            assert tool_result_messages, (
                f"Fix #1 regression: messages on turn 2 do not contain a role=tool message "
                f"with tool_call_id='{self.TOOL_CALL_ID}'.  The tool result was not propagated!\n"
                f"Messages received: {messages}"
            )

            def _stream_final():
                class DummyChunk:
                    class Choice:
                        class Delta:
                            def __init__(self):
                                self.content = "Done!"
                                self.tool_calls = None

                        def __init__(self):
                            self.delta = self.Delta()

                    def __init__(self):
                        self.choices = [self.Choice()]
                        self.id = "resp_2"
                        self.usage = None

                yield DummyChunk()

            return _stream_final()

        raise RuntimeError(f"Unexpected call #{self.call_count}")


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


# ============================================================================
# Fix #14: Strict tool-protocol regression tests
# ============================================================================


def test_tool_result_propagated_to_next_turn():
    """Fix #1 regression guard: the kernel MUST append a role=tool message after
    every tool call so the model can observe the outcome on the next turn.
    The StrictProtocolMockProvider asserts this invariant internally.
    """
    provider = StrictProtocolMockProvider()
    kernel = ExecutionKernel(provider, max_turns=5)

    def tool_executor(name, args):
        return True, "files: ['hello.py']"

    kernel.tool_executor = tool_executor

    events = list(kernel.run_interactive([{"role": "user", "content": "list files"}]))
    event_types = [e.type for e in events]

    # If StrictProtocolMockProvider didn't raise AssertionError, propagation is correct.
    assert EventType.RUN_COMPLETED in event_types, (
        f"Expected RUN_COMPLETED but got: {event_types}"
    )
    assert provider.call_count == 2, (
        f"Expected exactly 2 provider calls, got {provider.call_count}"
    )
    # Verify the turn-2 messages actually contain the role=tool message.
    assert provider.received_messages_on_turn2 is not None
    tool_msgs = [m for m in provider.received_messages_on_turn2 if m.get("role") == "tool"]
    assert tool_msgs, "Turn-2 messages must include at least one role=tool message"
    assert tool_msgs[0]["tool_call_id"] == StrictProtocolMockProvider.TOOL_CALL_ID
    assert tool_msgs[0]["content"] == "files: ['hello.py']"


def test_empty_response_triggers_run_failed():
    """Fix #3 regression: an empty provider response (no content, no tool calls)
    must be counted as PROVIDER_EMPTY_RESPONSE and ultimately fail, not succeed.
    """
    # Three consecutive empty responses (exceeds max_empty_retries=2)
    provider = MockProvider([
        {"content": "", "tool_calls": None},
        {"content": None, "tool_calls": None},
        {"content": "", "tool_calls": None},
    ])
    kernel = ExecutionKernel(provider, max_turns=5)
    events = list(kernel.run_interactive([{"role": "user", "content": "hello"}]))
    event_types = [e.type for e in events]
    assert EventType.RUN_FAILED in event_types, (
        f"Expected RUN_FAILED for repeated empty responses but got: {event_types}"
    )
    assert EventType.RUN_COMPLETED not in event_types, (
        "Empty response must NOT produce RUN_COMPLETED"
    )


def test_wall_clock_deadline_fires():
    """Fix #4 regression: when the wall-clock deadline is already in the past,
    the very first iteration must emit RUN_FAILED with 'deadline exceeded'.
    """
    provider = MockProvider([{"content": "This should never be reached", "tool_calls": None}])
    # Set deadline in the past
    past_deadline = time.monotonic() - 1.0
    kernel = ExecutionKernel(provider, max_turns=5, wall_clock_deadline=past_deadline)
    events = list(kernel.run_interactive([{"role": "user", "content": "hello"}]))
    event_types = [e.type for e in events]
    assert EventType.RUN_FAILED in event_types, (
        f"Expected RUN_FAILED for exceeded wall-clock deadline but got: {event_types}"
    )
    # Provider must NOT have been called at all — deadline fires before model request.
    assert provider.call_count == 0, (
        f"Provider should not be called when deadline is already exceeded; "
        f"got {provider.call_count} calls"
    )


def test_awaiting_approval_pauses_run_not_fails():
    """Fix #2 regression: AWAITING_APPROVAL must emit RUN_AWAITING_APPROVAL and halt
    cleanly.  The event type RUN_FAILED must NOT be present (this is not a failure).
    """
    APPROVAL_ID = "edit-abc"

    class DummyToolCall:
        index = 0
        id = "call_write"
        function = type(
            "F", (), {"name": "write_file", "arguments": '{"path":"a.py","content":"x"}'}
        )()

    provider = MockProvider([
        {"content": "", "tool_calls": [DummyToolCall()]},
    ])
    kernel = ExecutionKernel(provider, max_turns=5)

    def approval_tool_executor(name, args):
        # Simulate the AWAITING_APPROVAL sentinel from tool_executor.py
        sentinel = (
            f"__AWAITING_APPROVAL__:{APPROVAL_ID}\n"
            f"\u23f8\ufe0f The file edit has been queued for review.\n"
            f"Enter `/apply {APPROVAL_ID}` or `/reject {APPROVAL_ID}`."
        )
        return False, sentinel

    kernel.tool_executor = approval_tool_executor

    events = list(kernel.run_interactive([{"role": "user", "content": "write a.py"}]))
    event_types = [e.type for e in events]

    assert EventType.RUN_AWAITING_APPROVAL in event_types, (
        f"Expected RUN_AWAITING_APPROVAL event; got: {event_types}"
    )
    assert EventType.RUN_FAILED not in event_types, (
        "AWAITING_APPROVAL must NOT produce RUN_FAILED — it is a pause, not a failure"
    )

    # Verify the event carries the correct confirmation_id
    approval_events = [e for e in events if e.type == EventType.RUN_AWAITING_APPROVAL]
    assert len(approval_events) == 1
    assert approval_events[0].confirmation_id == APPROVAL_ID

def test_ephemeral_retry_context():
    """Fix #15: Verify that failure_replans is bounded so retries do not inflate history."""
    from nexus.planner import ExecutionPlan, PlanStep, IntentType, Difficulty, PlanType
    
    plan = ExecutionPlan(
        id="test-plan",
        goal="dummy task",
        intent=IntentType.UNKNOWN,
        difficulty=Difficulty.SIMPLE,
        plan_type=PlanType.DIRECT,
        steps=[PlanStep(id=1, title="step", description="step 1", subsystem="test")]
    )
    
    from nexus.planner import PlanningEngine
    planner = PlanningEngine()
    planner.current_plan = plan
    
    # Simulate repeated failures
    for i in range(10):
        plan.steps[0].status = "failed"
        planner.retry_step(1, failure_context=f"Error {i}")
        
    assert len(planner.current_plan.failure_replans) <= 2, "Failure replans must be bounded (ephemeral)"
    assert "Error 9" in planner.current_plan.failure_replans[-1]["evidence"], "Latest error must be preserved"
