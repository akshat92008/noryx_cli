"""Multi-provider hosted inference client for the NexusAI runtime.

Supports multi-key NVIDIA rotation and a compatible Groq fallback.
"""

import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

from nexus.openai_compat import OpenAI

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Hosted inference can legitimately take more than a few seconds before the
# first token. Keep the defaults conservative while allowing operators to tune
# them for their environment.
DEFAULT_NVIDIA_TIMEOUT = float(os.environ.get("NEXUS_NVIDIA_TIMEOUT", "60.0"))
DEFAULT_GROQ_TIMEOUT = float(os.environ.get("NEXUS_GROQ_TIMEOUT", "60.0"))

# Groq model mappings for ultimate fallback (must support tool calling if used)
GROQ_MODEL_MAP = {
    "meta/llama-3.3-70b-instruct": "openai/gpt-oss-120b",
    "deepseek-ai/deepseek-v4-pro": "openai/gpt-oss-120b",
    "deepseek-ai/deepseek-v4-flash": "openai/gpt-oss-120b",
    "z-ai/glm-5.2": "openai/gpt-oss-120b",
    "moonshotai/kimi-k2.6": "openai/gpt-oss-120b",
    "minimaxai/minimax-m3": "openai/gpt-oss-120b",
    "qwen/qwen3.5-397b-a17b": "openai/gpt-oss-120b",
    "nvidia/llama-3.3-nemotron-super-49b-v1.5": "openai/gpt-oss-120b",
    "meta/llama-3.1-70b-instruct": "openai/gpt-oss-120b",
}
DEFAULT_GROQ_MODEL = "openai/gpt-oss-120b"


def _response_usage(value: Any) -> dict[str, int]:
    usage = getattr(value, "usage", None)
    if usage is None:
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    result = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        item = usage.get(key, 0) if isinstance(usage, dict) else getattr(usage, key, 0)
        result[key] = int(item or 0)
    if not result["total_tokens"]:
        result["total_tokens"] = result["prompt_tokens"] + result["completion_tokens"]
    return result


class _ObservedStream:
    """Finalize physical-attempt telemetry when a provider stream is consumed."""

    def __init__(self, stream: Any, finish: Callable[[str, dict[str, Any]], None]):
        self._stream = stream
        self._finish = finish
        self._finished = False
        self._transport_closed = False

    def __iter__(self):
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        request_id = ""
        try:
            for chunk in self._stream:
                request_id = request_id or str(getattr(chunk, "id", "") or "")
                current = _response_usage(chunk)
                for key in usage:
                    usage[key] = max(usage[key], current[key])
                yield chunk
        except BaseException as exc:
            self._complete(
                "failed",
                {
                    "error": str(exc) or type(exc).__name__,
                    "request_id": request_id,
                    "usage": usage,
                },
            )
            raise
        else:
            self._complete("verified", {"request_id": request_id, "usage": usage})
        finally:
            self._close_transport()
            if not self._finished:
                self._complete("cancelled", {"request_id": request_id, "usage": usage})

    def _complete(self, status: str, metadata: dict[str, Any]) -> None:
        if not self._finished:
            self._finished = True
            self._finish(status, metadata)

    def _close_transport(self) -> None:
        if self._transport_closed:
            return
        self._transport_closed = True
        closer = getattr(self._stream, "close", None)
        if callable(closer):
            try:
                closer()
            except (OSError, RuntimeError, ValueError):
                pass

    def close(self) -> None:
        self._close_transport()
        self._complete("cancelled", {"request_id": "", "usage": {}})

    def __enter__(self) -> "_ObservedStream":
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()


