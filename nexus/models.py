"""
Canonical Model Registry & Descriptors for LLM Backends.
Supports hosted models, local Nova backends, pricing versions, aliases, and privacy policies.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from typing import Any


class PrivacyClass(str, Enum):
    LOCAL_ONLY = "LOCAL_ONLY"
    PRIVATE_INFRASTRUCTURE = "PRIVATE_INFRASTRUCTURE"
    APPROVED_CLOUD = "APPROVED_CLOUD"
    ANY_ALLOWED_PROVIDER = "ANY_ALLOWED_PROVIDER"


class ModelTier(str, Enum):
    LOCAL = "LOCAL"
    AFFORDABLE = "AFFORDABLE"
    STRONG = "STRONG"
    FRONTIER = "FRONTIER"


@dataclass
class ModelDescriptor:
    model_id: str
    provider_id: str
    display_name: str
    model_family: str
    local: bool
    enabled: bool = True
    context_window: int | None = 128000
    max_output_tokens: int | None = 16384
    supports_tools: bool = True
    supports_parallel_tools: bool = False
    supports_structured_output: bool = True
    supports_streaming: bool = True
    supports_images: bool = False
    input_cost: float | Decimal | None = None       # USD per 1,000,000 input tokens
    output_cost: float | Decimal | None = None      # USD per 1,000,000 output tokens
    cached_input_cost: float | Decimal | None = None # USD per 1,000,000 cached tokens
    privacy_class: PrivacyClass = PrivacyClass.APPROVED_CLOUD
    tier: ModelTier = ModelTier.AFFORDABLE
    data_retention_class: str | None = None
    capability_profile_id: str | None = None
    model_version: str = "v1"
    pricing_version: str = "2026-08"
    backend: str = "hosted"
    ollama_model: str | None = None
    description: str = ""
    category: str = "general"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.model_id,
            "provider_id": self.provider_id,
            "name": self.display_name,
            "family": self.model_family,
            "local": self.local,
            "enabled": self.enabled,
            "context": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "supports_tools": self.supports_tools,
            "supports_parallel_tools": self.supports_parallel_tools,
            "supports_structured_output": self.supports_structured_output,
            "supports_streaming": self.supports_streaming,
            "supports_images": self.supports_images,
            "input_cost": float(self.input_cost) if self.input_cost is not None else None,
            "output_cost": float(self.output_cost) if self.output_cost is not None else None,
            "cached_input_cost": float(self.cached_input_cost) if self.cached_input_cost is not None else None,
            "privacy_class": self.privacy_class.value,
            "tier": self.tier.value,
            "data_retention_class": self.data_retention_class,
            "capability_profile_id": self.capability_profile_id or self.model_id,
            "model_version": self.model_version,
            "pricing_version": self.pricing_version,
            "backend": self.backend,
            "ollama_model": self.ollama_model,
            "description": self.description,
            "category": self.category,
        }


DEFAULT_DESCRIPTORS: list[ModelDescriptor] = [
    ModelDescriptor(
        model_id="local/nova3b",
        provider_id="nova",
        display_name="Nova Codex (Nova 3B v11)",
        model_family="nova",
        local=True,
        context_window=32768,
        input_cost=0.0,
        output_cost=0.0,
        cached_input_cost=0.0,
        privacy_class=PrivacyClass.LOCAL_ONLY,
        tier=ModelTier.LOCAL,
        backend="nova",
        ollama_model="nova_codex",
        category="local",
        description="Nova Codex (Nova 3B v11) — handles well-specified subtasks fast and free, locally.",
    ),
    ModelDescriptor(
        model_id="deepseek-ai/deepseek-v4-flash",
        provider_id="hosted",
        display_name="DeepSeek V4 Flash",
        model_family="deepseek",
        local=False,
        context_window=1_000_000,
        input_cost=0.14,
        output_cost=0.28,
        cached_input_cost=0.07,
        privacy_class=PrivacyClass.APPROVED_CLOUD,
        tier=ModelTier.AFFORDABLE,
        category="coding",
        description="Fast DeepSeek MoE for code generation and tool use",
    ),
    ModelDescriptor(
        model_id="meta/llama-3.3-70b-instruct",
        provider_id="hosted",
        display_name="Llama 3.3 70B",
        model_family="llama",
        local=False,
        context_window=128000,
        input_cost=0.35,
        output_cost=0.40,
        cached_input_cost=0.18,
        privacy_class=PrivacyClass.APPROVED_CLOUD,
        tier=ModelTier.AFFORDABLE,
        category="coding",
        description="Meta's flagship 70B — super fast, elite tool calling & agentic coding",
    ),
    ModelDescriptor(
        model_id="qwen/qwen3.5-397b-a17b",
        provider_id="hosted",
        display_name="Qwen 3.5 (397B)",
        model_family="qwen",
        local=False,
        context_window=128000,
        input_cost=0.30,
        output_cost=0.60,
        cached_input_cost=0.15,
        privacy_class=PrivacyClass.APPROVED_CLOUD,
        tier=ModelTier.AFFORDABLE,
        category="coding",
        description="Alibaba's 397B flagship MoE — specialized in software engineering",
    ),
    ModelDescriptor(
        model_id="z-ai/glm-5.2",
        provider_id="hosted",
        display_name="GLM 5.2",
        model_family="glm",
        local=False,
        context_window=1_000_000,
        input_cost=0.50,
        output_cost=1.00,
        cached_input_cost=0.25,
        privacy_class=PrivacyClass.APPROVED_CLOUD,
        tier=ModelTier.STRONG,
        category="reasoning",
        description="Flagship agentic, coding, and long-horizon reasoning model",
    ),
    ModelDescriptor(
        model_id="deepseek-ai/deepseek-v4-pro",
        provider_id="hosted",
        display_name="DeepSeek V4 Pro",
        model_family="deepseek",
        local=False,
        context_window=1_000_000,
        input_cost=0.55,
        output_cost=2.19,
        cached_input_cost=0.14,
        privacy_class=PrivacyClass.APPROVED_CLOUD,
        tier=ModelTier.STRONG,
        category="reasoning",
        description="MoE flagship for long-context reasoning, coding, and agents",
    ),
    ModelDescriptor(
        model_id="moonshotai/kimi-k2.6",
        provider_id="hosted",
        display_name="Kimi K2.6",
        model_family="kimi",
        local=False,
        context_window=262144,
        input_cost=0.60,
        output_cost=2.50,
        cached_input_cost=0.30,
        privacy_class=PrivacyClass.APPROVED_CLOUD,
        tier=ModelTier.STRONG,
        category="coding",
        description="Multimodal MoE optimized for long-horizon coding and tool use",
    ),
    ModelDescriptor(
        model_id="minimaxai/minimax-m3",
        provider_id="hosted",
        display_name="MiniMax M3",
        model_family="minimax",
        local=False,
        context_window=1_000_000,
        input_cost=0.50,
        output_cost=1.50,
        cached_input_cost=0.25,
        privacy_class=PrivacyClass.APPROVED_CLOUD,
        tier=ModelTier.STRONG,
        category="general",
        description="Multimodal MoE for reasoning, coding, and tool calling",
    ),
    ModelDescriptor(
        model_id="nvidia/llama-3.3-nemotron-super-49b-v1.5",
        provider_id="hosted",
        display_name="Nemotron Super 49B",
        model_family="nemotron",
        local=False,
        context_window=128000,
        input_cost=0.60,
        output_cost=1.20,
        cached_input_cost=0.30,
        privacy_class=PrivacyClass.APPROVED_CLOUD,
        tier=ModelTier.STRONG,
        category="reasoning",
        description="NVIDIA tuned Llama 3.3 for reasoning & complex tool execution",
    ),
    ModelDescriptor(
        model_id="meta/llama-3.1-70b-instruct",
        provider_id="hosted",
        display_name="Llama 3.1 70B",
        model_family="llama",
        local=False,
        context_window=128000,
        input_cost=0.35,
        output_cost=0.40,
        cached_input_cost=0.18,
        privacy_class=PrivacyClass.APPROVED_CLOUD,
        tier=ModelTier.AFFORDABLE,
        category="general",
        description="Meta's highly capable 70B instruction-tuned model",
    ),
    ModelDescriptor(
        model_id="custom",
        provider_id="custom",
        display_name="Custom Hosted Model",
        model_family="custom",
        local=False,
        context_window=200000,
        input_cost=1.00,
        output_cost=3.00,
        cached_input_cost=0.50,
        privacy_class=PrivacyClass.ANY_ALLOWED_PROVIDER,
        tier=ModelTier.FRONTIER,
        backend="custom",
        category="custom",
        description="Custom hosted model via NEXUS_MODEL_ID / OpenAI endpoint",
    ),
]


ALIASES: dict[str, str] = {
    "auto": "glm-5.2",
    "llama": "llama-3.3-70b",
    "llama3": "llama-3.3-70b",
    "llama-3.3": "llama-3.3-70b",
    "deepseek": "deepseek-v4",
    "deepseek-v4": "deepseek-v4",
    "deepseek-v4-pro": "deepseek-v4",
    "deepseek-v4-flash": "deepseek-flash",
    "deepseek-pro": "deepseek-v4",
    "v4": "deepseek-v4",
    "v4-pro": "deepseek-v4",
    "ds": "deepseek-v4",
    "flash": "deepseek-flash",
    "glm": "glm-5.2",
    "glm-5.2": "glm-5.2",
    "kimi": "kimi-k2.6",
    "minimax": "minimax-m3",
    "qwen": "qwen3.5",
    "qwen-coder": "qwen3.5",
    "nemotron": "nemotron-super",
    "code": "qwen3.5",
    "nova": "nova3b",
    "nova-3b": "nova3b",
    "nova3b": "nova3b",
    "nova345": "nova3b",
    "nova3b11": "nova3b",
    "nova_codex": "nova3b",
    "local": "nova3b",
    "custom": "custom",
    "frontier": "custom",
    "openrouter": "custom",
}

DEFAULT_MODEL = "glm-5.2"


class ModelRegistry:
    """Thread-safe authoritative model registry for Nexus CLI."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._descriptors: dict[str, ModelDescriptor] = {}
        self._key_map: dict[str, str] = {}
        self._aliases: dict[str, str] = dict(ALIASES)
        self._initialize_defaults()

    def _initialize_defaults(self) -> None:
        key_mapping = {
            "local/nova3b": "nova3b",
            "deepseek-ai/deepseek-v4-flash": "deepseek-flash",
            "meta/llama-3.3-70b-instruct": "llama-3.3-70b",
            "qwen/qwen3.5-397b-a17b": "qwen3.5",
            "z-ai/glm-5.2": "glm-5.2",
            "deepseek-ai/deepseek-v4-pro": "deepseek-v4",
            "moonshotai/kimi-k2.6": "kimi-k2.6",
            "minimaxai/minimax-m3": "minimax-m3",
            "nvidia/llama-3.3-nemotron-super-49b-v1.5": "nemotron-super",
            "meta/llama-3.1-70b-instruct": "llama-3.1-70b",
            "custom": "custom",
        }
        for desc in DEFAULT_DESCRIPTORS:
            key = key_mapping.get(desc.model_id, desc.model_id)
            self.register_descriptor(key, desc)

    def register_descriptor(self, key: str, descriptor: ModelDescriptor) -> None:
        with self._lock:
            key_clean = key.lower().strip()
            if not key_clean:
                raise ValueError("Model key cannot be empty")
            if key_clean in self._descriptors:
                existing = self._descriptors[key_clean]
                if existing.model_id == descriptor.model_id and existing.model_version != descriptor.model_version:
                    descriptor.capability_profile_id = f"{descriptor.model_id}:{descriptor.model_version}"
            self._descriptors[key_clean] = descriptor
            self._key_map[descriptor.model_id.lower()] = key_clean

    def resolve_key(self, name: str) -> str | None:
        if not name:
            return DEFAULT_MODEL
        name_clean = name.lower().strip()

        with self._lock:
            if name_clean in self._aliases:
                key = self._aliases[name_clean]
                if key in self._descriptors:
                    return key
            if name_clean in self._descriptors:
                return name_clean
            if name_clean in self._key_map:
                return self._key_map[name_clean]

            for k, desc in self._descriptors.items():
                if name_clean in k or name_clean in desc.display_name.lower() or name_clean in desc.model_id.lower():
                    return k
        return None

    def get_descriptor(self, name: str) -> ModelDescriptor | None:
        key = self.resolve_key(name)
        if not key:
            return None
        with self._lock:
            desc = self._descriptors.get(key)
            if not desc:
                return None
            if key == "custom":
                custom_id = os.environ.get("NEXUS_MODEL_ID", "").strip()
                if custom_id:
                    return ModelDescriptor(
                        model_id=custom_id,
                        provider_id=desc.provider_id,
                        display_name=f"Custom Hosted Model ({custom_id})",
                        model_family=desc.model_family,
                        local=desc.local,
                        enabled=desc.enabled,
                        context_window=desc.context_window,
                        max_output_tokens=desc.max_output_tokens,
                        supports_tools=desc.supports_tools,
                        supports_parallel_tools=desc.supports_parallel_tools,
                        supports_structured_output=desc.supports_structured_output,
                        supports_streaming=desc.supports_streaming,
                        supports_images=desc.supports_images,
                        input_cost=desc.input_cost,
                        output_cost=desc.output_cost,
                        cached_input_cost=desc.cached_input_cost,
                        privacy_class=desc.privacy_class,
                        tier=desc.tier,
                        backend=desc.backend,
                        category=desc.category,
                        description=desc.description,
                    )
            return desc

    def list_all(self) -> list[ModelDescriptor]:
        with self._lock:
            return sorted(
                list(self._descriptors.values()),
                key=lambda d: (d.category, d.display_name),
            )

    def to_legacy_models_dict(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            res = {}
            for key, desc in self._descriptors.items():
                res[key] = desc.to_dict()
            return res


# Global singleton instance
model_registry = ModelRegistry()

# Backwards compatibility dictionary
MODELS = model_registry.to_legacy_models_dict()


def resolve_model_key(name: str) -> str | None:
    """Resolve a model name or alias to its canonical registry key."""
    return model_registry.resolve_key(name)


def resolve_model(name: str) -> dict | None:
    """Resolve a model name or alias to an isolated config dictionary."""
    desc = model_registry.get_descriptor(name)
    return desc.to_dict() if desc else None


def list_models() -> list[dict]:
    """Return all models sorted by category."""
    results = []
    for key, cfg in sorted(MODELS.items(), key=lambda x: (x[1]["category"], x[0])):
        resolved = resolve_model(key) or dict(cfg)
        results.append({"key": key, **resolved})
    return results
