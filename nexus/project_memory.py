"""
Project Memory — reads and enforces per-project rules from NEXUS.md.

Equivalent to Claude Code's CLAUDE.md — lets users define project-specific
conventions, preferences, and guardrails.

Example NEXUS.md:
    # Project Rules
    - Always use pnpm, never npm or yarn
    - Use TypeScript strict mode
    - Run tests before committing
    - Never use Redux, prefer Zustand
    - Follow Google's style guide

    # Safety Rules
    blocked_commands:
      - npm install
      - yarn add

    # Testing
    test_command: pnpm test
    lint_command: pnpm lint
"""

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ProjectRules:
    """Parsed project rules from NEXUS.md."""

    raw_content: str = ""
    conventions: list[str] = field(default_factory=list)
    safety_rules: dict = field(default_factory=dict)
    test_command: str = ""
    lint_command: str = ""
    build_command: str = ""
    format_command: str = ""
    preferred_model: str = ""
    preferred_language: str = ""
    preferred_framework: str = ""
    custom_tools: list[dict] = field(default_factory=list)
    hooks: list[dict] = field(default_factory=list)
    blocked_commands: list[str] = field(default_factory=list)
    allowed_commands: list[str] = field(default_factory=list)
    file_patterns: dict[str, str] = field(default_factory=dict)  # pattern -> convention

    def to_prompt_addon(self) -> str:
        """Generate a system prompt addon from the project rules."""
        if not self.conventions and not self.raw_content:
            return ""

        parts = ["\n[TRUSTED PROJECT INSTRUCTIONS]"]

        if self.conventions:
            for rule in self.conventions:
                parts.append(f"  • {rule}")

        if self.test_command:
            parts.append(f"  • Test command: {self.test_command}")
        if self.lint_command:
            parts.append(f"  • Lint command: {self.lint_command}")
        if self.build_command:
            parts.append(f"  • Build command: {self.build_command}")
        if self.format_command:
            parts.append(f"  • Format command: {self.format_command}")
        if self.preferred_model:
            parts.append(f"  • Preferred model: {self.preferred_model}")

        if self.blocked_commands:
            parts.append("  Blocked commands:")
            for cmd in self.blocked_commands:
                parts.append(f"    ✗ {cmd}")

        parts.append("[END TRUSTED PROJECT INSTRUCTIONS]")
        return "\n".join(parts)


