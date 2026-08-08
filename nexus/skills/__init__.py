"""
Skills System — reusable expertise modules that augment the agent's capabilities.

Skills are domain-specific knowledge packs containing system prompt addons,
tool permissions, quality checklists, and workflow patterns.

They auto-trigger based on keyword matching and can stack for combined expertise.
"""

from nexus.skills.base import BaseSkill, SkillTrigger
from nexus.skills.loader import SkillLoader, SkillRegistry

__all__ = ["BaseSkill", "SkillTrigger", "SkillLoader", "SkillRegistry"]
