"""Registry-backed dependency validation to block hallucinated package names."""

from __future__ import annotations

import json
import re
import shlex
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

DEPENDENCY_FILES = {
    "requirements.txt": "pypi",
    "requirements-dev.txt": "pypi",
    "package.json": "npm",
    "Cargo.toml": "crates",
    "go.mod": "go",
}


@dataclass
class PackageCheck:
    name: str
    registry: str
    status: str
    reason: str
    url: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def blocked(self) -> bool:
        return self.status == "blocked"

    @property
    def requires_confirmation(self) -> bool:
        """Registry outages are uncertainty, not proof that a package is malicious."""
        return self.status == "unverified"


class PackageGuard:
    """Extract proposed dependencies and prove they exist in their real registry."""

    def __init__(self, resolver: Callable[[str, str], PackageCheck] | None = None):
        self.resolver = resolver or self._lookup
        self._cache: dict[tuple[str, str], PackageCheck] = {}

    def check_file_change(
        self,
        path: str,
        content: str,
        current_content: str | None = None,
    ) -> list[PackageCheck]:
        """Validate only newly introduced dependency coordinates.

        Existing private or self-hosted dependencies are preserved without
        forcing a public-registry lookup.  Changed package names are treated as
        newly introduced; version-only changes keep the same package identity.
        """
        p = Path(path)
        registry = DEPENDENCY_FILES.get(p.name)
        if not registry:
            return []
        proposed = set(self._extract_file_packages(p.name, content))
        current = set(
            self._extract_file_packages(p.name, current_content or "")
        )
        return self._check_names(registry, sorted(proposed - current))

    def check_command(self, command: str) -> list[PackageCheck]:
        try:
            parts = shlex.split(command)
        except ValueError:
            return [
                PackageCheck(
                    "(command)", "unknown", "unverified", "install command could not be parsed"
                )
            ]
        if not parts:
            return []

        registry = ""
        names: list[str] = []
        executable = Path(parts[0]).name.lower()
        if executable in {"pip", "pip3"} and "install" in parts:
            registry = "pypi"
            names = self._positional_after(parts, "install")
        elif executable == "uv" and len(parts) > 2 and parts[1] == "pip" and "install" in parts:
            registry = "pypi"
            names = self._positional_after(parts, "install")
        elif (
            executable in {"python", "python3"}
            and len(parts) > 3
            and parts[1:3] == ["-m", "pip"]
            and "install" in parts
        ):
            registry = "pypi"
            names = self._positional_after(parts, "install")
        elif executable in {"npm", "pnpm", "yarn"} and any(
            x in parts for x in ("install", "add", "i")
        ):
            registry = "npm"
            verb = next(x for x in ("install", "add", "i") if x in parts)
            names = self._positional_after(parts, verb)
        elif executable == "cargo" and "add" in parts:
            registry = "crates"
            names = self._positional_after(parts, "add")
        elif executable == "go" and "get" in parts:
            registry = "go"
            names = self._positional_after(parts, "get")
        if not registry:
            return []

        cleaned = []
        for name in names:
            if name in {"&&", "||", ";", "|"}:
                break
            if self._is_local_or_url(name):
                continue
            cleaned.append(self._strip_version(name, registry))
        return self._check_names(registry, cleaned)

    def _check_names(self, registry: str, names: list[str]) -> list[PackageCheck]:
        checks = []
        for name in sorted(set(filter(None, names))):
            key = (registry, name.lower())
            if key not in self._cache:
                self._cache[key] = self.resolver(registry, name)
            checks.append(self._cache[key])
        return checks

    def _extract_file_packages(self, filename: str, content: str) -> list[str]:
        if filename.startswith("requirements"):
            names = []
            for line in content.splitlines():
                line = line.strip()
                if not line or line.startswith(("#", "-", ".", "/")) or "://" in line:
                    continue
                names.append(re.split(r"[<>=!~;\[]", line, maxsplit=1)[0].strip())
            return names
        if filename == "package.json":
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                return []
            names = []
            for key in (
                "dependencies",
                "devDependencies",
                "peerDependencies",
                "optionalDependencies",
            ):
                names.extend((data.get(key) or {}).keys())
            return names
        if filename == "Cargo.toml":
            names, in_dependencies = [], False
            for line in content.splitlines():
                stripped = line.strip()
                if stripped.startswith("["):
                    in_dependencies = "dependencies" in stripped and not stripped.startswith(
                        "[package"
                    )
                elif in_dependencies and "=" in stripped and not stripped.startswith("#"):
                    names.append(stripped.split("=", 1)[0].strip())
            return names
        if filename == "go.mod":
            names, in_require = [], False
            for line in content.splitlines():
                stripped = line.strip()
                if stripped == "require (":
                    in_require = True
                elif in_require and stripped == ")":
                    in_require = False
                elif stripped.startswith("require "):
                    names.append(stripped.split()[1])
                elif in_require and stripped and not stripped.startswith("//"):
                    names.append(stripped.split()[0])
            return names
        return []

    @staticmethod
    def _positional_after(parts: list[str], verb: str) -> list[str]:
        result = []
        skip_next = False
        value_flags = {
            "-r",
            "--requirement",
            "-i",
            "--index-url",
            "--extra-index-url",
            "--registry",
            "-F",
            "--features",
        }
        for item in parts[parts.index(verb) + 1 :]:
            if skip_next:
                skip_next = False
                continue
            if item in value_flags:
                skip_next = True
                continue
            if item.startswith("-"):
                continue
            result.append(item)
        return result

    @staticmethod
    def _is_local_or_url(name: str) -> bool:
        return name.startswith((".", "/", "git+", "http://", "https://", "file:")) or name == "-"

    @staticmethod
    def _strip_version(name: str, registry: str) -> str:
        if registry == "npm":
            if name.startswith("@"):
                second_at = name.find("@", 1)
                return name[:second_at] if second_at > 0 else name
            return name.split("@", 1)[0]
        if registry == "go":
            return name.rsplit("@", 1)[0]
        return re.split(r"[<>=!~\[]", name, maxsplit=1)[0]

    def _lookup(self, registry: str, name: str) -> PackageCheck:
        quoted = urllib.parse.quote(name, safe="@/")
        urls = {
            "pypi": f"https://pypi.org/pypi/{quoted}/json",
            "npm": f"https://registry.npmjs.org/{quoted}",
            "crates": f"https://crates.io/api/v1/crates/{quoted}",
            "go": f"https://proxy.golang.org/{quoted}/@v/list",
        }
        url = urls[registry]
        request = urllib.request.Request(url, headers={"User-Agent": "NexusAI-PackageGuard/1.0"})
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                raw = response.read(1_000_000)
            metadata = {} if registry == "go" else json.loads(raw.decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return PackageCheck(
                    name, registry, "blocked", "package does not exist in the registry", url
                )
            return PackageCheck(
                name, registry, "unverified", f"registry returned HTTP {exc.code}", url
            )
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            return PackageCheck(
                name, registry, "unverified", f"registry could not be verified: {exc}", url
            )

        warning = self._risk_warning(registry, metadata)
        if warning:
            return PackageCheck(
                name, registry, "warn", warning, url, metadata={"checked_at": time.time()}
            )
        return PackageCheck(name, registry, "pass", "package exists in the registry", url)

    @staticmethod
    def _risk_warning(registry: str, metadata: dict) -> str:
        now = datetime.now(timezone.utc)
        try:
            if registry == "pypi":
                releases = [
                    item for values in metadata.get("releases", {}).values() for item in values
                ]
                dates = [
                    datetime.fromisoformat(item["upload_time_iso_8601"].replace("Z", "+00:00"))
                    for item in releases
                    if item.get("upload_time_iso_8601")
                ]
                if dates and (now - min(dates)).days < 30:
                    return "package exists but was first published less than 30 days ago"
            elif registry == "npm":
                created = metadata.get("time", {}).get("created")
                if (
                    created
                    and (now - datetime.fromisoformat(created.replace("Z", "+00:00"))).days < 30
                ):
                    return "package exists but was first published less than 30 days ago"
            elif registry == "crates":
                crate = metadata.get("crate", {})
                created = crate.get("created_at")
                if (
                    created
                    and (now - datetime.fromisoformat(created.replace("Z", "+00:00"))).days < 30
                ):
                    return "crate exists but was first published less than 30 days ago"
                if int(crate.get("downloads", 0)) < 100:
                    return "crate exists but has fewer than 100 downloads"
        except (TypeError, ValueError, KeyError):
            return ""
        return ""
