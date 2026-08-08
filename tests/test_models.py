"""
Tests for the models module — model registry, aliases, and resolution.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from nexus.models import (
    ALIASES,
    DEFAULT_MODEL,
    MODELS,
    list_models,
    resolve_model,
    resolve_model_key,
)


def test_models_registry():
    """All models should have required fields."""
    required_fields = ["id", "name", "category", "context", "description", "supports_tools"]
    for key, cfg in MODELS.items():
        for field in required_fields:
            assert field in cfg, f"Model '{key}' missing field '{field}'"
        assert isinstance(cfg["context"], int), f"Model '{key}' context should be int"
        assert isinstance(cfg["supports_tools"], bool), (
            f"Model '{key}' supports_tools should be bool"
        )


def test_default_model_exists():
    """Default model should be in the registry."""
    assert DEFAULT_MODEL in MODELS


def test_resolve_model_exact():
    """Resolving an exact key should return the config."""
    cfg = resolve_model("deepseek-v4")
    assert cfg is not None
    assert cfg["name"] == "DeepSeek V4 Pro"


def test_resolve_model_alias():
    """Resolving an alias should return the correct config."""
    cfg = resolve_model("kimi")
    assert cfg is not None
    assert cfg["name"] == "Kimi K2.6"

    cfg = resolve_model("ds")
    assert cfg is not None
    assert cfg["name"] == "DeepSeek V4 Pro"

    cfg = resolve_model("nova")
    assert cfg is not None
    assert cfg["name"] == "Nova Codex (Nova 3B v11)"
    assert cfg["backend"] == "nova"
    assert resolve_model_key("nova-3b") == "nova3b"
    assert resolve_model_key("nova345") == "nova3b"
    assert resolve_model_key("nova3b11") == "nova3b"
    assert resolve_model_key("nova_codex") == "nova3b"
    assert cfg["ollama_model"] == "nova_codex"


def test_resolve_model_unknown():
    """Resolving an unknown model should return None."""
    cfg = resolve_model("nonexistent-model-xyz")
    assert cfg is None


def test_resolve_model_case_insensitive():
    """Model resolution should be case-insensitive."""
    cfg = resolve_model("KIMI")
    assert cfg is not None
    assert cfg["name"] == "Kimi K2.6"


def test_list_models():
    """list_models should return all models sorted by category."""
    models = list_models()
    assert len(models) == len(MODELS)
    # Should be sorted by category
    categories = [m["category"] for m in models]
    assert categories == sorted(categories)


def test_aliases_point_to_valid_models():
    """Every alias should resolve to a valid model."""
    for alias, target in ALIASES.items():
        assert target in MODELS, f"Alias '{alias}' points to unknown model '{target}'"


def test_tool_support_flags():
    """Models with supports_tools=True should be usable as coding agents."""
    tool_models = [k for k, v in MODELS.items() if v["supports_tools"]]
    assert len(tool_models) >= 8, "Should have at least 8 tool-capable models"
