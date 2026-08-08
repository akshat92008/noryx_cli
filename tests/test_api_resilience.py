"""
Test suit for API resilience: key rotation and Groq failover.
"""

import os
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from nexus.api import DEFAULT_GROQ_MODEL, NvidiaClient
from nexus.two_node_backend import CeilingCallTimeout, _run_ceiling_call


@pytest.fixture(autouse=True)
def provider_keys(monkeypatch):
    """Keep resilience tests independent of a developer's local .env file."""
    # These tests replace every transport with an in-memory fake.
    monkeypatch.delenv("NEXUS_DISABLE_NETWORK", raising=False)
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")


@patch.dict(
    os.environ,
    {
        "GROQ_API_KEY": "fake_groq_key",
        "NVIDIA_FALLBACK_API_KEY_1": "fake_nvidia_key1",
        "NVIDIA_API_KEY": "fake_nvidia_key",
    },
)
def test_api_client_key_loading():
    """Verify that primary key, fallback keys, and Groq key are loaded."""
    client = NvidiaClient()
    assert len(client.nvidia_keys) >= 2
    assert client.groq_key == "fake_groq_key"


def test_groq_model_resolution():
    """Verify model mapping to Groq models for tool calling compatibility."""
    client = NvidiaClient()
    assert client.resolve_groq_model("z-ai/glm-5.2") == "openai/gpt-oss-120b"
    assert client.resolve_groq_model("meta/llama-3.3-70b-instruct") == "openai/gpt-oss-120b"
    assert client.resolve_groq_model("deepseek-ai/deepseek-v4-pro") == "openai/gpt-oss-120b"
    assert client.resolve_groq_model("unknown-model") == DEFAULT_GROQ_MODEL


def test_client_timeout():
    """Verify hosted inference gets enough time to produce a first token."""
    client = NvidiaClient()
    assert client.timeout == 60.0
    assert client.client.timeout == 60.0


def test_groq_only_configuration_is_supported(monkeypatch):
    """A Groq key is sufficient to start the hosted client."""
    for name in list(os.environ):
        if name.startswith(("NVIDIA_API_KEY", "NVIDIA_FALLBACK_API_KEY")):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk-only")
    monkeypatch.setattr("nexus.api._load_env_file", lambda: None)

    client = NvidiaClient()

    assert client.nvidia_keys == []
    assert client.groq_keys == ["gsk-only"]
    assert str(client.client.base_url) == "https://api.groq.com/openai/v1/"


def test_openrouter_only_configuration_is_supported(monkeypatch):
    """An OpenRouter key is sufficient to start the hosted client."""
    for name in list(os.environ):
        if name.startswith(
            (
                "NVIDIA_API_KEY",
                "NVIDIA_FALLBACK_API_KEY",
                "GROQ_API_KEY",
                "GROQ_FALLBACK_API_KEY",
            )
        ):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-only")
    monkeypatch.setattr("nexus.api._load_env_file", lambda: None)

    client = NvidiaClient()

    assert client.nvidia_keys == []
    assert client.groq_keys == []
    assert str(client.client.base_url) == "https://openrouter.ai/api/v1/"


def test_groq_fallback_execution(monkeypatch):
    """Verify that when NVIDIA attempts fail, it automatically falls back to Groq."""
    monkeypatch.delenv("NORYX_DISABLE_NETWORK", raising=False)
    monkeypatch.delenv("NEXUS_DISABLE_NETWORK", raising=False)
    client = NvidiaClient()

    # Mock _get_nvidia_client to raise TimeoutError
    def mock_nvidia_fail(key):
        m = MagicMock()
        m.chat.completions.create.side_effect = TimeoutError("Request timed out.")
        return m

    # Mock Groq client to return success
    mock_groq_response = MagicMock()
    mock_groq_response.choices = [MagicMock(message=MagicMock(content="Groq fallback success!"))]

    mock_groq_instance = MagicMock()
    mock_groq_instance.chat.completions.create.return_value = mock_groq_response

    with patch.object(client, "_get_nvidia_client", side_effect=mock_nvidia_fail):
        with patch.object(client, "_get_groq_client", return_value=mock_groq_instance):
            resp = client.chat_sync(
                model_id="z-ai/glm-5.2",
                messages=[{"role": "user", "content": "hello"}],
            )
            assert resp.choices[0].message.content == "Groq fallback success!"


def test_active_round_robin_key_rotation(monkeypatch):
    """Verify that successful requests advance current_key_idx in a Round-Robin cycle."""
    monkeypatch.delenv("NORYX_DISABLE_NETWORK", raising=False)
    monkeypatch.delenv("NEXUS_DISABLE_NETWORK", raising=False)
    client = NvidiaClient()
    client.nvidia_keys = ["key1", "key2", "key3"]
    client.current_key_idx = 0

    mock_resp = MagicMock()

    def mock_nvidia_success(key):
        m = MagicMock()
        m.chat.completions.create.return_value = mock_resp
        return m

    with patch.object(client, "_get_nvidia_client", side_effect=mock_nvidia_success):
        # Call 1 -> Uses key1 (idx 0), advances current_key_idx to 1
        client.chat(model_id="test", messages=[{"role": "user", "content": "hello"}], stream=False)
        assert client.current_key_idx == 1

        # Call 2 -> Uses key2 (idx 1), advances current_key_idx to 2
        client.chat(model_id="test", messages=[{"role": "user", "content": "hello"}], stream=False)
        assert client.current_key_idx == 2

        # Call 3 -> Uses key3 (idx 2), advances current_key_idx to 0
        client.chat(model_id="test", messages=[{"role": "user", "content": "hello"}], stream=False)
        assert client.current_key_idx == 0


