"""Secret discovery and redaction layer for Nexus CLI.

Scans for credentials, API keys, private keys, bearer tokens, and connection strings
in terminal output, model prompts, tool results, logs, and evidence receipts.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from enum import Enum
from typing import Any

SECRET_REDACTED_TEXT = "[REDACTED]"

KNOWN_SECRET_PATTERNS = [
    # API Keys & Tokens
    (r"sk-[a-zA-Z0-9\-_]{20,}", "OpenAI API Key"),
    (r"sk-ant-api[0-9a-zA-Z\-_]{20,}", "Anthropic API Key"),
    (r"AKIA[0-9A-Z]{16}", "AWS Access Key ID"),
    (r"ghp_[a-zA-Z0-9]{36}", "GitHub Personal Access Token"),
    (r"gho_[a-zA-Z0-9]{36}", "GitHub OAuth Token"),
    (r"glpat-[a-zA-Z0-9\-_]{20}", "GitLab Personal Access Token"),
    (r"xoxb-[0-9]{10,13}-[0-9]{10,13}-[a-zA-Z0-9]{24}", "Slack Bot Token"),
    (r"xoxp-[0-9]{10,13}-[0-9]{10,13}-[a-zA-Z0-9]{24}", "Slack User Token"),
    # Private Keys & Bearer Tokens
    (r"-----BEGIN (RSA|EC|DSA|OPENSSH) PRIVATE KEY-----", "Private Key Header"),
    (r"Bearer\s+[a-zA-Z0-9\-\._~\+\/]+=*", "HTTP Bearer Token"),
    # Database Connection Strings with Passwords
    (r"(postgres|mysql|mongodb|redis)://[^:]+:([^@]+)@", "Database Password"),
]


class SecretStatus(str, Enum):
    SECRET_PRESENT = "secret_present"
    SECRET_REDACTED = "secret_redacted"
    SECRET_ACCESS_DENIED = "secret_access_denied"
    POSSIBLE_SECRET = "possible_secret"


@dataclass
class SecretMatch:
    pattern_name: str
    matched_text: str
    start: int
    end: int


def calculate_entropy(text: str) -> float:
    """Calculate Shannon entropy of string."""
    if not text:
        return 0.0
    prob = [float(text.count(c)) / len(text) for c in dict.fromkeys(list(text))]
    entropy = -sum([p * math.log(p) / math.log(2.0) for p in prob])
    return entropy


class SecretScanner:
    """Detects potential secrets in raw strings."""

    def __init__(self, min_entropy: float = 4.5):
        self.min_entropy = min_entropy

    def scan(self, text: str) -> list[SecretMatch]:
        matches: list[SecretMatch] = []
        if not text:
            return matches

        # 1. Pattern Matching
        for pattern, name in KNOWN_SECRET_PATTERNS:
            for m in re.finditer(pattern, text):
                matches.append(SecretMatch(pattern_name=name, matched_text=m.group(0), start=m.start(), end=m.end()))

        return matches


class SecretRedactor:
    """Redacts discovered secrets from strings and structured objects."""

    def __init__(self, scanner: SecretScanner | None = None):
        self.scanner = scanner or SecretScanner()

    def redact_text(self, text: str) -> str:
        if not text:
            return text

        redacted = text
        matches = self.scanner.scan(text)
        # Process matches in reverse order of appearance to maintain index accuracy
        for match in sorted(matches, key=lambda m: m.start, reverse=True):
            redacted = redacted[: match.start] + SECRET_REDACTED_TEXT + redacted[match.end :]

        return redacted

    def redact_object(self, obj: Any) -> Any:
        """Recursively redact secrets in dicts, lists, and strings."""
        if isinstance(obj, str):
            return self.redact_text(obj)
        elif isinstance(obj, dict):
            new_dict = {}
            for k, v in obj.items():
                if any(sec_key in str(k).lower() for sec_key in ("secret", "password", "token", "api_key", "key")):
                    new_dict[k] = SECRET_REDACTED_TEXT
                else:
                    new_dict[k] = self.redact_object(v)
            return new_dict
        elif isinstance(obj, list):
            return [self.redact_object(item) for item in obj]
        elif isinstance(obj, tuple):
            return tuple(self.redact_object(item) for item in obj)
        return obj
