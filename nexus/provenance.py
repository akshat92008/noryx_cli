"""Deterministic source and build provenance helpers for Nexus releases."""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

_IGNORED_PARTS = {
    ".git",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "dist",
    "release_evidence",
    "verification_evidence",
    "runs",
    ".nexus",
    ".nexusai",
}
_IGNORED_SUFFIXES = {".pyc", ".pyo", ".whl", ".zip", ".gz"}


def source_tree_sha256(root: str | Path) -> str:
    """Hash source inputs while excluding generated/runtime state."""

    base = Path(root).expanduser().resolve()
    digest = hashlib.sha256()
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(base)
        if any(part in _IGNORED_PARTS or part.endswith(".egg-info") for part in relative.parts):
            continue
        if path.suffix in _IGNORED_SUFFIXES or path.name.startswith(".coverage"):
            continue
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def sha256_file(path: str | Path) -> str:
    target = Path(path)
    return hashlib.sha256(target.read_bytes()).hexdigest() if target.is_file() else ""


def _git(root: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", *args],
        cwd=root,
        text=True,
        stderr=subprocess.DEVNULL,
    ).strip()


@dataclass(frozen=True)
class SourceIdentity:
    revision: str
    commit: str
    dirty: bool | None
    source_tree_sha256: str
    dependency_lock: str
    dependency_lock_sha256: str
    ci_run_id: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def resolve_source_identity(root: str | Path) -> SourceIdentity:
    """Bind a qualification result to Git when available, otherwise to source bytes."""

    base = Path(root).expanduser().resolve()
    tree_hash = source_tree_sha256(base)
    commit = ""
    dirty: bool | None = None
    try:
        commit = _git(base, "rev-parse", "HEAD")
        status = subprocess.check_output(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=base,
            text=True,
            stderr=subprocess.DEVNULL,
        )
        dirty = bool(status.strip())
    except (OSError, subprocess.CalledProcessError):
        pass

    revision = f"git:{commit}" if commit else f"archive:{tree_hash}"
    lock_path = next(
        (candidate for candidate in (base / "release-constraints.txt", base / "qualification-lock.txt", base / "requirements.txt") if candidate.is_file()),
        None,
    )
    return SourceIdentity(
        revision=revision,
        commit=commit,
        dirty=dirty,
        source_tree_sha256=tree_hash,
        dependency_lock=lock_path.name if lock_path else "",
        dependency_lock_sha256=sha256_file(lock_path) if lock_path else "",
        ci_run_id=(os.environ.get("GITHUB_RUN_ID") or os.environ.get("CI_PIPELINE_ID") or "").strip(),
    )
