"""Behavioural, database, and security verification adapters for Nexus."""

from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx


class ProbeStatus(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"


@dataclass
class ProbeResult:
    """One behavioural verification result."""

    kind: str
    status: ProbeStatus
    summary: str
    duration_ms: int = 0
    evidence: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == ProbeStatus.PASSED

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload


@dataclass(frozen=True)
class ApiProbeSpec:
    method: str
    url: str
    expected_status: int = 200
    expected_json: dict[str, Any] | None = None
    expected_text: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    json_body: Any = None
    timeout_seconds: float = 15.0
    allow_external: bool = False


class ApiVerifier:
    """Send bounded HTTP requests and validate status and response contracts."""

    LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}

    def verify(self, spec: ApiProbeSpec) -> ProbeResult:
        parsed = urlparse(spec.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ProbeResult("api", ProbeStatus.BLOCKED, "API URL must be HTTP(S)")
        if not spec.allow_external and parsed.hostname not in self.LOCAL_HOSTS:
            return ProbeResult(
                "api",
                ProbeStatus.BLOCKED,
                "External API verification requires explicit network approval",
                evidence={"url": spec.url},
            )

        started = time.monotonic()
        try:
            response = httpx.request(
                spec.method.upper(),
                spec.url,
                headers=spec.headers,
                json=spec.json_body,
                timeout=spec.timeout_seconds,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            return ProbeResult(
                "api",
                ProbeStatus.FAILED,
                f"API request failed: {exc}",
                duration_ms=int((time.monotonic() - started) * 1000),
                evidence={"url": spec.url, "method": spec.method.upper()},
            )

        failures: list[str] = []
        if response.status_code != spec.expected_status:
            failures.append(
                f"expected status {spec.expected_status}, received {response.status_code}"
            )
        decoded_json: Any = None
        if spec.expected_json is not None:
            try:
                decoded_json = response.json()
            except ValueError:
                failures.append("response was not valid JSON")
            else:
                if not isinstance(decoded_json, dict):
                    failures.append("JSON response was not an object")
                else:
                    for key, expected in spec.expected_json.items():
                        if decoded_json.get(key) != expected:
                            failures.append(
                                f"JSON field {key!r}: expected {expected!r}, "
                                f"received {decoded_json.get(key)!r}"
                            )
        if spec.expected_text and spec.expected_text not in response.text:
            failures.append(f"response did not contain {spec.expected_text!r}")

        headers = {
            key: value
            for key, value in response.headers.items()
            if key.lower() in {"content-type", "location", "cache-control"}
        }
        evidence = {
            "url": spec.url,
            "method": spec.method.upper(),
            "status_code": response.status_code,
            "headers": headers,
            "body_excerpt": response.text[:4000],
        }
        return ProbeResult(
            "api",
            ProbeStatus.FAILED if failures else ProbeStatus.PASSED,
            "; ".join(failures) if failures else "API contract satisfied",
            duration_ms=int((time.monotonic() - started) * 1000),
            evidence=evidence,
        )


@dataclass(frozen=True)
class BrowserStep:
    action: str
    selector: str = ""
    value: str = ""


@dataclass(frozen=True)
class BrowserProbeSpec:
    url: str
    steps: tuple[BrowserStep, ...] = ()
    screenshot_path: str = ""
    timeout_seconds: float = 30.0
    allow_external: bool = False


class BrowserVerifier:
    """Run a deterministic Playwright workflow when the optional extra exists."""

    LOCAL_HOSTS = ApiVerifier.LOCAL_HOSTS

    def verify(self, spec: BrowserProbeSpec) -> ProbeResult:
        parsed = urlparse(spec.url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return ProbeResult("browser", ProbeStatus.BLOCKED, "Browser URL must be HTTP(S)")
        if not spec.allow_external and parsed.hostname not in self.LOCAL_HOSTS:
            return ProbeResult(
                "browser",
                ProbeStatus.BLOCKED,
                "External browser verification requires explicit network approval",
            )
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            return ProbeResult(
                "browser",
                ProbeStatus.UNAVAILABLE,
                "Install nexusai-cli[browser] and run 'playwright install chromium'",
            )

        started = time.monotonic()
        console_errors: list[str] = []
        failed_requests: list[str] = []
        final_url = spec.url
        title = ""
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                page.set_default_timeout(spec.timeout_seconds * 1000)
                page.on(
                    "console",
                    lambda message: (
                        console_errors.append(message.text) if message.type == "error" else None
                    ),
                )
                page.on(
                    "requestfailed",
                    lambda request: failed_requests.append(
                        f"{request.method} {request.url}: {request.failure}"
                    ),
                )
                page.goto(spec.url, wait_until="networkidle")
                for step in spec.steps:
                    action = step.action.lower()
                    if action == "click":
                        page.locator(step.selector).click()
                    elif action == "fill":
                        page.locator(step.selector).fill(step.value)
                    elif action == "expect_text":
                        if step.value not in page.locator(step.selector or "body").inner_text():
                            raise AssertionError(
                                f"{step.selector or 'body'} did not contain {step.value!r}"
                            )
                    elif action == "wait":
                        page.wait_for_timeout(float(step.value) * 1000)
                    else:
                        raise ValueError(f"Unsupported browser action: {step.action}")
                if spec.screenshot_path:
                    target = Path(spec.screenshot_path).expanduser().resolve()
                    target.parent.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(target), full_page=True)
                final_url = page.url
                title = page.title()
                browser.close()
        except (OSError, TypeError, ValueError) as exc:
            return ProbeResult(
                "browser",
                ProbeStatus.FAILED,
                f"Browser workflow failed: {exc}",
                duration_ms=int((time.monotonic() - started) * 1000),
                evidence={
                    "url": final_url,
                    "console_errors": console_errors,
                    "failed_requests": failed_requests,
                },
            )

        failures = []
        if console_errors:
            failures.append(f"{len(console_errors)} console error(s)")
        if failed_requests:
            failures.append(f"{len(failed_requests)} failed request(s)")
        return ProbeResult(
            "browser",
            ProbeStatus.FAILED if failures else ProbeStatus.PASSED,
            ", ".join(failures) if failures else "Browser workflow satisfied",
            duration_ms=int((time.monotonic() - started) * 1000),
            evidence={
                "url": final_url,
                "title": title,
                "console_errors": console_errors,
                "failed_requests": failed_requests,
                "screenshot": spec.screenshot_path,
            },
        )


class DatabaseVerifier:
    """Inspect SQLite safely and flag destructive SQL migrations."""

    DESTRUCTIVE = (
        (re.compile(r"\bDROP\s+(?:TABLE|COLUMN|DATABASE|SCHEMA)\b", re.I), "drop"),
        (re.compile(r"\bTRUNCATE\s+TABLE\b", re.I), "truncate"),
        (re.compile(r"\bDELETE\s+FROM\b(?![^;]*\bWHERE\b)", re.I), "unbounded-delete"),
        (re.compile(r"\bALTER\s+TABLE\b.*\bRENAME\b", re.I | re.S), "rename"),
    )

    def verify_sqlite(self, database: str | Path) -> ProbeResult:
        path = Path(database).expanduser().resolve()
        if not path.is_file():
            return ProbeResult("database", ProbeStatus.FAILED, f"Database not found: {path}")
        started = time.monotonic()
        try:
            connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5)
            try:
                integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
                foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
                tables = [
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master "
                        "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                    )
                ]
            finally:
                connection.close()
        except sqlite3.Error as exc:
            return ProbeResult(
                "database",
                ProbeStatus.FAILED,
                f"SQLite validation failed: {exc}",
                duration_ms=int((time.monotonic() - started) * 1000),
            )

        passed = integrity == "ok" and not foreign_keys
        return ProbeResult(
            "database",
            ProbeStatus.PASSED if passed else ProbeStatus.FAILED,
            (
                "SQLite integrity and foreign keys passed"
                if passed
                else f"integrity={integrity}; foreign_key_violations={len(foreign_keys)}"
            ),
            duration_ms=int((time.monotonic() - started) * 1000),
            evidence={
                "path": str(path),
                "integrity": integrity,
                "foreign_key_violations": [list(row) for row in foreign_keys[:100]],
                "tables": tables,
            },
        )

    def migration_risks(self, sql: str) -> list[dict[str, str]]:
        findings = []
        for pattern, kind in self.DESTRUCTIVE:
            for match in pattern.finditer(sql):
                findings.append(
                    {
                        "severity": "high",
                        "kind": kind,
                        "statement_excerpt": match.group(0)[:500],
                        "requires_approval": "true",
                    }
                )
        return findings


class SecurityScanner:
    """Fast deterministic source scan; results never claim a complete audit."""

    SCANNABLE_SUFFIXES = {
        ".c",
        ".cc",
        ".cpp",
        ".cs",
        ".env",
        ".go",
        ".h",
        ".hpp",
        ".ini",
        ".java",
        ".js",
        ".json",
        ".jsx",
        ".kt",
        ".php",
        ".ps1",
        ".py",
        ".rb",
        ".rs",
        ".sh",
        ".sql",
        ".toml",
        ".ts",
        ".tsx",
        ".yaml",
        ".yml",
    }
    SCANNABLE_NAMES = {"Dockerfile", "Gemfile", "Makefile"}
    PLACEHOLDER_MARKERS = {
        "changeme",
        "change-me",
        "dummy",
        "example",
        "fake",
        "placeholder",
        "sample",
        "test",
        "your-",
        "your_",
        "xxxx",
    }

    PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
        (
            "critical",
            "private-key",
            re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
        ),
        (
            "high",
            "hardcoded-credential",
            re.compile(
                r"(?i)\b(?:api[_-]?key|password|passwd|secret|token)\s*[:=]\s*"
                r"['\"][^'\"\n]{8,}['\"]"
            ),
        ),
        (
            "high",
            "shell-injection",
            re.compile(r"\b(?:eval|exec)\s*\([^)]*(?:input|request|argv|query)"),
        ),
        (
            "high",
            "sql-injection",
            re.compile(
                r"(?i)\b(?:execute|executemany|query)\s*\(\s*"
                r"(?:f['\"]|['\"][^'\"\n]*['\"]\s*\.format\("
                r"|[^)\n]*\+\s*(?:input|request|params|query|user))"
            ),
        ),
        (
            "medium",
            "unsafe-cors",
            re.compile(r"(?i)(?:allow_origins|access-control-allow-origin).{0,20}(?:\*|all)"),
        ),
        (
            "medium",
            "weak-hash",
            re.compile(r"\b(?:hashlib\.)?(?:md5|sha1)\s*\("),
        ),
    )

    def scan(self, root: str | Path, paths: Iterable[str | Path] | None = None) -> ProbeResult:
        repository = Path(root).expanduser().resolve()
        explicit_scope = paths is not None
        candidates = (
            [repository / Path(item) for item in paths]
            if explicit_scope
            else list(repository.rglob("*"))
        )
        findings: list[dict[str, Any]] = []
        scanned = 0
        for path in candidates:
            try:
                resolved = path.resolve()
                resolved.relative_to(repository)
            except (ValueError, OSError):
                continue
            if not resolved.is_file() or any(
                part
                in {
                    ".git",
                    ".nexusai",
                    "node_modules",
                    ".venv",
                    "venv",
                    "dist",
                    "build",
                    "verification_evidence",
                }
                for part in resolved.relative_to(repository).parts
            ):
                continue
            if not explicit_scope and any(
                part in {"tests", "test", "benchmarks", "fixtures"}
                for part in resolved.relative_to(repository).parts[:-1]
            ):
                continue
            if (
                resolved.suffix.lower() not in self.SCANNABLE_SUFFIXES
                and resolved.name not in self.SCANNABLE_NAMES
            ):
                continue
            try:
                if resolved.stat().st_size > 2_000_000:
                    continue
                content = resolved.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            scanned += 1
            lines = content.splitlines()
            for severity, kind, pattern in self.PATTERNS:
                for match in pattern.finditer(content):
                    matched = match.group(0).lower()
                    if kind == "hardcoded-credential" and any(
                        marker in matched for marker in self.PLACEHOLDER_MARKERS
                    ):
                        continue
                    line_number = content.count("\n", 0, match.start()) + 1
                    surrounding = "\n".join(lines[max(0, line_number - 3) : line_number + 1])
                    if "re.compile" in surrounding:
                        continue
                    findings.append(
                        {
                            "severity": severity,
                            "kind": kind,
                            "path": resolved.relative_to(repository).as_posix(),
                            "line": line_number,
                        }
                    )
        blocking = [item for item in findings if item["severity"] in {"critical", "high"}]
        return ProbeResult(
            "security",
            ProbeStatus.FAILED if blocking else ProbeStatus.PASSED,
            (
                f"{len(blocking)} high/critical deterministic finding(s)"
                if blocking
                else "No high/critical deterministic patterns detected"
            ),
            evidence={
                "scanned_files": scanned,
                "findings": findings,
                "scope": "deterministic-pattern-scan-not-complete-audit",
            },
        )


def probe_results_json(results: Iterable[ProbeResult]) -> str:
    """Stable JSON serialization for CLI/CI outputs."""
    return json.dumps([item.to_dict() for item in results], indent=2, sort_keys=True)
