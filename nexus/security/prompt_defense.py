"""Prompt-injection defense and instruction trust hierarchy for Nexus CLI.

Enforces strict authority layering beneath the model:
1. System Policy (Immutable)
2. User Instruction (Explicit User Command)
3. Project Policy (Verified Configuration)
4. Execution Contract (Plan & Task Contracts)
5. Untrusted Content (Repo files, READMEs, source comments, issue templates, tool outputs)

Untrusted repository data CANNOT override policy or escalate permissions.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Sequence


class TrustLevel(int, Enum):
    SYSTEM_POLICY = 100
    USER_INSTRUCTION = 80
    PROJECT_POLICY = 60
    PLAN_CONTRACT = 40
    UNTRUSTED_DATA = 10


SUSPICIOUS_INSTRUCTION_PATTERNS = [
    (r"ignore\s+(all\s+)?(previous|prior|system)\s+(instructions|policy|rules)", "Prompt Injection (Ignore Instructions)"),
    (r"disregard\s+(all\s+)?(system|policy)\s+rules", "Prompt Injection (Disregard Rules)"),
    (r"read\s+~?/\.ssh/", "Prompt Injection (SSH Key Access)"),
    (r"read\s+~?/\.aws/", "Prompt Injection (AWS Credential Access)"),
    (r"disable\s+all\s+(security|tests|verification)", "Prompt Injection (Disable Verification)"),
    (r"bypass\s+(policy|permissions|sandbox)", "Prompt Injection (Bypass Policy)"),
    (r"increase\s+budget\s+to\s+unlimited", "Prompt Injection (Budget Manipulation)"),
    (r"exfiltrate|send\s+source\s+code\s+to", "Prompt Injection (Exfiltration Request)"),
]


class PromptDefense:
    """Scans untrusted repository inputs and context for prompt injection attacks."""

    def __init__(self, trust_level: TrustLevel = TrustLevel.UNTRUSTED_DATA):
        self.trust_level = trust_level

    def scan_untrusted_text(self, text: str, source_label: str = "repo_content") -> list[str]:
        """Scan untrusted text for policy override or prompt injection attempts."""
        warnings: list[str] = []
        if not text:
            return warnings

        for pattern, description in SUSPICIOUS_INSTRUCTION_PATTERNS:
            if re.search(pattern, text, re.IGNORECASE):
                warnings.append(f"Suspicious instruction detected in {source_label}: {description}")

        return warnings

    def sanitize_context_block(self, text: str, source_label: str = "untrusted") -> str:
        """Wrap untrusted repository content with clear data boundaries for model context."""
        scanned_warnings = self.scan_untrusted_text(text, source_label)
        header = f"--- BEGIN UNTRUSTED DATA ({source_label}) ---"
        footer = f"--- END UNTRUSTED DATA ({source_label}) ---"
        if scanned_warnings:
            warning_text = "\n".join(f"[SECURITY WARNING: {w}]" for w in scanned_warnings)
            return f"{header}\n{warning_text}\n{text}\n{footer}"
        return f"{header}\n{text}\n{footer}"
