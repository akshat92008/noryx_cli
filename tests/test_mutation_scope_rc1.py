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
    )
    agent.client = MockMutatingProvider()
    
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
    )
    agent.client = MockReadOnlyProvider()
    
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
    )
    agent.client = MockReadOnlyProvider()
    
    prompt = "Reply with exactly: HOSTED_MODEL_OK"
    content, events = agent.run_non_interactive(prompt)
    report = agent.export_final_report()
    
    assert report.get("status") != "blocked"
    assert "HOSTED_MODEL_OK" in content


def test_ambiguous_mutation_fails_closed(tmp_path):
    """An ambiguous request (UNKNOWN intent) that tries to mutate an existing repo should fail closed at the tool execution level."""
    # Existing repo
    (tmp_path / "existing.py").write_text("print('hello')")
    
    agent = Agent(
        working_dir=str(tmp_path),
        model_key="mock_mut/test",
        mode_policy=get_mode_policy("autonomous"),
    )
    agent.client = MockMutatingProvider()
    
    prompt = "Reply with exactly: HOSTED_MODEL_OK"  # Maps to UNKNOWN intent
    content, events = agent.run_non_interactive(prompt)
    
    # The provider tries to run write_file
    # The tool execution should fail with the exact block message
    tool_results = [event for event in events if event.get("type") == "tool_call"]
    assert any("BLOCKED: repository intelligence could not establish a safe mutation scope" in str(r) for r in tool_results)