def test_cloud_api_exhaustion_falls_back_to_local_nova():
    """Verify that when all cloud APIs fail, agent.run falls back to local Nova turn."""
    import sys

    from nexus.agent import Agent

    print("Setting up agent...", file=sys.stderr, flush=True)
    with Agent(
        api_key="nvapi-test",
        model_key="deepseek-v4",
        enable_nova_fallback=True,
    ) as agent:
        agent.local_intern_enabled = True
        agent.repo_graph = MagicMock()
        agent.repo_graph.context_bundle.return_value = "Mocked Graph Context"

        print("Patching client...", file=sys.stderr, flush=True)
        # Mock client.stream to simulate cloud rate limit exhaustion on a chat query.
        with (
            patch.object(
                agent.client,
                "stream",
                side_effect=RuntimeError("Rate limited after multiple retries"),
            ),
            patch.object(
                agent.client,
                "chat",
                side_effect=RuntimeError("Rate limited after multiple retries"),
            ),
        ):
            with patch.object(
                agent, "_run_nova_turn", return_value=("Local Nova fallback response", [])
            ) as mock_nova:
                print("Calling agent.run...", file=sys.stderr, flush=True)
                res = agent.run("hello, explain binary search trees")
                print(f"agent.run finished with result: {res}", file=sys.stderr, flush=True)
                assert res == "Local Nova fallback response"
                mock_nova.assert_called_once()
                print("Test passed.", file=sys.stderr, flush=True)


def test_round_robin_key_pool():
    """Verify explicit RoundRobinKeyPool cycling and cooldown skipping."""
    from nexus.api import NvidiaClient, RoundRobinKeyPool

    pool = RoundRobinKeyPool(["k1", "k2", "k3"])
    assert pool.get_next_key() == "k1"
    assert pool.get_next_key() == "k2"
    assert pool.get_next_key() == "k3"
    assert pool.get_next_key() == "k1"

    # Mark k1 in cooldown
    pool.mark_cooldown("k1", duration=60.0)
    assert pool.is_cooldown("k1") is True
    # Should skip k1 and return k2
    assert pool.get_next_key() == "k2"

    client = NvidiaClient()
    client.nvidia_keys = ["keyA", "keyB"]
    client.nvidia_pool = RoundRobinKeyPool(client.nvidia_keys)
    assert client.get_next_key("nvidia") == "keyA"
    assert client.get_next_key("nvidia") == "keyB"


def test_ceiling_timeout_works_outside_main_thread():
    result = {}

    def run():
        try:
            _run_ceiling_call(lambda: time.sleep(0.1), 0.01)
        except CeilingCallTimeout:
            result["timed_out"] = True

    worker = threading.Thread(target=run)
    worker.start()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert result == {"timed_out": True}


def test_timed_out_ceiling_transport_cannot_block_process_shutdown():
    release = threading.Event()
    result = {}

    def run():
        try:
            _run_ceiling_call(lambda: release.wait(timeout=1), 0.01)
        except CeilingCallTimeout as exc:
            result["error"] = str(exc)

    caller = threading.Thread(target=run)
    caller.start()
    caller.join(timeout=0.5)

    transports = [thread for thread in threading.enumerate() if thread.name == "nexus-ceiling-call"]
    try:
        assert not caller.is_alive()
        assert "daemonized transport" in result["error"]
        assert transports
        assert all(thread.daemon for thread in transports)
    finally:
        release.set()
        for thread in transports:
            thread.join(timeout=0.5)


def test_hosted_client_owns_and_closes_cached_transports(monkeypatch):
    created = []

    class FakeTransport:
        def __init__(self, **kwargs):
            self.base_url = kwargs["base_url"]
            self.timeout = kwargs["timeout"]
            self.closed = 0
            created.append(self)

        def close(self):
            self.closed += 1

    monkeypatch.setattr("nexus.api.OpenAI", FakeTransport)
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-lifecycle")
    monkeypatch.setenv("GROQ_API_KEY", "gsk-lifecycle")
    monkeypatch.setattr("nexus.api._load_env_file", lambda: None)

    client = NvidiaClient()
    first = client._get_nvidia_client("nvapi-lifecycle")
    assert first is client._get_nvidia_client("nvapi-lifecycle")
    groq = client._get_groq_client("gsk-lifecycle")
    assert len(created) == 2

    client.close()
    client.close()

    assert first.closed == 1
    assert groq.closed == 1
    with pytest.raises(RuntimeError, match="closed"):
        client._get_nvidia_client("nvapi-lifecycle")


def test_observed_stream_close_finalizes_cancelled_once():
    from nexus.api import _ObservedStream

    statuses = []

    class Stream:
        def __init__(self):
            self.closed = 0

        def close(self):
            self.closed += 1

    stream = Stream()
    observed = _ObservedStream(stream, lambda status, _metadata: statuses.append(status))
    observed.close()
    observed.close()

    assert statuses == ["cancelled"]
    assert stream.closed == 1
