"""Secret detection and redaction module for repository intelligence — Sprint 5."""

from __future__ import annotations

import re


SECRET_PATTERNS = [
    # API Keys & Tokens
    (r"(?i)(api[_-]?key|secret[_-]?key|auth[_-]?token|access[_-]?token)\s*[:=]\s*['\"]?([A-Za-z0-9_\-.~+]{12,})['\"]?", "SECRET_KEY_REDACTED"),
    (r"(ghp_[A-Za-z0-9]{36}|github_pat_[A-Za-z0-9]{22}_[A-Za-z0-9]{59})", "GITHUB_TOKEN_REDACTED"),
    (r"(AKIA[0-9A-Z]{16})", "AWS_ACCESS_KEY_REDACTED"),
    (r"-----BEGIN (RSA|EC|OPENSSH|DSA|PRIVATE) KEY-----[\s\S]*?-----END \1 KEY-----", "PRIVATE_KEY_REDACTED"),
    (r"Bearer\s+([A-Za-z0-9\-._~+/]+=*)", "BEARER_TOKEN_REDACTED"),
]


class SecretProtector:
    """Detects and redacts secrets from source code before model presentation."""

    @staticmethod
    def is_secret_file(file_path: str) -> bool:
        name = file_path.lower().split("/")[-1]
        return name in {".env", ".env.local", ".env.production", "secrets.yaml", "credentials.json", "id_rsa", "id_ed25519"} or name.endswith(".pem") or name.endswith(".key")

    @staticmethod
    def sanitize(content: str, file_path: str = "") -> tuple[str, bool]:
        """
        Sanitize content. If the file itself is a secret file (e.g. .env),
        return a redacted placeholder. Otherwise redact matching secret tokens.
        """
        if SecretProtector.is_secret_file(file_path):
            return f"[PROTECTED FILE: {file_path} - Secret values redacted by security policy]", True

        has_secrets = False
        sanitized = content

        for pattern, replacement in SECRET_PATTERNS:
            if re.search(pattern, sanitized):
                has_secrets = True
                sanitized = re.sub(pattern, f"[REDACTED_{replacement}]", sanitized)

        return sanitized, has_secrets
