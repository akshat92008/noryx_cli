"""Nova local-model provider adapter with an OpenAI-compatible response shape."""

import json
import logging
from types import SimpleNamespace
from typing import Any

from nexus.nova_backend import NovaPipelineBackend
from nexus.providers.base import ModelProvider

logger = logging.getLogger(__name__)


class NovaProvider(ModelProvider):
    """Adapter for the local Nova 3B pipeline backend."""

    def __init__(self, model_name: str, working_dir: str):
        self.model_name = model_name
        self.working_dir = working_dir
        self._backend = NovaPipelineBackend(
            model=model_name,
            working_dir=working_dir,
        )
        self._last_result = None

    # ── Provider protocol ────────────────────────────────────────────────────

    @property
    def id(self) -> str:
        return "nova"

    @property
    def name(self) -> str:
        return "Nova Local Pipeline"

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
        """Run the Nova pipeline with the last user message as input.

        max_tokens / temperature are accepted for protocol compatibility but
        are not forwarded to the local pipeline (Nova manages its own limits).
        """
        user_input = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                user_input = content if isinstance(content, str) else str(content)
                break

        # After the runtime has executed the proposals from the previous call,
        # return a clean terminal response instead of generating and applying the
        # same patch repeatedly until the turn budget is exhausted.
        last_user_index = max(
            (index for index, item in enumerate(messages) if item.get("role") == "user"),
            default=-1,
        )
        has_tool_result = any(
            item.get("role") == "tool" for item in messages[last_user_index + 1 :]
        )
        if has_tool_result and self._last_result is not None:
            result = self._last_result
            proposals = []
            content = result.assistant_text or "Nova tool execution completed."
        else:
            result = self._backend.run(user_input)
            self._last_result = result
            proposals = list(result.proposals)
            content = result.assistant_text or result.raw_output

        tool_calls = [
            SimpleNamespace(
                index=index,
                id=f"nova-call-{index}",
                type="function",
                function=SimpleNamespace(
                    name=proposal.name,
                    arguments=json.dumps(proposal.args, ensure_ascii=False),
                ),
            )
            for index, proposal in enumerate(proposals)
        ]
        if proposals and result.test_command:
            index = len(tool_calls)
            tool_calls.append(
                SimpleNamespace(
                    index=index,
                    id=f"nova-call-{index}",
                    type="function",
                    function=SimpleNamespace(
                        name="run_command",
                        arguments=json.dumps(
                            {"command": result.test_command, "cwd": self.working_dir},
                            ensure_ascii=False,
                        ),
                    ),
                )
            )

        if stream:
            def _stream():
                delta = SimpleNamespace(content=content, tool_calls=tool_calls)
                yield SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=None)

            return _stream()

        message = SimpleNamespace(content=content, tool_calls=tool_calls)
        response = SimpleNamespace(
            choices=[SimpleNamespace(message=message)],
            usage=None,
            nexus_result=result,
        )
        return response

    def chat_sync(
        self,
        model_id: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> Any:
        """Blocking Nova pipeline call (same as non-streaming chat)."""
        return self.chat(
            model_id,
            messages,
            tools=tools,
            stream=False,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )

    def count_tokens(self, text: str) -> int:
        return len(text) // 4  # Fallback estimate

    # ── ModelProvider target interface ───────────────────────────────────────

    def complete(self, messages: list[dict], tools: list[dict] | None = None, **kwargs: Any) -> Any:
        return self.chat_sync(self.model_id, messages, tools=tools, **kwargs)

    def stream(self, messages: list[dict], tools: list[dict] | None = None, **kwargs: Any) -> Any:
        return self.chat(self.model_id, messages, tools=tools, stream=True, **kwargs)

    def supports_tools(self) -> bool:
        return True

    def supports_structured_output(self) -> bool:
        return True

    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return 0.0  # Nova is local
