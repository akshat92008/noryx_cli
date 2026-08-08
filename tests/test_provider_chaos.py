"""Offline chaos tests for provider contracts and failover semantics."""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from nexus.providers.base import Provider, ProviderCapabilities, ProviderContractError
from nexus.providers.hosted import HostedProvider
from nexus.providers.router import FallbackRouter


@pytest.fixture(autouse=True)
def allow_in_memory_provider_transports(monkeypatch):
    """The suite replaces provider transports with fakes unless a test opts out."""
    monkeypatch.delenv("NEXUS_DISABLE_NETWORK", raising=False)


class FakeProvider(Provider):
    def __init__(
        self,
        provider_id: str,
        *,
        model_id: str | None = None,
        sync_result: object = "ok",
        stream_factory=None,
        fail_sync: Exception | None = None,
        capabilities: ProviderCapabilities | None = None,
    ):
        self._id = provider_id
        self._model_id = model_id or provider_id
        self.sync_result = sync_result
        self.stream_factory = stream_factory or (lambda: iter(["ok"]))
        self.fail_sync = fail_sync
        self.requested_models: list[str] = []
        self._capabilities = capabilities or ProviderCapabilities()

    @property
    def id(self) -> str:
        return self._id

    @property
    def name(self) -> str:
        return self._id

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

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
        self.requested_models.append(model_id)
        if stream:
            return self.stream_factory()
        return self.chat_sync(model_id, messages, tools, max_tokens, temperature, **kwargs)

    def chat_sync(
        self,
        model_id,
        messages,
        tools=None,
        max_tokens=None,
        temperature=None,
        **kwargs,
    ):
        self.requested_models.append(model_id)
        if self.fail_sync:
            raise self.fail_sync
        return self.sync_result

    def count_tokens(self, text: str) -> int:
        return len(text)


def test_hosted_provider_rejects_unknown_option_before_transport(monkeypatch):
    class Client:
        attempt_telemetry_enabled = False

        def __init__(self, **_kwargs):
            self.called = False

        def chat_sync(self, **_kwargs):
            self.called = True
            return "unexpected"

    monkeypatch.setattr("nexus.providers.hosted.NvidiaClient", Client)
    provider = HostedProvider(api_key="test")

    with pytest.raises(ProviderContractError, match="unsupported_option"):
        provider.chat_sync("model", [], unsupported_option=True)
    assert provider._client.called is False


def test_hosted_provider_forwards_normalized_supported_options(monkeypatch):
    captured = {}

    class Client:
        attempt_telemetry_enabled = False

        def __init__(self, **_kwargs):
            pass

        def chat_sync(self, **kwargs):
            captured.update(kwargs)
            return "ok"

    monkeypatch.setattr("nexus.providers.hosted.NvidiaClient", Client)
    provider = HostedProvider(api_key="test")

    assert provider.chat_sync(
        "model",
        [{"role": "user", "content": "hello"}],
        response_format={"type": "json_object"},
        seed=7,
        top_p=0.8,
    ) == "ok"
    assert captured["response_format"] == {"type": "json_object"}
    assert captured["seed"] == 7
    assert captured["top_p"] == 0.8


def test_sync_fallback_uses_fallback_provider_model():
    primary = FakeProvider("primary", fail_sync=RuntimeError("429"))
    fallback = FakeProvider("fallback", model_id="fallback/frontier", sync_result="recovered")
    router = FallbackRouter(primary, [fallback])

    assert router.chat_sync("primary/frontier", []) == "recovered"
    assert fallback.requested_models == ["fallback/frontier"]


def test_stream_falls_back_when_primary_fails_before_first_chunk():
    def fail_before_output() -> Iterable[str]:
        raise RuntimeError("connection reset")
        yield "unreachable"

    primary = FakeProvider("primary", stream_factory=fail_before_output)
    fallback = FakeProvider(
        "fallback",
        model_id="fallback/model",
        stream_factory=lambda: iter(["recovered"]),
    )
    router = FallbackRouter(primary, [fallback])

    assert list(router.chat("primary/model", [], stream=True)) == ["recovered"]
    assert fallback.requested_models == ["fallback/model"]


def test_stream_never_replays_after_partial_output():
    def fail_after_output():
        yield "partial"
        raise RuntimeError("mid-stream disconnect")

    primary = FakeProvider("primary", stream_factory=fail_after_output)
    fallback = FakeProvider("fallback", stream_factory=lambda: iter(["duplicate"]))
    router = FallbackRouter(primary, [fallback])

    stream = router.chat("primary/model", [], stream=True)
    assert next(stream) == "partial"
    with pytest.raises(RuntimeError, match="mid-stream"):
        next(stream)
    assert fallback.requested_models == []


