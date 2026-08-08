"""Tests for End-to-End Provider behaviors."""

from nexus.providers.base import Provider
from nexus.runtime.engine import ExecutionEngine
from nexus.runtime.events import RunCompleted, RunFailed


class FakeProvider(Provider):
    def __init__(self, responses, simulate_rate_limit=False):
        self.responses = responses
        self.call_count = 0
        self.simulate_rate_limit = simulate_rate_limit
        self.rate_limit_raised = False

    @property
    def id(self):
        return "fake_provider"

    @property
    def name(self):
        return "Fake Provider"

    def chat(self, model_id, messages, tools=None, stream=False, max_tokens=None, temperature=None):
        if self.simulate_rate_limit and not self.rate_limit_raised:
            self.rate_limit_raised = True
            raise Exception("429 Rate limit exceeded")

        if self.call_count >= len(self.responses):
            if stream:

                def _stream():
                    class DummyChunk:
                        choices = [
                            type(
                                "Choice",
                                (),
                                {
                                    "delta": type(
                                        "Delta", (), {"content": "default", "tool_calls": []}
                                    )()
                                },
                            )()
                        ]

                    yield DummyChunk()

                return _stream()
            else:

                class DummyMessage:
                    content = "default"
                    tool_calls = []

                class DummyChoice:
                    message = DummyMessage()

                class DummyResponse:
                    choices = [DummyChoice()]

                return DummyResponse()

        resp = self.responses[self.call_count]
        self.call_count += 1

        if stream:

            def _stream():
                class DummyChunk:
                    choices = [
                        type(
                            "Choice",
                            (),
                            {
                                "delta": type(
                                    "Delta",
                                    (),
                                    {
                                        "content": resp.get("content", ""),
                                        "tool_calls": resp.get("tool_calls", []),
                                    },
                                )()
                            },
                        )()
                    ]

                yield DummyChunk()

            return _stream()
        else:

            class DummyMessage:
                content = resp.get("content", "")
                tool_calls = resp.get("tool_calls", [])

            class DummyChoice:
                message = DummyMessage()

            class DummyResponse:
                choices = [DummyChoice()]

            return DummyResponse()

    def chat_sync(
        self, model_id, messages, tools=None, max_tokens=None, temperature=None, **kwargs
    ):
        """Synchronous (non-streaming) version for protocol compliance."""
        return self.chat(
            model_id,
            messages,
            tools=tools,
            stream=False,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    def count_tokens(self, text):
        return len(text)


def test_hosted_chat_without_tools():
    provider = FakeProvider([{"content": "hello world"}])
    engine = ExecutionEngine(provider, max_turns=5, model_id="fake")
    events = list(engine.run(messages=[{"role": "user", "content": "hi"}]))

    completed = [e for e in events if isinstance(e, RunCompleted)]
    assert len(completed) == 1
    assert completed[0].content == "hello world"


def test_hosted_chat_with_one_tool_call():
    class DummyTC:
        index = 0
        id = "call_123"
        function = type("Func", (), {"name": "test_tool", "arguments": "{}"})()

    provider = FakeProvider([{"tool_calls": [DummyTC()]}, {"content": "done"}])
    engine = ExecutionEngine(provider, max_turns=5, model_id="fake")
    engine.tool_executor = lambda n, a: (True, "tool output")

    events = list(engine.run(messages=[{"role": "user", "content": "hi"}]))

    completed = [e for e in events if isinstance(e, RunCompleted)]
    assert len(completed) == 1
    assert completed[0].content == "done"


def test_hosted_rate_limit_exhaustion():
    provider = FakeProvider([{"content": "hello"}], simulate_rate_limit=True)
    engine = ExecutionEngine(provider, max_turns=5, model_id="fake")

    events = list(engine.run(messages=[{"role": "user", "content": "hi"}]))
    failed = [e for e in events if isinstance(e, RunFailed)]
    assert len(failed) == 1
    assert "Rate limit exceeded" in failed[0].error
