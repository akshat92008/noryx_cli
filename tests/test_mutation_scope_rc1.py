"""Tests for mutation scope behaviors regarding read-only requests and greenfield projects."""

import os
from unittest.mock import MagicMock

import pytest

from nexus.agent import Agent
from nexus.policy import get_mode_policy
from nexus.providers.base import Provider


class MockReadOnlyProvider(Provider):
    @property
    def id(self):
        return "mock_ro"

    @property
    def name(self):
        return "Mock RO"

    def count_tokens(self, text):
        return len(text)

    def chat(self, model_id, messages, tools=None, stream=False, max_tokens=None, temperature=None, **kwargs):
        if stream:
            def _stream():
                class DummyChunk:
                    choices = [
                        type("Choice", (), {
                            "delta": type("Delta", (), {"content": "HOSTED_MODEL_OK", "tool_calls": []})()
                        })()
                    ]
                yield DummyChunk()
            return _stream()

        class DummyMessage:
            content = "HOSTED_MODEL_OK"
            tool_calls = []

        class DummyChoice:
            message = DummyMessage()

        class DummyResponse:
            choices = [DummyChoice()]

        return DummyResponse()

    chat_sync = chat


class MockMutatingProvider(Provider):
    @property
    def id(self):
        return "mock_mut"

    @property
    def name(self):
        return "Mock Mut"

    def count_tokens(self, text):
        return len(text)

    def chat(self, model_id, messages, tools=None, stream=False, max_tokens=None, temperature=None, **kwargs):
        class DummyFunction:
            def __init__(self):
                self.name = "write_file"
                self.arguments = '{"file_path": "new_app.py", "content": "print(1)"}'

        class DummyToolCall:
            def __init__(self):
                self.id = "call_123"
                self.function = DummyFunction()
                self.index = 0

        tc = [DummyToolCall()]
        
        if stream:
            def _stream():
                class DummyChunk:
                    choices = [
                        type("Choice", (), {
                            "delta": type("Delta", (), {"content": "", "tool_calls": tc})()
                        })()
                    ]
                yield DummyChunk()
            return _stream()

        class DummyMessage:
            content = ""
            tool_calls = tc

        class DummyChoice:
            message = DummyMessage()

        class DummyResponse:
            choices = [DummyChoice()]

        return DummyResponse()

    chat_sync = chat


def test_empty_repository_greenfield(tmp_path):
    """An empty repository must not block greenfield mutating requests."""
    # tmp_path is completely empty
    agent = Agent(
        working_dir=str(tmp_path),
        model_key="mock_mut/test",
        mode_policy=get_mode_policy("autonomous"),
        provider=MockMutatingProvider(),
    )
    
    # "create" intent maps to BUILD (mutating)
    prompt = "Create a small Python command-line calculator application in this empty repository."
    content, events = agent.run_non_interactive(prompt)
    report = agent.export_final_report()
    
    # Should not be blocked
    assert report.get("status") != "blocked"
    assert "could not establish a safe mutation scope" not in content


def test_readonly_request_empty_repo(tmp_path):
    """An empty repository must allow read-only requests."""
    agent = Agent(
        working_dir=str(tmp_path),
        model_key="mock_ro/test",
        mode_policy=get_mode_policy("autonomous"),
        provider=MockReadOnlyProvider(),
    )
    
    prompt = "Reply with exactly: HOSTED_MODEL_OK"
    content, events = agent.run_non_interactive(prompt)
    report = agent.export_final_report()
    
    assert report.get("status") != "blocked"
    assert "HOSTED_MODEL_OK" in content


def test_readonly_request_existing_repo(tmp_path):
    """An existing repository must allow read-only requests even if no decisive files are found."""
    # Create some files to make it an existing repository
    (tmp_path / "existing.py").write_text("print('hello')")
    
    agent = Agent(
        working_dir=str(tmp_path),
        model_key="mock_ro/test",
        mode_policy=get_mode_policy("autonomous"),
        provider=MockReadOnlyProvider(),
    )
    
    prompt = "Reply with exactly: HOSTED_MODEL_OK"
    content, events = agent.run_non_interactive(prompt)
    report = agent.export_final_report()
    
    assert report.get("status") != "blocked"
    assert "HOSTED_MODEL_OK" in content


def test_exact_literal_bypasses_mutating_provider(tmp_path):
    """Exact-literal requests use the deterministic read-only fast path."""
    existing = tmp_path / "existing.py"
    existing.write_text("print('hello')", encoding="utf-8")

    agent = Agent(
        working_dir=str(tmp_path),
        model_key="mock_mut/test",
        mode_policy=get_mode_policy("autonomous"),
        provider=MockMutatingProvider(),
    )

    content, events = agent.run_non_interactive("Reply with exactly: HOSTED_MODEL_OK")

    assert content == "HOSTED_MODEL_OK"
    assert existing.read_text(encoding="utf-8") == "print('hello')"
    assert not (tmp_path / "new_app.py").exists()
    assert not [event for event in events if event.get("type") == "tool_call"]