def test_router_exposes_capability_intersection():
    primary = FakeProvider(
        "primary",
        capabilities=ProviderCapabilities(
            json_mode=True,
            parallel_tool_calls=True,
            supported_options=frozenset({"seed", "response_format"}),
        ),
    )
    fallback = FakeProvider(
        "fallback",
        capabilities=ProviderCapabilities(
            json_mode=False,
            parallel_tool_calls=False,
            supported_options=frozenset({"seed"}),
        ),
    )

    capabilities = FallbackRouter(primary, [fallback]).capabilities
    assert capabilities.json_mode is False
    assert capabilities.parallel_tool_calls is False
    assert capabilities.supported_options == frozenset({"seed"})


def test_provider_transport_honors_global_network_kill_switch(monkeypatch):
    from nexus.api import NvidiaClient

    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.setenv("NEXUS_DISABLE_NETWORK", "1")
    client = NvidiaClient()
    monkeypatch.setattr(
        client,
        "_get_nvidia_client",
        lambda _key: pytest.fail("transport must not initialize while network is disabled"),
    )

    with pytest.raises(RuntimeError, match="NEXUS_DISABLE_NETWORK"):
        client.chat_sync("test/model", [])


# ─── Extended chaos / contract tests ─────────────────────────────────────────


def test_timeout_primary_falls_back_successfully():
    """Primary raises TimeoutError; fallback must succeed and deliver full response."""
    primary = FakeProvider("primary", fail_sync=TimeoutError("provider timed out"))
    fallback = FakeProvider("fallback", model_id="fb/model", sync_result="timeout_recovered")
    router = FallbackRouter(primary, [fallback])
    result = router.chat_sync("primary/model", [{"role": "user", "content": "hi"}])
    assert result == "timeout_recovered"
    assert fallback.requested_models == ["fb/model"]


def test_retry_exhaustion_propagates_last_error():
    """When all providers fail the router must raise — not silently return None."""
    primary = FakeProvider("primary", fail_sync=ConnectionError("primary connection refused"))
    fallback = FakeProvider("fallback", fail_sync=TimeoutError("fallback timeout"))
    router = FallbackRouter(primary, [fallback])
    with pytest.raises((ConnectionError, TimeoutError, RuntimeError)):
        router.chat_sync("primary/model", [])


def test_streaming_fallback_delivers_all_chunks():
    """When primary fails before first chunk, fallback must deliver ALL chunks."""
    def explode():
        raise RuntimeError("stream refused")
        yield "never"

    fallback_chunks = ["chunk1", "chunk2", "chunk3"]
    primary = FakeProvider("primary", stream_factory=explode)
    fallback = FakeProvider(
        "fallback",
        model_id="fb/model",
        stream_factory=lambda: iter(fallback_chunks),
    )
    router = FallbackRouter(primary, [fallback])
    result = list(router.chat("primary/model", [], stream=True))
    assert result == fallback_chunks


def test_fallback_router_is_thread_safe_under_concurrent_chat():
    """FallbackRouter used from 10 threads concurrently must not corrupt state."""
    import threading

    results: list = []
    errors: list = []
    lock = threading.Lock()

    primary = FakeProvider("primary", sync_result="ok")
    router = FallbackRouter(primary, [])

    def call():
        try:
            result = router.chat_sync("primary/model", [{"role": "user", "content": "x"}])
            with lock:
                results.append(result)
        except Exception as exc:
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=call) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors, f"Thread-safety errors: {errors[:3]}"
    assert len(results) == 10
    assert all(r == "ok" for r in results)


def test_fallback_not_triggered_on_sync_success():
    """If the primary succeeds, the fallback must NEVER be called."""
    primary = FakeProvider("primary", sync_result="primary_ok")
    fallback = FakeProvider("fallback", sync_result="fallback_ok")
    router = FallbackRouter(primary, [fallback])
    result = router.chat_sync("primary/model", [{"role": "user", "content": "hi"}])
    assert result == "primary_ok"
    assert not fallback.requested_models, "Fallback must not be invoked when primary succeeds"


def test_fallback_records_all_attempted_providers():
    """The router must try all providers in chain before giving up."""
    p1 = FakeProvider("p1", fail_sync=RuntimeError("p1 down"))
    p2 = FakeProvider("p2", fail_sync=RuntimeError("p2 down"))
    p3 = FakeProvider("p3", sync_result="ok")
    router = FallbackRouter(p1, [p2, p3])
    result = router.chat_sync("p1/model", [{"role": "user", "content": "x"}])
    assert result == "ok"
    assert p1.requested_models
    assert p2.requested_models
    assert p3.requested_models


def test_streaming_preserves_empty_chunks_from_provider():
    """Streaming must faithfully forward empty string chunks from the provider."""
    chunks = ["", "data", "", "end", ""]
    primary = FakeProvider("primary", stream_factory=lambda: iter(chunks))
    router = FallbackRouter(primary, [])
    result = list(router.chat("primary/model", [], stream=True))
    assert result == chunks
