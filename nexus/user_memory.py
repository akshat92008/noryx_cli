"""
User Memory — persistent user preferences that persist across sessions and projects.

Tracks user preferences, coding style, past corrections, and behavioral patterns.
Stored in ~/.nexusai/user_prefs.json.
"""

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime

from nexus.paths import nexus_home

PREFS_FILE = nexus_home() / "user_prefs.json"


@dataclass
class UserPreferences:
    """Persistent user preferences."""

    preferred_model: str = ""
    preferred_language: str = ""
    coding_style: dict[str, str] = field(default_factory=dict)
    conventions: list[str] = field(default_factory=list)
    corrections: list[dict] = field(default_factory=list)  # Things the user corrected
    favorite_tools: list[str] = field(default_factory=list)
    disliked_patterns: list[str] = field(default_factory=list)
    theme: str = "dark"
    verbose: bool = True
    auto_commit: bool = False
    auto_test: bool = False
    auto_lint: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_prompt_addon(self) -> str:
        """Generate a system prompt addon from user preferences."""
        parts = []

        if self.conventions:
            parts.append("[USER PREFERENCES]")
            for conv in self.conventions:
                parts.append(f"  • {conv}")

        if self.coding_style:
            parts.append("  Coding style:")
            for key, value in self.coding_style.items():
                parts.append(f"    {key}: {value}")

        if self.corrections:
            recent = self.corrections[-5:]  # Last 5 corrections
            parts.append("  Recent corrections (apply these going forward):")
            for corr in recent:
                parts.append(f"    • {corr.get('lesson', '')}")

        if self.disliked_patterns:
            parts.append("  Avoid these patterns:")
            for pattern in self.disliked_patterns:
                parts.append(f"    ✗ {pattern}")

        if parts:
            parts.append("[END USER PREFERENCES]")
            return "\n".join(parts)
        return ""


class UserMemory:
    """
    Manages persistent user preferences across sessions and projects.

    Preferences are stored in ~/.nexusai/user_prefs.json and are automatically
    loaded and saved.

    Usage:
        um = UserMemory()
        prefs = um.load()

        # Update preferences
        um.set_preference("preferred_model", "kimi-k2.6")
        um.add_convention("Always use TypeScript")
        um.record_correction("Use arrow functions instead of function declarations")

        # Get prompt addon
        addon = um.get_prompt_addon()
    """

    def __init__(self):
        self._prefs: UserPreferences | None = None

    def load(self) -> UserPreferences:
        """Load user preferences from disk."""
        if self._prefs is not None:
            return self._prefs

        PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)

        if PREFS_FILE.exists():
            try:
                with open(PREFS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._prefs = UserPreferences(
                    **{k: v for k, v in data.items() if k in UserPreferences.__dataclass_fields__}
                )
            except (json.JSONDecodeError, OSError, TypeError):
                self._prefs = UserPreferences()
        else:
            self._prefs = UserPreferences()

        return self._prefs

    def save(self):
        """Save user preferences to disk."""
        prefs = self.load()
        prefs.updated_at = datetime.now().isoformat()

        PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(PREFS_FILE, "w", encoding="utf-8") as f:
            json.dump(asdict(prefs), f, indent=2)

    def set_preference(self, key: str, value) -> bool:
        """Set a user preference."""
        prefs = self.load()
        if hasattr(prefs, key):
            setattr(prefs, key, value)
            self.save()
            return True
        return False

    def add_convention(self, convention: str):
        """Add a coding convention."""
        prefs = self.load()
        if convention not in prefs.conventions:
            prefs.conventions.append(convention)
            # Keep list manageable
            if len(prefs.conventions) > 50:
                prefs.conventions = prefs.conventions[-50:]
            self.save()

    def remove_convention(self, convention: str) -> bool:
        """Remove a coding convention."""
        prefs = self.load()
        if convention in prefs.conventions:
            prefs.conventions.remove(convention)
            self.save()
            return True
        return False

    def record_correction(self, lesson: str, context: str = ""):
        """Record a user correction for future reference."""
        prefs = self.load()
        prefs.corrections.append(
            {
                "lesson": lesson,
                "context": context,
                "timestamp": datetime.now().isoformat(),
            }
        )
        # Keep last 100 corrections
        if len(prefs.corrections) > 100:
            prefs.corrections = prefs.corrections[-100:]
        self.save()

    def set_coding_style(self, key: str, value: str):
        """Set a coding style preference."""
        prefs = self.load()
        prefs.coding_style[key] = value
        self.save()

    def add_disliked_pattern(self, pattern: str):
        """Add a pattern to avoid."""
        prefs = self.load()
        if pattern not in prefs.disliked_patterns:
            prefs.disliked_patterns.append(pattern)
            self.save()

    def get_prompt_addon(self) -> str:
        """Get the system prompt addon from user preferences."""
        prefs = self.load()
        return prefs.to_prompt_addon()

    def get_preferred_model(self) -> str:
        """Get the user's preferred model."""
        prefs = self.load()
        return prefs.preferred_model

    def get_summary(self) -> str:
        """Get a human-readable summary of user preferences."""
        prefs = self.load()
        lines = ["👤 User Preferences"]
        if prefs.preferred_model:
            lines.append(f"  Model: {prefs.preferred_model}")
        if prefs.preferred_language:
            lines.append(f"  Language: {prefs.preferred_language}")
        lines.append(f"  Conventions: {len(prefs.conventions)}")
        lines.append(f"  Corrections: {len(prefs.corrections)}")
        lines.append(f"  Style rules: {len(prefs.coding_style)}")
        lines.append(f"  Auto-commit: {'yes' if prefs.auto_commit else 'no'}")
        lines.append(f"  Auto-test: {'yes' if prefs.auto_test else 'no'}")
        lines.append(f"  Updated: {prefs.updated_at[:19]}")
        return "\n".join(lines)

    def reset(self):
        """Reset all preferences."""
        self._prefs = UserPreferences()
        self.save()
