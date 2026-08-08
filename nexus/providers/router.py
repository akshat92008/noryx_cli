"""
Cost-aware routing and fallback for LLM providers.
"""

import logging
import threading
from typing import Any

from nexus.providers.base import Provider, ProviderCapabilities

logger = logging.getLogger(__name__)


class FallbackRouter(Provider):
    """Wraps multiple providers and handles automatic fallback if a provider fails.

    The router implements the full Provider protocol so that BudgetedClient and
    other wrappers can use it transparently — including chat_sync() and proper
    forwarding of max_tokens / temperature kwargs.
    """

    def __init__(self, primary: Provider, fallbacks: list[Provider] | None = None):
        self._primary = primary
        self._fallbacks = fallbacks or []
        self._active_provider = self._primary
        self._fallback_index = 0
        self._lock = threading.RLock()

    # ── Provider protocol ────────────────────────────────────────────────────

    @property
    def id(self) -> str:
        """Return the active provider's id rather than the hardcoded 'router' string.

        The engine uses ``provider.id`` as the model string fallback. Returning
        the active provider's id (e.g. 'hosted') means we don't accidentally
        send the literal string 'router' to the API.
        """
        return self._active_provider.id

    @property
    def model_id(self) -> str:  # type: ignore[override]
        """Delegate model_id to the currently active provider."""
        return self._active_provider.model_id

    @property
    def name(self) -> str:
        return f"Router (Active: {self._active_provider.name})"

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Expose only features supported across every possible route."""
        capabilities = [provider.capabilities for provider in [self._primary, *self._fallbacks]]
        context_limits = [item.max_context_tokens for item in capabilities if item.max_context_tokens]
        supported_options = set(capabilities[0].supported_options)
        for item in capabilities[1:]:
            supported_options.intersection_update(item.supported_options)
        return ProviderCapabilities(
            streaming=all(item.streaming for item in capabilities),
            tools=all(item.tools for item in capabilities),
            json_mode=all(item.json_mode for item in capabilities),
            parallel_tool_calls=all(item.parallel_tool_calls for item in capabilities),
            max_context_tokens=min(context_limits) if context_limits else None,
            supported_options=frozenset(supported_options),
        )

    @property
    def attempt_telemetry_enabled(self) -> bool:
        providers = [self._primary, *self._fallbacks]
        return bool(providers) and all(
            getattr(provider, "attempt_telemetry_enabled", False) for provider in providers
        )

    # ── Fallback control ─────────────────────────────────────────────────────

    def switch_to_fallback(self) -> bool:
        """Switch to the next fallback provider. Returns True if successful."""
        with self._lock:
            if self._fallback_index < len(self._fallbacks):
                self._active_provider = self._fallbacks[self._fallback_index]
                self._fallback_index += 1
                logger.warning("Switched to fallback provider: %s", self._active_provider.name)
                return True
            return False

    def reset(self) -> None:
        """Reset back to the primary provider."""
        with self._lock:
            self._active_provider = self._primary
            self._fallback_index = 0

    def _active_request(self, requested_model_id: str) -> tuple[Provider, str]:
        with self._lock:
            provider = self._active_provider
        if provider is self._primary:
            return provider, requested_model_id
        fallback_model = str(getattr(provider, "model_id", "") or "").strip()
        return provider, fallback_model or requested_model_id

    # ── Core chat methods ────────────────────────────────────────────────────

    def chat(
        self,
        model_id: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        stream: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> Any:
        if stream:
            return self._stream_with_fallback(
                model_id,
                messages,
                tools,
                max_tokens=max_tokens,
                temperature=temperature,
                **kwargs,
            )
        return self.chat_sync(
            model_id,
            messages,
            tools,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )

    def _stream_with_fallback(
        self,
        model_id: str,
        messages: list[dict],
        tools: list[dict] | None,
        *,
        max_tokens: int | None,
        temperature: float | None,
        **kwargs: Any,
    ):
        """Fail over a stream only before its first chunk.

        Retrying after output has been emitted can duplicate tool calls or text,
        so mid-stream failures are surfaced instead of replayed.
        """
        while True:
            provider, effective_model = self._active_request(model_id)
            emitted = False
            try:
                response = provider.chat(
                    effective_model,
                    messages,
                    tools,
                    stream=True,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **kwargs,
                )
                for chunk in response:
                    emitted = True
                    yield chunk
                return
            except Exception as exc:
                logger.warning("Provider %s stream failed: %s", provider.name, exc)
                if emitted or not self.switch_to_fallback():
                    raise

    def chat_sync(
        self,
        model_id: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> Any:
        """Blocking (non-streaming) chat with automatic fallback."""
        while True:
            provider, effective_model = self._active_request(model_id)
            try:
                return provider.chat_sync(
                    effective_model,
                    messages,
                    tools,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    **kwargs,
                )
            except Exception as exc:
                logger.warning("Provider %s failed in chat_sync: %s", provider.name, exc)
                if not self.switch_to_fallback():
                    raise

    def count_tokens(self, text: str) -> int:
        return self._active_provider.count_tokens(text)

    def close(self) -> None:
        """Close every unique provider owned by the router."""

        errors: list[BaseException] = []
        seen: set[int] = set()
        for provider in [self._primary, *self._fallbacks]:
            if id(provider) in seen:
                continue
            seen.add(id(provider))
            closer = getattr(provider, "close", None)
            if not callable(closer):
                continue
            try:
                closer()
            except BaseException as exc:
                errors.append(exc)
        if errors:
            raise RuntimeError(
                "Failed to close provider router resources: "
                + "; ".join(f"{type(exc).__name__}: {exc}" for exc in errors)
            )