class ProjectMemory:
    """
    Reads and enforces per-project rules from NEXUS.md.

    Looks for NEXUS.md in:
    1. Current working directory
    2. Parent directories (up to 5 levels)
    3. ~/.nexusai/global_rules.md (fallback)

    Usage:
        pm = ProjectMemory("/path/to/project")
        rules = pm.load_rules()
        prompt_addon = rules.to_prompt_addon()
        safety_config = pm.get_safety_config()
    """

    RULE_FILENAMES = [
        "NEXUS.md",
        "nexus.md",
        ".nexus.md",
        "AGENTS.md",
        "AGENT.md",
        "CLAUDE.md",
    ]

    def __init__(self, working_dir: str):
        self.working_dir = working_dir
        self._rules: ProjectRules | None = None
        self._rules_path: str | None = None

    def load_rules(self) -> ProjectRules:
        """Load and parse project rules from NEXUS.md."""
        if self._rules is not None:
            return self._rules

        content = self._find_and_read_rules()
        if content:
            self._rules = self._parse_rules(content)
        else:
            self._rules = ProjectRules()

        return self._rules

    def get_safety_config(self) -> dict:
        """Get safety configuration derived from project rules."""
        rules = self.load_rules()
        return {
            "blocked_commands": rules.blocked_commands,
            "allowed_commands": rules.allowed_commands,
        }

    def get_prompt_addon(self) -> str:
        """Get the system prompt addon from project rules."""
        rules = self.load_rules()
        return rules.to_prompt_addon()

    def get_test_command(self) -> str:
        """Get the project's test command."""
        rules = self.load_rules()
        return rules.test_command

    def get_lint_command(self) -> str:
        """Get the project's lint command."""
        rules = self.load_rules()
        return rules.lint_command

    def get_build_command(self) -> str:
        """Get the project's build command."""
        rules = self.load_rules()
        return rules.build_command

    def rules_file_exists(self) -> bool:
        """Check if a NEXUS.md file exists."""
        return bool(self.get_rules_paths())

    def get_rules_path(self) -> str | None:
        """Get the first project instruction path for legacy callers."""
        if self._rules_path:
            return self._rules_path
        paths = self.get_rules_paths()
        if paths:
            self._rules_path = paths[0]
        return self._rules_path

    def get_rules_paths(self) -> list[str]:
        """Return every instruction file at the nearest repository level."""
        search_dir = Path(self.working_dir).resolve()
        for _ in range(5):
            found = []
            seen_inodes = set()
            for filename in self.RULE_FILENAMES:
                p = search_dir / filename
                if p.is_file():
                    try:
                        st = p.stat()
                        if st.st_ino not in seen_inodes:
                            seen_inodes.add(st.st_ino)
                            found.append(str(p.resolve()))
                    except OSError:
                        pass
            if found:
                return found
            parent = search_dir.parent
            if parent == search_dir:
                break
            search_dir = parent
        global_rules = Path.home() / ".nexusai" / "global_rules.md"
        return [str(global_rules.resolve())] if global_rules.is_file() else []

    def create_default_rules(self) -> str:
        """Create a default NEXUS.md file in the project root."""
        default_content = """# NEXUS.md — Project Rules for NexusAI

## Conventions
- Follow consistent naming conventions
- Write meaningful commit messages
- Add error handling to all functions
- Include type hints (Python) or TypeScript types

## Commands
# test_command: pytest
# lint_command: ruff check .
# build_command: python -m build
# format_command: ruff format .

## Safety
# blocked_commands:
#   - rm -rf /
# allowed_commands:
#   - npm run dev

## Preferences
# preferred_model: deepseek-v4
"""
        filepath = Path(self.working_dir) / "NEXUS.md"
        filepath.write_text(default_content, encoding="utf-8")
        self._rules_path = str(filepath)
        return str(filepath)

    def reload(self):
        """Force reload of project rules."""
        self._rules = None
        self._rules_path = None
        self.load_rules()

    # ── Private Methods ──────────────────────────────────────────────────

    def _find_rules_file(self) -> Path | None:
        """Search for NEXUS.md in the project and parent directories."""
        paths = self.get_rules_paths()
        return Path(paths[0]) if paths else None

    def _find_and_read_rules(self) -> str:
        """Find and read the rules file."""
        paths = [Path(item) for item in self.get_rules_paths()]
        if not paths:
            return ""

        self._rules_path = str(paths[0])
        contents = []
        for path in paths:
            try:
                contents.append(
                    f"# Instructions from {path.name}\n"
                    + path.read_text(encoding="utf-8", errors="replace")
                )
            except OSError:
                continue
        return "\n\n".join(contents)

    def _parse_rules(self, content: str) -> ProjectRules:
        """Parse NEXUS.md content into structured rules."""
        rules = ProjectRules(raw_content=content)

        lines = content.split("\n")
        current_section = ""

        for line in lines:
            stripped = line.strip()

            # Section headers
            if stripped.startswith("##"):
                current_section = stripped.lstrip("#").strip().lower()
                continue

            if stripped.startswith("#") and not stripped.startswith("# "):
                # Comment line (not a header)
                continue

            if not stripped or stripped.startswith("#"):
                # Empty line or comment
                if stripped.startswith("# "):
                    # Inline comment with directive
                    directive = stripped[2:].strip()
                    self._parse_directive(directive, rules)
                continue

            # Parse list items as conventions
            if stripped.startswith("- ") or stripped.startswith("* "):
                item = stripped[2:].strip()

                if current_section in ("safety", "blocked", "blocked_commands"):
                    rules.blocked_commands.append(item)
                elif current_section in ("allowed", "allowed_commands"):
                    rules.allowed_commands.append(item)
                elif current_section in ("hooks",):
                    rules.hooks.append({"raw": item})
                else:
                    rules.conventions.append(item)
                continue

            # Parse key-value directives
            self._parse_directive(stripped, rules)

        return rules

    def _parse_directive(self, text: str, rules: ProjectRules):
        """Parse a key: value directive."""
        match = re.match(r"^(\w[\w_]*)\s*:\s*(.+)$", text)
        if not match:
            return

        key = match.group(1).lower().strip()
        value = match.group(2).strip()

        directive_map = {
            "test_command": "test_command",
            "test": "test_command",
            "lint_command": "lint_command",
            "lint": "lint_command",
            "build_command": "build_command",
            "build": "build_command",
            "format_command": "format_command",
            "format": "format_command",
            "preferred_model": "preferred_model",
            "model": "preferred_model",
            "preferred_language": "preferred_language",
            "language": "preferred_language",
            "preferred_framework": "preferred_framework",
            "framework": "preferred_framework",
        }

        attr = directive_map.get(key)
        if attr:
            setattr(rules, attr, value)
