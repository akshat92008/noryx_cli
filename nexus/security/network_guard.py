"""Centralized Network Guard for Nexus CLI.

Enforces network operational modes, SSRF prevention, cloud metadata endpoint blocking,
and outbound URL destination allowlisting.
"""

from __future__ import annotations

import ipaddress
import re
import urllib.parse
from enum import Enum
from typing import Sequence

from nexus.network_policy import NetworkPolicy, NetworkViolation


class NetworkMode(str, Enum):
    OFFLINE = "offline"
    PROVIDERS_ONLY = "providers_only"
    PACKAGE_REGISTRIES = "package_registries"
    ALLOWLIST = "allowlist"
    UNRESTRICTED_WITH_APPROVAL = "unrestricted_with_approval"


METADATA_DESTINATIONS = (
    "169.254.169.254",
    "100.100.100.200",
    "192.0.0.192",
    "metadata.google.internal",
    "metadata.goog",
    "metadata.azure.com",
    "metadata.oraclecloud.com",
)

APPROVED_PROVIDER_DOMAINS = (
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "openrouter.ai",
)

APPROVED_PACKAGE_DOMAINS = (
    "pypi.org",
    "files.pythonhosted.org",
    "registry.npmjs.org",
    "crates.io",
)


class NetworkGuard:
    """Central network policy guard."""

    def __init__(
        self,
        mode: NetworkMode = NetworkMode.ALLOWLIST,
        allowed_domains: Sequence[str] | None = None,
    ):
        self.mode = mode
        self.allowed_domains = set(allowed_domains or [])
        self.underlying_policy = NetworkPolicy()

    def validate_url(self, url: str) -> None:
        """Validate destination URL against mode and SSRF rules.
        
        Raises ValueError if URL is blocked by network policy.
        """
        if self.mode == NetworkMode.OFFLINE:
            raise ValueError(f"Network request blocked (Network Mode is OFFLINE): {url}")

        parsed = urllib.parse.urlparse(url)
        hostname = (parsed.hostname or "").lower()

        # 1. Scheme Check
        if parsed.scheme not in ("http", "https"):
            raise ValueError(f"Network request blocked (Forbidden URL scheme {parsed.scheme!r}): {url}")

        # 2. Cloud Metadata & Private Range Check
        if hostname in METADATA_DESTINATIONS:
            raise ValueError(f"Network request blocked (Cloud Metadata Endpoint Access Forbidden): {url}")

        # Try parsing IP
        try:
            ip_obj = ipaddress.ip_address(hostname)
            if ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_link_local:
                raise ValueError(f"Network request blocked (Private / Loopback IP Access Forbidden): {url}")
        except ValueError:
            pass  # Hostname is a domain name, not an IP literal

        # 3. Mode Enforcement
        if self.mode == NetworkMode.PROVIDERS_ONLY:
            if not any(hostname == domain or hostname.endswith("." + domain) for domain in APPROVED_PROVIDER_DOMAINS):
                raise ValueError(
                    f"Network request blocked (Domain {hostname!r} not permitted in PROVIDERS_ONLY mode): {url}"
                )

        elif self.mode == NetworkMode.PACKAGE_REGISTRIES:
            valid_domains = set(APPROVED_PROVIDER_DOMAINS) | set(APPROVED_PACKAGE_DOMAINS)
            if not any(hostname == domain or hostname.endswith("." + domain) for domain in valid_domains):
                raise ValueError(
                    f"Network request blocked (Domain {hostname!r} not permitted in PACKAGE_REGISTRIES mode): {url}"
                )

        elif self.mode == NetworkMode.ALLOWLIST:
            valid_domains = set(APPROVED_PROVIDER_DOMAINS) | set(APPROVED_PACKAGE_DOMAINS) | self.allowed_domains
            if not any(hostname == domain or hostname.endswith("." + domain) for domain in valid_domains):
                raise ValueError(
                    f"Network request blocked (Domain {hostname!r} not in network allowlist): {url}"
                )

        # 4. Perform underlying SSRF resolution test
        violation = self.underlying_policy.validate_url(url)
        if violation:
            raise ValueError(f"Network policy violation ({violation.category}): {violation.reason}")
