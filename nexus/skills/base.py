"""
Base Skill — the foundation class for all skills.

Skills are reusable expertise modules containing domain-specific knowledge,
system prompt addons, tool permissions, and quality checklists.
"""

from dataclasses import dataclass, field


@dataclass
class SkillTrigger:
    """Defines when a skill should auto-activate."""

    keywords: list[str] = field(default_factory=list)
    file_patterns: list[str] = field(default_factory=list)  # e.g., ["*.tsx", "*.jsx"]
    intent_types: list[str] = field(default_factory=list)  # e.g., ["build", "fix"]
    priority: int = 50  # 0 = lowest, 100 = highest


class BaseSkill:
    """
    Base class for all skills. Subclass this to create a new skill.

    Skills provide:
    - Domain-specific system prompt additions
    - Tool usage recommendations
    - Quality checklists for verification
    - Compatible skills for stacking

    Example:
        class ReactSkill(BaseSkill):
            name = "react"
            description = "React.js frontend development expert"
            trigger = SkillTrigger(
                keywords=["react", "jsx", "component", "hook", "useState"],
                file_patterns=["*.jsx", "*.tsx"],
                intent_types=["build", "fix", "refactor"],
            )

            def get_system_prompt(self) -> str:
                return "You are a React expert. Use hooks, functional components..."
    """

    # ── Class attributes (override in subclasses) ────────────────────────
    name: str = "base"
    description: str = "Base skill"
    category: str = "general"
    trigger: SkillTrigger = SkillTrigger()
    compose_with: list[str] = []  # Skills that work well with this one
    tool_permissions: list[str] = []  # Allowed tools (empty = all)

    def __init__(self):
        self._active = False

    @property
    def is_active(self) -> bool:
        return self._active

    def activate(self):
        """Activate this skill."""
        self._active = True

    def deactivate(self):
        """Deactivate this skill."""
        self._active = False

    def get_system_prompt(self) -> str:
        """
        Return the system prompt addon for this skill.
        Override in subclasses.
        """
        return ""

    def get_quality_checklist(self) -> list[str]:
        """
        Return a quality checklist for this skill's domain.
        Override in subclasses.
        """
        return []

    def get_workflow(self) -> list[dict]:
        """
        Return a recommended workflow for tasks in this skill's domain.
        Override in subclasses.

        Each dict has: step, description, tools
        """
        return []

    def matches(self, user_input: str, file_path: str = "", intent: str = "") -> float:
        """
        Calculate how well this skill matches the current context.
        Returns a score from 0.0 to 1.0.
        """
        score = 0.0
        text = user_input.lower()

        # Keyword matching
        if self.trigger.keywords:
            import re

            keyword_hits = 0
            for kw in self.trigger.keywords:
                if re.search(r"\b" + re.escape(kw.lower()) + r"\b", text):
                    keyword_hits += 1
            if keyword_hits > 0:
                score += min(keyword_hits * 0.25, 0.6)

        # File pattern matching
        if file_path and self.trigger.file_patterns:
            import fnmatch

            for pattern in self.trigger.file_patterns:
                if fnmatch.fnmatch(file_path.lower(), pattern.lower()):
                    score += 0.3
                    break

        # Intent matching
        if intent and self.trigger.intent_types:
            if intent.lower() in [it.lower() for it in self.trigger.intent_types]:
                score += 0.2

        # Apply priority multiplier
        score *= (self.trigger.priority / 100.0) if self.trigger.priority else 0.5

        return min(score, 1.0)

    def to_dict(self) -> dict:
        """Serialize skill info for API/UI."""
        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "active": self._active,
            "keywords": self.trigger.keywords[:10],
            "compose_with": self.compose_with,
            "priority": self.trigger.priority,
        }

    def __repr__(self) -> str:
        status = "active" if self._active else "inactive"
        return f"<Skill: {self.name} ({status})>"
