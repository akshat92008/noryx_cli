"""
Subagent System — isolated parallel agents for complex tasks.

Spawns independent agent instances, each with their own context and system prompt,
that execute tasks in parallel and report summaries back to the main agent.
"""

from nexus.subagents.base import BaseSubagent, SubagentResult
from nexus.subagents.orchestrator import SubagentOrchestrator
from nexus.subagents.templates import SUBAGENT_TEMPLATES

__all__ = ["BaseSubagent", "SubagentResult", "SubagentOrchestrator", "SUBAGENT_TEMPLATES"]
