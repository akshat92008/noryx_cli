"""
Skill Loader — auto-discovery, registration, and trigger matching for skills.

Discovers skills from:
1. nexus/skills/builtin/ — built-in skills
2. trusted .nexus/skills/*.md — repository workflows
3. ~/.nexusai/skills/ — user-defined custom skills
4. Plugin skills — loaded via the plugin system
"""

import re
from pathlib import Path
from typing import Callable

from nexus.skills.base import BaseSkill, SkillTrigger


class DeclarativeSkill(BaseSkill):
    """Non-executable repository skill loaded from a trusted Markdown file."""

    def __init__(
        self,
        *,
        name: str,
        description: str,
        prompt: str,
        checklist: list[str],
        keywords: list[str],
    ):
        super().__init__()
        self.name = name
        self.description = description
        self.category = "project"
        self.trigger = SkillTrigger(keywords=keywords, priority=80)
        self._prompt = prompt
        self._checklist = checklist

    def get_system_prompt(self) -> str:
        return self._prompt

    def get_quality_checklist(self) -> list[str]:
        return list(self._checklist)


class SkillRegistry:
    """
    Central registry of all available skills.

    Handles:
    - Registration of skills
    - Auto-triggering based on user input
    - Skill composition (stacking multiple skills)
    - Activation/deactivation
    """

    def __init__(self):
        self._skills: dict[str, BaseSkill] = {}
        self._active_skills: list[str] = []

    def register(self, skill: BaseSkill):
        """Register a skill."""
        self._skills[skill.name] = skill

    def unregister(self, name: str):
        """Unregister a skill."""
        if name in self._skills:
            self._skills[name].deactivate()
            del self._skills[name]
            if name in self._active_skills:
                self._active_skills.remove(name)

    def get(self, name: str) -> BaseSkill | None:
        """Get a skill by name."""
        return self._skills.get(name)

    def list_all(self) -> list[BaseSkill]:
        """List all registered skills."""
        return list(self._skills.values())

    def list_active(self) -> list[BaseSkill]:
        """List currently active skills."""
        return [self._skills[name] for name in self._active_skills if name in self._skills]

    def activate(self, name: str) -> bool:
        """Manually activate a skill."""
        skill = self._skills.get(name)
        if skill:
            skill.activate()
            if name not in self._active_skills:
                self._active_skills.append(name)
            return True
        return False

    def deactivate(self, name: str) -> bool:
        """Deactivate a skill."""
        skill = self._skills.get(name)
        if skill:
            skill.deactivate()
            if name in self._active_skills:
                self._active_skills.remove(name)
            return True
        return False

    def deactivate_all(self):
        """Deactivate all skills."""
        for name in list(self._active_skills):
            self.deactivate(name)

    def auto_activate(
        self,
        user_input: str,
        file_path: str = "",
        intent: str = "",
        max_skills: int = 4,
    ) -> list[BaseSkill]:
        """
        Auto-activate skills based on the user's input.

        Returns the list of newly activated skills.
        """
        # Score all skills
        scores: list[tuple[str, float]] = []
        for name, skill in self._skills.items():
            score = skill.matches(user_input, file_path, intent)
            if score > 0.1:  # Minimum threshold
                scores.append((name, score))

        # Sort by score and take top N
        scores.sort(key=lambda x: x[1], reverse=True)
        top_skills = scores[:max_skills]

        # Activate top skills
        activated = []
        for name, _score in top_skills:
            if name not in self._active_skills:
                self.activate(name)
                activated.append(self._skills[name])

        return activated

    def get_combined_prompt(self) -> str:
        """Get the combined system prompt addon from all active skills."""
        parts = []
        for name in self._active_skills:
            skill = self._skills.get(name)
            if skill:
                prompt = skill.get_system_prompt()
                if prompt:
                    parts.append(f"\n[SKILL: {skill.name}]\n{prompt}\n[END SKILL]")
        return "\n".join(parts)

    def get_combined_checklist(self) -> list[str]:
        """Get combined quality checklist from all active skills."""
        checklist = []
        for name in self._active_skills:
            skill = self._skills.get(name)
            if skill:
                items = skill.get_quality_checklist()
                for item in items:
                    if item not in checklist:
                        checklist.append(item)
        return checklist

    def get_skill_summary(self) -> str:
        """Get a human-readable summary of all skills."""
        lines = [f"🧠 Skills ({len(self._skills)} registered, {len(self._active_skills)} active)"]
        lines.append("")

        by_category: dict[str, list[BaseSkill]] = {}
        for skill in self._skills.values():
            cat = skill.category
            if cat not in by_category:
                by_category[cat] = []
            by_category[cat].append(skill)

        for category, skills in sorted(by_category.items()):
            lines.append(f"  {category.title()}:")
            for skill in sorted(skills, key=lambda s: s.name):
                status = "🟢" if skill.is_active else "⚪"
                lines.append(f"    {status} {skill.name} — {skill.description}")

        return "\n".join(lines)