def _load_env_file():
    """Load local Nexus environment files without overriding process values."""
    cwd = os.getcwd()
    checkout = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    possible_paths = [
        os.path.join(cwd, ".env"),
        os.path.join(checkout, ".env"),
        os.path.expanduser("~/.config/nexus/.env"),
        os.path.expanduser("~/.nexusai/.env"),
    ]
    for p in possible_paths:
        if os.path.exists(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line and not line.startswith("#") and "=" in line:
                            k, v = line.split("=", 1)
                            k, v = k.strip(), v.strip().strip("'\"")
                            if p == os.path.join(cwd, ".env"):
                                if k.startswith(("NEXUS_", "OPENAI_", "ANTHROPIC_", "GROQ_", "NVIDIA_", "OPENROUTER_")) or k in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE"}:
                                    continue
                            # Explicit process environment wins over repository .env.
                            # This is required for CLI flags, CI, and isolated tests.
                            os.environ.setdefault(k, v)
            except (OSError, TypeError, ValueError):
                pass


class RoundRobinKeyPool:
    """
    Thread-safe Round-Robin Pool Manager for API keys.
    Handles active key cycling, rate-limit cooldown tracking, and automatic key failover.
    """

    def __init__(self, keys: list[str], cooldown_seconds: float = 60.0):
        self.keys = list(dict.fromkeys([k for k in keys if k]))
        self.cooldown_seconds = cooldown_seconds
        self.current_idx = 0
        self.cooldowns: dict[str, float] = {}
        self._lock = threading.RLock()

    def get_next_key(self) -> str | None:
        """Select and return the next active round-robin key, skipping cooling-down keys."""
        wait_time = 0
        earliest_key = None
        with self._lock:
            if not self.keys:
                return None
            now = time.time()
            start_idx = self.current_idx
            for offset in range(len(self.keys)):
                idx = (start_idx + offset) % len(self.keys)
                key = self.keys[idx]
                if key not in self.cooldowns or now >= self.cooldowns[key]:
                    self.current_idx = (idx + 1) % len(self.keys)
                    return key
            earliest_key = min(self.keys, key=lambda k: self.cooldowns.get(k, 0))
            wait_time = max(0, self.cooldowns[earliest_key] - now)
            self.current_idx = (self.keys.index(earliest_key) + 1) % len(self.keys)

        if wait_time > 0:
            time.sleep(wait_time)
        return earliest_key

    def mark_cooldown(self, key: str, duration: float | None = None):
        """Mark a key as temporarily cooling down (e.g. on HTTP 429 rate limit)."""
        sec = duration if duration is not None else self.cooldown_seconds
        with self._lock:
            self.cooldowns[key] = time.time() + sec

    def is_cooldown(self, key: str) -> bool:
        """Check if a key is currently in cooldown."""
        with self._lock:
            return key in self.cooldowns and time.time() < self.cooldowns[key]

    def reset_cooldowns(self):
        """Reset all rate-limit cooldowns."""
        with self._lock:
            self.cooldowns.clear()


class NvidiaClient:
    """
    Multi-Provider & Multi-Key Resilient Client for NexusAI.
    Handles automatic round-robin key rotation across NVIDIA API keys,
    NVIDIA model fallbacks, and seamless Groq API ultimate failover.
    """

    def __init__(
        self,
        api_key: str | None = None,
        timeout: float = DEFAULT_NVIDIA_TIMEOUT,
        *,
        attempt_controller: Any = None,
        attempt_observer: Callable[[dict[str, Any]], None] | None = None,
    ):
        _load_env_file()
        self.primary_key = api_key or os.environ.get("NVIDIA_API_KEY", "")
        self.api_key = self.primary_key
        self.timeout = timeout
        self._attempt_controller = attempt_controller
        self._attempt_observer = attempt_observer
        self._client_lock = threading.RLock()
        self._client_cache: dict[tuple[str, str, float], Any] = {}
        self._closed = False
        self.custom_base_url = os.environ.get("NEXUS_OPENAI_BASE_URL", "").strip().rstrip("/")
        self.custom_api_key = os.environ.get("NEXUS_OPENAI_API_KEY", "").strip()
        self.custom_model = os.environ.get("NEXUS_MODEL_ID", "").strip()

        # Collect all NVIDIA keys
        self.nvidia_keys = [self.primary_key] if self.primary_key else []
        for k in sorted(os.environ.keys()):
            if (
                k.startswith("NVIDIA_FALLBACK_API_KEY") or k.startswith("NVIDIA_API_KEY_")
            ) and os.environ[k]:
                self.nvidia_keys.append(os.environ[k])

        # Deduplicate keys while preserving insertion order
        self.nvidia_keys = list(dict.fromkeys([k for k in self.nvidia_keys if k]))
        self.current_key_idx = 0
        self.key_cooldowns = {}

        # Round-Robin Key Pools
        self.nvidia_pool = RoundRobinKeyPool(self.nvidia_keys, cooldown_seconds=60.0)

        # Groq key fallback pool
        self.groq_keys = []
        if os.environ.get("GROQ_API_KEY"):
            self.groq_keys.append(os.environ["GROQ_API_KEY"])
        for k in sorted(os.environ.keys()):
            if (
                k.startswith("GROQ_API_KEY_") or k.startswith("GROQ_FALLBACK_API_KEY")
            ) and os.environ[k]:
                self.groq_keys.append(os.environ[k])
        self.groq_keys = list(dict.fromkeys([k for k in self.groq_keys if k]))
        self.groq_key = self.groq_keys[0] if self.groq_keys else ""
        self.groq_pool = RoundRobinKeyPool(self.groq_keys, cooldown_seconds=60.0)

        # Pre-instantiate the highest-priority configured client.
        if self.custom_base_url and self.custom_api_key:
            self.client = self._get_client(
                self.custom_base_url,
                self.custom_api_key,
                self.timeout,
            )
        elif self.nvidia_keys:
            self.client = self._get_client(NVIDIA_BASE_URL, self.nvidia_keys[0], self.timeout)
        elif self.groq_keys:
            self.client = self._get_client(
                GROQ_BASE_URL,
                self.groq_keys[0],
                DEFAULT_GROQ_TIMEOUT,
            )
        elif os.environ.get("OPENROUTER_API_KEY"):
            self.client = self._get_client(
                OPENROUTER_BASE_URL,
                os.environ["OPENROUTER_API_KEY"],
                DEFAULT_GROQ_TIMEOUT,
            )
        else:
            raise ValueError(
                "No valid API key found for a custom OpenAI-compatible endpoint, NVIDIA, Groq, or OpenRouter."
            )

    @property
    def all_keys(self) -> list[str]:
        """Legacy compatibility property for existing codebase."""
        return self.nvidia_keys

    @property
    def attempt_telemetry_enabled(self) -> bool:
        return self._attempt_observer is not None

    def _get_client(self, base_url: str, api_key: str, timeout: float) -> OpenAI:
        """Return one owned OpenAI-compatible client per endpoint/key tuple.

        The previous implementation allocated fresh SDK clients on every
        failover attempt.  Official OpenAI clients own an HTTP transport, so
        repeatedly abandoning them makes process shutdown dependent on garbage
        collection.  Cache and explicitly close them instead.
        """

        if self._closed:
            raise RuntimeError("Hosted provider client is closed")
        key = (base_url.rstrip("/"), api_key, float(timeout))
        with self._client_lock:
            cached = self._client_cache.get(key)
            if cached is not None:
                return cached
            client = OpenAI(
                base_url=base_url,
                api_key=api_key,
                timeout=timeout,
                max_retries=2,
            )
            self._client_cache[key] = client
            return client

    def close(self) -> None:
        """Close every owned HTTP transport exactly once."""

        with self._client_lock:
            if self._closed:
                return
            self._closed = True
            clients = list(dict.fromkeys(map(id, self._client_cache.values())))
            by_id = {id(value): value for value in self._client_cache.values()}
            self._client_cache.clear()
        errors: list[BaseException] = []
        for client_id in clients:
            client = by_id[client_id]
            closer = getattr(client, "close", None)
            if not callable(closer):
                continue
            try:
                closer()
            except BaseException as exc:  # cleanup must attempt every transport
                errors.append(exc)
        if errors:
            raise RuntimeError(
                "Failed to close hosted provider transport(s): "
                + "; ".join(f"{type(exc).__name__}: {exc}" for exc in errors)
            )

    def __enter__(self) -> "NvidiaClient":
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.close()

    def _provider_request(
        self,
        *,
        provider: str,
        model: str,
        request: Callable[[], Any],
        streaming: bool,
        fallback_from: str = "",
        attempt_number: int = 1,
    ) -> Any:
        physical_attempt = 1
        if self._attempt_controller is not None:
            physical_attempt = self._attempt_controller.before_provider_attempt(provider, model)
        started = datetime.now(timezone.utc)

        def finish(status: str, metadata: dict[str, Any]) -> None:
            if self._attempt_observer is None:
                return
            completed = datetime.now(timezone.utc)
            self._attempt_observer(
                {
                    "provider": provider,
                    "model": model,
                    "status": status,
                    "attempt": max(1, int(attempt_number)),
                    "physical_attempt": physical_attempt,
                    "fallback_from": fallback_from,
                    "started_at": started.isoformat(),
                    "completed_at": completed.isoformat(),
                    "duration_ms": int((completed - started).total_seconds() * 1000),
                    "request_id": metadata.get("request_id", ""),
                    "usage": metadata.get("usage", {}),
                    "error": metadata.get("error", ""),
                }
            )

        try:
            response = request()
        except (OSError, ValueError) as exc:
            finish("failed", {"error": str(exc)})
            raise
        if streaming:
            return _ObservedStream(response, finish)
        finish(
            "verified",
            {
                "request_id": str(getattr(response, "id", "") or ""),
                "usage": _response_usage(response),
            },
        )
        return response

    def get_next_key(self, provider: str = "nvidia") -> str | None:
        """Get the next active key via Round-Robin selection."""
        if provider.lower() == "groq":
            return self.groq_pool.get_next_key()
        key = self.nvidia_pool.get_next_key()
        if key and key in self.nvidia_keys:
            self.current_key_idx = self.nvidia_keys.index(key)
        return key

    def round_robin_rotate(self) -> str:
        """Explicit Round-Robin rotation to the next key in the pool."""
        next_key = self.get_next_key(provider="nvidia")
        if next_key:
            self.api_key = next_key
            self.client = self._get_nvidia_client(next_key)
        return self.api_key

    def switch_to_fallback(self) -> bool:
        """Switch round-robin to the next available NVIDIA key in the pool."""
        if len(self.nvidia_keys) > 1:
            self.round_robin_rotate()
            return True
        return False

    def _get_groq_client(self, key: str | None = None) -> OpenAI:
        """Get an OpenAI client configured for Groq API."""
        api_key = key or self.groq_key
        return self._get_client(GROQ_BASE_URL, api_key, DEFAULT_GROQ_TIMEOUT)

    def _get_nvidia_client(self, key: str) -> OpenAI:
        """Get an OpenAI client for a specific NVIDIA key."""
        return self._get_client(NVIDIA_BASE_URL, key, self.timeout)

    def resolve_groq_model(self, model_id: str) -> str:
        """Map an NVIDIA model ID to an equivalent Groq model ID."""
        return GROQ_MODEL_MAP.get(model_id, DEFAULT_GROQ_MODEL)

    def chat(
        self,
        model_id: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 16384,
        stream: bool = True,
        parallel_tool_calls: bool | None = None,
        response_format: dict[str, Any] | None = None,
        seed: int | None = None,
        stop: str | list[str] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        top_p: float | None = None,
    ):
        """
        Send a chat completion request with automatic multi-key and multi-provider failover.
        Tries NVIDIA keys, alternative NVIDIA models, Groq, then OpenRouter.
        """
        from nexus.network_policy import network_globally_disabled

        if network_globally_disabled():
            raise RuntimeError(
                "Outbound provider requests are disabled by NEXUS_DISABLE_NETWORK."
            )
        kwargs = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if stream:
            kwargs["stream_options"] = {"include_usage": True}
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
            if parallel_tool_calls is not None:
                kwargs["parallel_tool_calls"] = bool(parallel_tool_calls)
        if response_format is not None:
            kwargs["response_format"] = response_format
        if seed is not None:
            kwargs["seed"] = int(seed)
        if stop is not None:
            kwargs["stop"] = stop
        if top_p is not None:
            kwargs["top_p"] = float(top_p)

        errors = []
        connection_timed_out = False

        # ── Step 0: Explicit custom OpenAI-compatible endpoint ───────────
        if self.custom_base_url and self.custom_api_key:
            try:
                custom_client = self._get_client(
                    self.custom_base_url,
                    self.custom_api_key,
                    self.timeout,
                )
                effective_model = self.custom_model or model_id
                return self._provider_request(
                    provider="custom",
                    model=effective_model,
                    streaming=stream,
                    attempt_number=len(errors) + 1,
                    request=lambda: custom_client.chat.completions.create(
                        model=effective_model,
                        **kwargs,
                    ),
                )
            except (OSError, ValueError) as exc:
                errors.append(f"Custom OpenAI-compatible endpoint: {exc}")

        # ── Step 1: Try NVIDIA keys with requested model (max 3 key attempts for fast failover)
        if self.nvidia_keys:
            start_idx = self.current_key_idx
            now = time.time()
            max_attempts = min(3, len(self.nvidia_keys))
            attempted = 0
            for offset in range(len(self.nvidia_keys)):
                if attempted >= max_attempts:
                    break
                idx = (start_idx + offset) % len(self.nvidia_keys)
                key = self.nvidia_keys[idx]
                if key in self.key_cooldowns and now < self.key_cooldowns[key]:
                    continue  # Skip key currently in rate-limit cooldown
                attempted += 1
                try:
                    client = self._get_nvidia_client(key)
                    resp = self._provider_request(
                        provider="nvidia",
                        model=model_id,
                        streaming=stream,
                        attempt_number=len(errors) + 1,
                        request=lambda client=client, model_id=model_id: (
                            client.chat.completions.create(model=model_id, **kwargs)
                        ),
                    )
                    self.current_key_idx = (idx + 1) % len(self.nvidia_keys)
                    self.api_key = key
                    self.client = client
                    return resp
                except Exception as e:
                    err_str = str(e)
                    errors.append(f"NVIDIA Key {idx + 1} ({model_id}): {err_str}")
                    if any(
                        t in err_str.lower() for t in ("429", "rate limit", "too many requests")
                    ):
                        self.key_cooldowns[key] = time.time() + 60.0
                    if any(
                        t in err_str.lower()
                        for t in ("timeout", "timed out", "connection", "connect", "unreachable")
                    ):
                        connection_timed_out = True
                        break  # Fast exit on host timeout
                    self.switch_to_fallback()

        # ── Step 2: Try NVIDIA fallback models (DeepSeek Flash & Llama 3.3) ──
        if not connection_timed_out:
            fallback_nvidia_models = [
                "deepseek-ai/deepseek-v4-flash",
                "meta/llama-3.3-70b-instruct",
            ]
            now = time.time()
            for fb_model in fallback_nvidia_models:
                if fb_model == model_id:
                    continue
                for idx, key in enumerate(self.nvidia_keys):
                    if key in self.key_cooldowns and now < self.key_cooldowns[key]:
                        continue
                    try:
                        client = self._get_nvidia_client(key)
                        resp = self._provider_request(
                            provider="nvidia",
                            model=fb_model,
                            streaming=stream,
                            fallback_from=model_id,
                            attempt_number=len(errors) + 1,
                            request=lambda client=client, fb_model=fb_model: (
                                client.chat.completions.create(model=fb_model, **kwargs)
                            ),
                        )
                        self.current_key_idx = (idx + 1) % len(self.nvidia_keys)
                        self.api_key = key
                        self.client = client
                        return resp
                    except Exception as e:
                        err_str = str(e)
                        errors.append(f"NVIDIA Fallback Model {fb_model} Key {idx + 1}: {err_str}")
                        if any(
                            t in err_str.lower() for t in ("429", "rate limit", "too many requests")
                        ):
                            self.key_cooldowns[key] = time.time() + 60.0
                        if any(
                            t in err_str.lower()
                            for t in (
                                "timeout",
                                "timed out",
                                "connection",
                                "connect",
                                "unreachable",
                            )
                        ):
                            connection_timed_out = True
                            break
                if connection_timed_out:
                    break

        # ── Step 3: Ultimate Fallback to Groq API (multi-key & multi-model) ──
        if self.groq_keys:
            primary_groq = self.resolve_groq_model(model_id)
            groq_candidates = [
                primary_groq,
                "openai/gpt-oss-120b",
                "openai/gpt-oss-20b",
            ]
            groq_candidates = list(dict.fromkeys(groq_candidates))

            groq_kwargs = dict(kwargs)
            if groq_kwargs.get("max_tokens", 16384) > 32768:
                groq_kwargs["max_tokens"] = 32768

            for g_model in groq_candidates:
                for g_key in self.groq_keys:
                    try:
                        groq_client = self._get_groq_client(g_key)
                        return self._provider_request(
                            provider="groq",
                            model=g_model,
                            streaming=stream,
                            fallback_from=model_id,
                            attempt_number=len(errors) + 1,
                            request=lambda groq_client=groq_client, g_model=g_model: (
                                groq_client.chat.completions.create(model=g_model, **groq_kwargs)
                            ),
                        )
                    except Exception as e:
                        err_str = str(e)
                        errors.append(f"Groq API Fallback ({g_model}): {err_str}")
                        if (
                            "429" in err_str
                            or "rate limit" in err_str.lower()
                            or "413" in err_str
                            or "400" in err_str
                        ):
                            continue  # Try next model or key if rate-limited or invalid model

        # ── Step 4: Fallback to OpenRouter if a key is present ───────────────
        openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
        if openrouter_key:
            try:
                or_client = self._get_client(
                    OPENROUTER_BASE_URL,
                    openrouter_key,
                    DEFAULT_GROQ_TIMEOUT,
                )
                or_kwargs = dict(kwargs)
                if or_kwargs.get("max_tokens", 16384) > 8192:
                    or_kwargs["max_tokens"] = 8192
                openrouter_model = (
                    os.environ.get("NEXUS_OPENROUTER_MODEL", "").strip()
                    or self.custom_model
                    or model_id
                )
                return self._provider_request(
                    provider="openrouter",
                    model=openrouter_model,
                    streaming=stream,
                    fallback_from=model_id,
                    attempt_number=len(errors) + 1,
                    request=lambda: or_client.chat.completions.create(
                        model=openrouter_model, **or_kwargs
                    ),
                )
            except Exception as e:
                errors.append(f"OpenRouter Fallback: {e}")

        # If all providers and keys failed, raise a clear exception with details
        summary_err = " | ".join(errors[-3:]) if errors else "All API attempts failed"
        raise RuntimeError(f"Nexus AI Provider Failover Error: {summary_err}")

    def chat_sync(
        self,
        model_id: str,
        messages: list[dict],
        tools: list[dict] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 16384,
        parallel_tool_calls: bool | None = None,
        response_format: dict[str, Any] | None = None,
        seed: int | None = None,
        stop: str | list[str] | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        top_p: float | None = None,
    ):
        """Non-streaming chat completion with full multi-provider failover."""
        return self.chat(
            model_id=model_id,
            messages=messages,
            tools=tools,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            parallel_tool_calls=parallel_tool_calls,
            response_format=response_format,
            seed=seed,
            stop=stop,
            tool_choice=tool_choice,
            top_p=top_p,
        )
