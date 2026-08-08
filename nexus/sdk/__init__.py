"""Versioned Nexus extension SDK.

The SDK exports the same protocols used by the production extension loader;
there is no separate, disconnected execution model.
"""

from nexus.extensions import (
    EXTENSION_API_VERSION,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    NexusTool,
    PolicyProvider,
    ToolContext,
)
from nexus.sdk.hooks import HookContext, HookEvent, HookPlugin
from nexus.sdk.policy import ExecutionPolicy
from nexus.sdk.tools import BaseTool, FunctionTool, ToolRegistry

__all__ = [
    "EXTENSION_API_VERSION",
    "ModelProvider",
    "ModelRequest",
    "ModelResponse",
    "NexusTool",
    "PolicyProvider",
    "ToolContext",
    "BaseTool",
    "FunctionTool",
    "ToolRegistry",
    "ExecutionPolicy",
    "HookPlugin",
    "HookEvent",
    "HookContext",
]
