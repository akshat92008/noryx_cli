"""Content-addressed trust approval for project instructions and executable config."""

from __future__ import annotations

import difflib
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

TRUSTED_CONFIG_NAMES = {
    "NEXUS.md",
    "nexus.md",
    ".nexus.md",
    "CLAUDE.md",
    "AGENT.md",
    "AGENTS.md",
    ".mcp.json",
    "mcp_servers.json",
    "settings.json",
    "settings.local.json",
    "hooks.json",
    "plugin.json",
    ".lsp.json",
}


@dataclass
class TrustDecision:
    path: str
    digest: str
    approved: bool
    changed: bool
    diff: str = ""


class TrustStore:
    """Require approval for the exact bytes of every executable project config."""

    def __init__(self, working_dir: str):
        self.working_dir = Path(working_dir).resolve()
        self.state_dir = self.working_dir / ".nexusai"
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path = self.state_dir / "trusted-config.json"
        self.state = self._load()

    def inspect(
        self, path: str | Path, expected_digest: str | None = None, expected_content: str | None = None
    ) -> TrustDecision:
        p = Path(path).expanduser().resolve()
        if not p.is_file() and expected_digest is None:
            return TrustDecision(str(p), "", False, False, "file does not exist")
        if expected_digest is not None:
            digest = expected_digest
            new_text = expected_content or ""
        else:
            content = p.read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            new_text = content.decode("utf-8", errors="replace")

        previous = self.state.get(str(p), {})
        approved = previous.get("digest") == digest and bool(previous.get("approved"))
        old_text = previous.get("content", "")
        changed = bool(previous) and previous.get("digest") != digest
        diff = "".join(
            difflib.unified_diff(
                old_text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=f"approved/{p.name}",
                tofile=f"current/{p.name}",
            )
        )
        return TrustDecision(str(p), digest, approved, changed, diff)

    def approve(
        self, path: str | Path, expected_digest: str | None = None, expected_content: str | None = None
    ) -> TrustDecision:
        decision = self.inspect(path, expected_digest, expected_content)
        if not decision.digest:
            return decision
        p = Path(decision.path)
        self.state[decision.path] = {
            "digest": decision.digest,
            "approved": True,
            "content": expected_content if expected_content is not None else (
                p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
            ),
        }
        self._save()
        decision.approved = True
        return decision

    def reject(
        self, path: str | Path, expected_digest: str | None = None, expected_content: str | None = None
    ) -> TrustDecision:
        decision = self.inspect(path, expected_digest, expected_content)
        p = Path(decision.path)
        self.state[decision.path] = {
            "digest": decision.digest,
            "approved": False,
            "content": expected_content if expected_content is not None else (
                p.read_text(encoding="utf-8", errors="replace") if p.is_file() else ""
            ),
        }
        self._save()
        return decision

    def scan_project(self) -> list[TrustDecision]:
        candidates: set[Path] = set()
        for name in TRUSTED_CONFIG_NAMES:
            candidates.update(self.working_dir.rglob(name))
        candidates.update((self.working_dir / ".nexus" / "skills").glob("*.md"))
        candidates.update((self.working_dir / ".nexus").glob("policies.*"))
        candidates.update((self.working_dir / ".nexus").glob("config.*"))
        candidates.update((self.working_dir / ".nexus").glob("verify.json"))
        ignored = {
            ".git",
            ".nexusai",
            "node_modules",
            ".venv",
            "venv",
            "dist",
            "build",
            "__pycache__",
        }
        return [self.inspect(p) for p in sorted(candidates) if not ignored.intersection(p.parts)]

    def is_approved(
        self, path: str | Path, expected_digest: str | None = None, expected_content: str | None = None
    ) -> bool:
        return self.inspect(path, expected_digest, expected_content).approved

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}

    def _save(self):
        self.path.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
