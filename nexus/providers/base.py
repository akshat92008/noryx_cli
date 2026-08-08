"""
Provider architecture for LLM backends.

All providers MUST implement this protocol exactly. Every wrapper in the chain
(HostedProvider, FallbackRouter, NovaProvider, BudgetedClient, test doubles)
must preserve both the synchronous and streaming forms and forward kwargs.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


class ProviderContractError(ValueError):
    """Raised before transport when a request exceeds a provider contract."""


@dataclass(frozen=True)
class ProviderCapabilities:
    """Machine-readable provider features used by adapters and routers."""

    streaming: bool = True
    tools: bool = True
    json_mode: bool = False
    parallel_tool_calls: bool = False
    max_context_tokens: int | None = None
    supported_options: frozenset[str] = frozenset()

    def labels(self) -> list[str]:
        labels = ["chat", "chat_sync"]
        if self.streaming:
            labels.append("streaming")
        if self.tools:
            labels.append("tools")
        if self.json_mode:
            labels.append("json_mode")
        if self.parallel_tool_calls:
            labels.append("parallel_tool_calls")
        return labels


@dataclass(frozen=True)
class ChatRequest:
    """Normalized request shared by provider adapters.

    The public provider protocol remains backwards compatible, while adapters
    validate legacy keyword arguments through this object before any network
    request is made.
    """

    model_id: str
    messages: list[dict]
    tools: list[dict] | None = None
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None
    options: dict[str, Any] = field(default_factory=dict)

    def validate(self, capabilities: ProviderCapabilities) -> "ChatRequest":
        if not self.model_id.strip():
            raise ProviderContractError("model_id must be a non-empty string")
        if not isinstance(self.messages, list):
            raise ProviderContractError("messages must be a list")
        if self.stream and not capabilities.streaming:
            raise ProviderContractError("provider does not support streaming requests")
        if self.tools and not capabilities.tools:
            raise ProviderContractError("provider does not support tool calls")
        unsupported = sorted(set(self.options) - set(capabilities.supported_options))
        if unsupported:
            raise ProviderContractError(
                "Unsupported provider option(s): " + ", ".join(unsupported)
            )
        if self.options.get("parallel_tool_calls") and not capabilities.parallel_tool_calls:
            raise ProviderContractError("provider does not support parallel tool calls")
        return self

    def client_kwargs(self) -> dict[str, Any]:
        """Return only transport options explicitly accepted by the adapter."""
        values: dict[str, Any] = dict(self.options)
        if self.max_tokens is not None:
            values["max_tokens"] = self.max_tokens
        if self.temperature is not None:
            values["temperature"] = self.temperature
        return values


class Provider(ABC):
    """Abstract base class for all LLM providers."""

    @property
    @abstractmethod
    def id(self) -> str:
        """Unique identifier for this provider instance (e.g. 'hosted', 'nova', 'router')."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of the provider."""

    @property
    def model_id(self) -> str:
        """The effective model string to send to the API.

        Providers backed by a real model should override this to return the
        actual model identifier (e.g. 'z-ai/glm-5.2'). Defaults to ``id`` for
        backward-compatibility with providers that do not distinguish between
        the two concepts.
        """
        return self.id

    @property
    def capabilities(self) -> ProviderCapabilities:
        """Return the provider's normalized capability declaration."""
        return ProviderCapabilities()

    @abstractmethod
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
        """Send a chat completion request to the model.

        Args:
            model_id: The ID of the model to use.
            messages: List of message dictionaries (role, content).
            tools: List of tool definitions.
            stream: Whether to stream the response.
            max_tokens: Optional hard cap on completion tokens.
            temperature: Optional sampling temperature.
            **kwargs: Additional provider-specific parameters.

        Returns:
            A stream (generator / Iterator) if stream=True, else a single
            response object.
        """

    @abstractmethod
    def chat_sync(
        self,
        model_id: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> Any:
        """Blocking (non-streaming) chat completion.

        Must be implemented by every provider. The two-node backend and any
        other synchronous code path rely exclusively on this method.
        """

    @abstractmethod
    def count_tokens(self, text: str) -> int:
        """Count the number of tokens in the given text."""

    def get_capabilities(self) -> list[str]:
        """Return a list of capabilities this provider supports."""
        return self.capabilities.labels()

    def close(self) -> None:
        """Release provider-owned resources.

        Providers that own sockets, HTTP clients, subprocesses, executors, or
        other long-lived resources should override this method.  Keeping the
        method on the base protocol gives callers one deterministic lifecycle
        hook without forcing light-weight test doubles to implement it.
        """

    def __enter__(self) -> "Provider":
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()

class ModelProvider(Provider):
    """
    Modern target interface for Sprint 3.
    """
    @abstractmethod
    def complete(self, messages: list[dict], tools: list[dict] | None = None, **kwargs: Any) -> Any:
        pass
        
    @abstractmethod
    def stream(self, messages: list[dict], tools: list[dict] | None = None, **kwargs: Any) -> Any:
        pass

    @abstractmethod
    def supports_tools(self) -> bool:
        pass

    @abstractmethod
    def supports_structured_output(self) -> bool:
        pass

    @abstractmethod
    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        pass
