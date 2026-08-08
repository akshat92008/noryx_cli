"""Network Policy — blocks SSRF, private-range access, and dangerous redirects.

Validates URLs before any outbound HTTP request to prevent Server-Side
Request Forgery (SSRF) and network abuse through the agent's web tools.
"""

from __future__ import annotations

import ipaddress
import os
import queue
import socket
import threading
import time
import urllib.parse
from dataclasses import dataclass
from typing import Callable


def network_globally_disabled() -> bool:
    """Return whether the process-level outbound network kill switch is active."""
    return os.environ.get("NEXUS_DISABLE_NETWORK", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@dataclass(frozen=True)
class NetworkViolation:
    """A single network policy violation."""

    url: str
    reason: str
    category: str  # "private_range", "metadata", "loopback", "blocked_scheme", etc.


@dataclass(frozen=True)
class ResolvedTarget:
    """A URL and the exact validated addresses an HTTP client may connect to."""

    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


# Cloud metadata endpoints (AWS, GCP, Azure, DigitalOcean, Oracle, Alibaba)
_METADATA_IPS = frozenset(
    {
        "169.254.169.254",  # AWS / GCP / Azure / most clouds
        "100.100.100.200",  # Alibaba Cloud
        "192.0.0.192",  # Oracle Cloud
    }
)

_METADATA_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata.goog",
        "metadata.azure.com",
        "metadata.oraclecloud.com",
    }
)

_BLOCKED_SCHEMES = frozenset(
    {
        "file",
        "ftp",
        "gopher",
        "data",
        "javascript",
        "vbscript",
        "jar",
        "netdoc",
        "mailto",
    }
)

_ALLOWED_SCHEMES = frozenset({"http", "https"})

MAX_RESPONSE_BYTES = 10 * 1024 * 1024  # 10 MB default limit

_DANGEROUS_CONTENT_TYPES = frozenset(
    {
        "application/x-executable",
        "application/x-sharedlib",
        "application/x-mach-binary",
        "application/x-dosexec",
    }
)