class SkillLoader:
    """
    Discovers and loads skills from multiple sources.

    Sources:
    1. nexus/skills/builtin/ — built-in skills
    2. ~/.nexusai/skills/ — user-defined custom skills
    """

    def __init__(
        self,
        registry: SkillRegistry,
        working_dir: str | Path | None = None,
        trusted: Callable[[str | Path], bool] | None = None,
    ):
        self.registry = registry
        self.working_dir = Path(working_dir).resolve() if working_dir else None
        self.trusted = trusted

    def load_all(self):
        """Load all skills from all sources."""
        self.load_builtin()
        self.load_project()
        self.load_custom()

    def load_builtin(self):
        """Load built-in skills from nexus/skills/builtin/."""
        from nexus.skills.builtin import ALL_SKILLS

        for skill_class in ALL_SKILLS:
            try:
                skill = skill_class()
                self.registry.register(skill)
            except (OSError, ValueError):
                pass  # Don't let a bad skill break everything

    def load_custom(self):
        """Load custom skills from ~/.nexusai/skills/."""
        custom_dir = Path.home() / ".nexusai" / "skills"
        if not custom_dir.is_dir():
            return

        # Custom skills are Python files that define a class inheriting BaseSkill
        for filepath in custom_dir.glob("*.py"):
            if filepath.name.startswith("_"):
                continue
            try:
                import importlib.util

                spec = importlib.util.spec_from_file_location(
                    f"custom_skill_{filepath.stem}", str(filepath)
                )
                if spec and spec.loader:
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)

                    # Find BaseSkill subclasses in the module
                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)
                        if (
                            isinstance(attr, type)
                            and issubclass(attr, BaseSkill)
                            and attr is not BaseSkill
                        ):
                            skill = attr()
                            self.registry.register(skill)
            except (ImportError, TypeError, ValueError):
                pass  # Don't let bad custom skills break anything

    def load_project(self):
        """Load trusted, declarative ``.nexus/skills/*.md`` workflows."""
        if not self.working_dir:
            return
        directory = self.working_dir / ".nexus" / "skills"
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.md")):
            if self.trusted is not None and not self.trusted(path):
                continue
            try:
                content = path.read_text(encoding="utf-8")
                metadata, prompt = self._parse_markdown_skill(content, path)
                skill = DeclarativeSkill(
                    name=metadata["name"],
                    description=metadata["description"],
                    prompt=prompt,
                    checklist=metadata["checklist"],
                    keywords=metadata["keywords"],
                )
                self.registry.register(skill)
            except (OSError, ValueError, KeyError):
                continue

    @staticmethod
    def _parse_markdown_skill(content: str, path: Path) -> tuple[dict, str]:
        metadata = {
            "name": path.stem,
            "description": f"Repository workflow from {path.name}",
            "keywords": [path.stem.replace("-", " ")],
            "checklist": [],
        }
        body = content
        if content.startswith("---\n") and "\n---\n" in content[4:]:
            header, body = content[4:].split("\n---\n", 1)
            for line in header.splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                normalized = key.strip().lower()
                raw = value.strip().strip("'\"")
                if normalized in {"name", "description"} and raw:
                    metadata[normalized] = raw
                elif normalized == "keywords":
                    metadata["keywords"] = [item.strip() for item in raw.split(",") if item.strip()]
        checklist_match = re.search(
            r"(?ims)^##\s+(?:quality\s+)?checklist\s*$\n(?P<body>.*?)(?=^##\s|\Z)",
            body,
        )
        if checklist_match:
            metadata["checklist"] = [
                match.group(1).strip()
                for match in re.finditer(
                    r"(?m)^\s*[-*]\s+(.+)$",
                    checklist_match.group("body"),
                )
            ]
        if not body.strip():
            raise ValueError(f"Project skill is empty: {path}")
        return metadata, body.strip()

    def load_from_plugin(self, skills: list[BaseSkill]):
        """Load skills provided by a plugin."""
        for skill in skills:
            self.registry.register(skill)
