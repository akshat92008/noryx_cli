"""Small OpenAI-compatible client with an optional official-SDK fast path.

Nexus declares the official ``openai`` package as a normal dependency.  This
module keeps source checkouts, diagnostics, and local-only workflows usable
when that package has not been installed yet.  When the SDK is available it is
used unchanged; otherwise a narrow HTTPX implementation covers the Chat
Completions surface Nexus needs.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Iterator

try:  # pragma: no cover - exercised when the official dependency is present
    from openai import OpenAI as OpenAI  # type: ignore[assignment]
except ImportError:  # pragma: no cover - fallback is covered in this repository
    import httpx

    def _ns(value: Any) -> Any:
        if isinstance(value, dict):
            return SimpleNamespace(**{key: _ns(item) for key, item in value.items()})
        if isinstance(value, list):
            return [_ns(item) for item in value]
        return value

    class _Completions:
        def __init__(self, owner: "OpenAI"):
            self._owner = owner

        def create(self, *, model: str, **kwargs: Any) -> Any:
            payload = {"model": model, **kwargs}
            stream = bool(payload.get("stream"))
            headers = {
                "Authorization": f"Bearer {self._owner.api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if stream else "application/json",
            }
            url = f"{str(self._owner.base_url).rstrip('/')}/chat/completions"
            if stream:
                return self._stream(url, headers, payload)
            with httpx.Client(timeout=self._owner.timeout, follow_redirects=False) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
                return _ns(response.json())

        def _stream(
            self,
            url: str,
            headers: dict[str, str],
            payload: dict[str, Any],
        ) -> Iterator[Any]:
            with httpx.Client(timeout=self._owner.timeout, follow_redirects=False) as client:
                with client.stream("POST", url, headers=headers, json=payload) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            break
                        try:
                            decoded = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        yield _ns(decoded)

    class _Chat:
        def __init__(self, owner: "OpenAI"):
            self.completions = _Completions(owner)

    class OpenAI:
        """Subset of the official client used by Nexus.

        The class intentionally mirrors the public attributes used by the
        runtime and tests: ``base_url``, ``timeout``, and
        ``chat.completions.create``.
        """

        def __init__(
            self,
            *,
            base_url: str,
            api_key: str,
            timeout: float = 60.0,
            max_retries: int = 0,
            **_kwargs: Any,
        ):
            if not api_key:
                raise ValueError("An API key is required for hosted inference")
            self.base_url = base_url.rstrip("/") + "/"
            self.api_key = api_key
            self.timeout = timeout
            self.max_retries = max_retries
            self.chat = _Chat(self)

        def close(self) -> None:
            """Compatibility no-op; fallback HTTPX clients are request-scoped."""

        def __enter__(self) -> "OpenAI":
            return self

        def __exit__(self, _exc_type, _exc, _tb) -> None:
            self.close()