class NetworkPolicy:
    """Validates URLs against SSRF and network-abuse rules.

    Usage::

        policy = NetworkPolicy()
        violation = policy.check_url("http://169.254.169.254/latest/meta-data/")
        if violation:
            raise ValueError(violation.reason)
    """

    def __init__(
        self,
        *,
        allow_localhost: bool = False,
        allow_private: bool = False,
        allowed_hosts: frozenset[str] | None = None,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
        dns_timeout_seconds: float = 3.0,
        dns_cache_ttl_seconds: float = 30.0,
        resolver: Callable[..., list] | None = None,
    ):
        self.allow_localhost = allow_localhost
        self.allow_private = allow_private
        self.allowed_hosts = allowed_hosts  # If set, only these hosts are allowed
        self.max_response_bytes = max_response_bytes
        self.dns_timeout_seconds = max(0.05, float(dns_timeout_seconds))
        self.dns_cache_ttl_seconds = max(0.0, float(dns_cache_ttl_seconds))
        self._resolver = resolver or socket.getaddrinfo
        self._dns_cache: dict[str, tuple[float, tuple[str, ...]]] = {}

    def check_url(self, url: str) -> NetworkViolation | None:
        """Validate a URL before making a request.  Returns None if allowed."""
        violation, _target = self.resolve_url(url)
        return violation

    def check_url_syntax(self, url: str) -> NetworkViolation | None:
        """Validate URL syntax/host policy without starting a DNS lookup."""
        try:
            parsed = urllib.parse.urlparse(url)
        except ValueError:
            return NetworkViolation(url, "Malformed URL", "parse_error")

        # ── Scheme check ────────────────────────────────────────────────
        scheme = (parsed.scheme or "").lower()
        if scheme in _BLOCKED_SCHEMES:
            return NetworkViolation(url, f"Blocked scheme: {scheme}", "blocked_scheme")
        if scheme not in _ALLOWED_SCHEMES:
            return NetworkViolation(url, f"Unsupported scheme: {scheme}", "blocked_scheme")

        # ── Host check ──────────────────────────────────────────────────
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if not hostname:
            return NetworkViolation(url, "Empty hostname", "empty_host")
        if parsed.username is not None or parsed.password is not None:
            return NetworkViolation(
                url,
                "Credentials in URL authority are not allowed",
                "userinfo",
            )

        # Check metadata hosts
        if hostname in _METADATA_HOSTS:
            return NetworkViolation(
                url,
                f"Cloud metadata hostname blocked: {hostname}",
                "metadata",
            )

        # Optional allowlist
        if self.allowed_hosts is not None and hostname not in self.allowed_hosts:
            return NetworkViolation(
                url,
                f"Host not in allowlist: {hostname}",
                "not_allowed",
            )

        try:
            literal = ipaddress.ip_address(hostname)
        except ValueError:
            return None
        return self._check_ip(url, str(literal), hostname)

    def resolve_url(
        self, url: str
    ) -> tuple[NetworkViolation | None, ResolvedTarget | None]:
        """Validate and resolve a URL, returning the addresses that must be pinned."""
        violation = self.check_url_syntax(url)
        if violation:
            return violation, None
        parsed = urllib.parse.urlparse(url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        try:
            port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
        except ValueError:
            return NetworkViolation(url, "Invalid URL port", "parse_error"), None
        try:
            literal = ipaddress.ip_address(hostname)
            addresses = (str(literal),)
        except ValueError:
            addresses, error = self._resolve_host(hostname, port)
            if error:
                return NetworkViolation(url, error[0], error[1]), None
        for address in addresses:
            violation = self._check_ip(url, address, hostname)
            if violation:
                return violation, None
        return None, ResolvedTarget(url, hostname, port, addresses)

    def check_redirect(self, original_url: str, redirect_url: str) -> NetworkViolation | None:
        """Revalidate a redirect destination — redirects into blocked ranges are SSRF."""
        violation = self.check_url(redirect_url)
        if violation:
            return NetworkViolation(
                redirect_url,
                f"Redirect from {original_url} leads to blocked destination: {violation.reason}",
                f"redirect_{violation.category}",
            )
        return None

    def check_content_type(self, content_type: str) -> NetworkViolation | None:
        """Check if a response content type is safe."""
        ct = content_type.lower().split(";")[0].strip()
        if ct in _DANGEROUS_CONTENT_TYPES:
            return NetworkViolation(
                "",
                f"Dangerous content type: {ct}",
                "dangerous_content_type",
            )
        return None

    def _resolve_host(
        self, hostname: str, port: int
    ) -> tuple[tuple[str, ...], tuple[str, str] | None]:
        """Resolve on a bounded daemon thread and fail closed on every DNS error."""
        cached = self._dns_cache.get(hostname)
        now = time.monotonic()
        if cached and now - cached[0] <= self.dns_cache_ttl_seconds:
            return cached[1], None

        result: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

        def resolve() -> None:
            try:
                result.put(
                    (
                        True,
                        self._resolver(
                            hostname,
                            port,
                            socket.AF_UNSPEC,
                            socket.SOCK_STREAM,
                        ),
                    )
                )
            except BaseException as exc:
                result.put((False, exc))

        threading.Thread(
            target=resolve,
            name=f"nexus-dns-{hostname[:32]}",
            daemon=True,
        ).start()
        try:
            succeeded, value = result.get(timeout=self.dns_timeout_seconds)
        except queue.Empty:
            return (), (f"DNS resolution timed out for {hostname}", "dns_timeout")
        if not succeeded:
            return (), (f"DNS resolution failed for {hostname}: {value}", "dns_error")
        addresses = tuple(
            dict.fromkeys(
                str(info[4][0])
                for info in value
                if len(info) >= 5 and info[4] and info[4][0]
            )
        )
        if not addresses:
            return (), (f"DNS resolution returned no addresses for {hostname}", "dns_empty")
        self._dns_cache[hostname] = (now, addresses)
        return addresses, None

    def _check_ip(self, url: str, ip_str: str, hostname: str) -> NetworkViolation | None:
        """Check a single IP address against blocked ranges."""
        try:
            addr = ipaddress.ip_address(ip_str)
        except ValueError:
            return None

        # Cloud metadata IPs
        if ip_str in _METADATA_IPS:
            return NetworkViolation(
                url,
                f"Cloud metadata IP blocked: {ip_str} (resolved from {hostname})",
                "metadata",
            )

        # Loopback (127.0.0.0/8, ::1)
        if addr.is_loopback and not self.allow_localhost:
            return NetworkViolation(
                url,
                f"Loopback address blocked: {ip_str} (resolved from {hostname})",
                "loopback",
            )

        # Private ranges (10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16, fc00::/7)
        if addr.is_private and not self.allow_private:
            return NetworkViolation(
                url,
                f"Private address blocked: {ip_str} (resolved from {hostname})",
                "private_range",
            )

        # Link-local (169.254.0.0/16, fe80::/10)
        if addr.is_link_local:
            return NetworkViolation(
                url,
                f"Link-local address blocked: {ip_str} (resolved from {hostname})",
                "link_local",
            )

        # Multicast
        if addr.is_multicast:
            return NetworkViolation(
                url,
                f"Multicast address blocked: {ip_str}",
                "multicast",
            )

        # Unspecified (0.0.0.0, ::)
        if addr == ipaddress.ip_address("0.0.0.0") or addr == ipaddress.ip_address("::"):
            return NetworkViolation(
                url,
                f"Unspecified address blocked: {ip_str}",
                "unspecified",
            )

        # Includes reserved/documentation ranges and shared carrier-grade NAT
        # space. These are never valid public web destinations for agent tools.
        if not addr.is_global and not (
            (addr.is_loopback and self.allow_localhost)
            or (addr.is_private and self.allow_private)
        ):
            return NetworkViolation(
                url,
                f"Non-public address blocked: {ip_str} (resolved from {hostname})",
                "special_range",
            )

        return None
