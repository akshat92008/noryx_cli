"""
nexus.agent package
-------------------
Canonical implementation in nexus.agent.core (original agent.py).
"""
from nexus.agent.core import Agent, _is_relative_to, _redact_runtime_text, _effective_evidence

__all__ = ["Agent", "_is_relative_to", "_redact_runtime_text", "_effective_evidence"]
