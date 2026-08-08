"""Actionable backend capability checks shared by CLI, doctor, and benchmarks."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse


@dataclass(frozen=True)
class BackendProbe:
    ready: bool
    backend: str
    code: str
    detail: str
    remediation: tuple[str, ...] = ()
    metadata: dict[str, Any] | None = None

    def format(self) -> str:
        lines = [self.detail]
        if self.remediation:
            lines.append("Recommended action:")
            lines.extend(f"  {line}" for line in self.remediation)
        return "\n".join(lines)


def ollama_base_url() -> str:
    value = (
        os.environ.get("NEXUS_OLLAMA_URL")
        or os.environ.get("OLLAMA_HOST")
        or "http://127.0.0.1:11434"
    )
    if not value.startswith(("http://", "https://")):
        value = f"http://{value}"
    return value.rstrip("/")


@lru_cache(maxsize=32)
def _probe_ollama_cached(model: str, base_url: str, cache_bucket: int) -> BackendProbe:
    del cache_bucket
    request = urllib.request.Request(
        f"{base_url}/api/tags",
        headers={"Accept": "application/json", "User-Agent": "NexusAI/3.1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=0.75) as response:
            payload = json.loads(response.read(2_000_000).decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return BackendProbe(
            ready=False,
            backend="ollama",
            code="endpoint_unreachable",
            detail=f"Ollama is not reachable at {base_url}: {exc}",
            remediation=(
                "Start Ollama with `ollama serve`.",
                "Run `ollama list` to inspect installed models.",
            ),
            metadata={"base_url": base_url, "model": model},
        )

    installed = {
        str(item.get("name", "")) for item in payload.get("models", []) if isinstance(item, dict)
    }
    normalized = {name.split(":", 1)[0] for name in installed}
    requested = (model or "nova_codex").split(":", 1)[0]
    if model and model not in installed and requested not in normalized:
        return BackendProbe(
            ready=False,
            backend="ollama",
            code="model_missing",
            detail=f"Ollama is running, but model '{model}' is not installed.",
            remediation=(
                f'Install or create the model, then verify with `ollama run {model} "hello"`.',
            ),
            metadata={"base_url": base_url, "model": model, "installed": sorted(installed)},
        )
    return BackendProbe(
        ready=True,
        backend="ollama",
        code="ready",
        detail=f"Ollama model '{model}' is available at {base_url}.",
        metadata={"base_url": base_url, "model": model},
    )


def probe_ollama(model: str = "nova_codex", *, use_cache: bool = True) -> BackendProbe:
    bucket = int(time.monotonic() // 5) if use_cache else time.monotonic_ns()
    return _probe_ollama_cached(model, ollama_base_url(), bucket)


def configured_hosted_credentials() -> list[str]:
    names = (
        "NEXUS_OPENAI_API_KEY",
        "NVIDIA_API_KEY",
        "GROQ_API_KEY",
        "OPENROUTER_API_KEY",
    )
    return [name for name in names if os.environ.get(name)]


def probe_hosted() -> BackendProbe:
    credentials = configured_hosted_credentials()
    custom_url = os.environ.get("NEXUS_OPENAI_BASE_URL", "").strip()
    if custom_url:
        parsed = urlparse(custom_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return BackendProbe(
                ready=False,
                backend="hosted",
                code="custom_url_invalid",
                detail="NEXUS_OPENAI_BASE_URL must be an absolute HTTP(S) URL.",
                remediation=("Use a URL such as https://provider.example/v1.",),
            )
    if custom_url and not os.environ.get("NEXUS_OPENAI_API_KEY"):
        return BackendProbe(
            ready=False,
            backend="hosted",
            code="custom_key_missing",
            detail="NEXUS_OPENAI_BASE_URL is configured, but NEXUS_OPENAI_API_KEY is missing.",
            remediation=("Set NEXUS_OPENAI_API_KEY for the custom endpoint.",),
        )
    if not credentials:
        return BackendProbe(
            ready=False,
            backend="hosted",
            code="credentials_missing",
            detail="No hosted-provider credential is configured.",
            remediation=(
                "Set NVIDIA_API_KEY, GROQ_API_KEY, or OPENROUTER_API_KEY.",
                "For a custom OpenAI-compatible endpoint, set NEXUS_OPENAI_BASE_URL and NEXUS_OPENAI_API_KEY.",
            ),
        )
    return BackendProbe(
        ready=True,
        backend="hosted",
        code="credentials_present",
        detail="Hosted credentials are configured via " + ", ".join(credentials) + ".",
        metadata={"credentials": credentials, "custom_base_url": custom_url or None},
    )


def probe_model(model_cfg: dict[str, Any], *, model_name: str = "") -> BackendProbe:
    if model_cfg.get("backend") == "nova":
        return probe_ollama(model_cfg.get("ollama_model", "nova_codex"), use_cache=False)
    if model_cfg.get("backend") == "custom" and not model_cfg.get("id"):
        return BackendProbe(
            ready=False,
            backend="hosted",
            code="model_id_missing",
            detail=f"Custom model '{model_name or 'custom'}' requires a provider model ID.",
            remediation=("Pass --model-id or set NEXUS_MODEL_ID.",),
        )
    return probe_hosted()
