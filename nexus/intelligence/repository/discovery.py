"""Deterministic repository discovery module — Sprint 5."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Iterable

IGNORED_DIRECTORIES = {
    ".git",
    ".hg",
    ".svn",
    ".nexusai",
    ".nexus",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "vendor",
    "coverage",
    "verification_evidence",
    ".venv",
    "venv",
    "build_env",
    "site-packages",
    ".eggs",
    "eggs",
    "Library",
    "Applications",
    "Pictures",
    "Music",
    "Movies",
    "Downloads",
    "System",
    "Volumes",
    ".Trash",
    ".DocumentRevisions-V100",
}

SUPPORTED_EXTENSIONS = {
    ".py", ".pyi", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx",
    ".go", ".rs", ".java", ".kt", ".kts", ".rb", ".php", ".cs",
    ".swift", ".sql", ".graphql", ".prisma", ".json", ".toml",
    ".yaml", ".yml", ".xml", ".sh", ".bash", ".md"
}


class RepositoryDiscovery:
    """Discovers project structure, roots, manifests, and files."""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        if not self.root.exists():
            raise ValueError(f"Root path does not exist: {self.root}")

    def find_git_root(self) -> Path | None:
        """Find the enclosing Git root if present."""
        current = self.root
        while current != current.parent:
            if (current / ".git").exists():
                return current
            current = current.parent
        return None

    def detect_ecosystems(self) -> list[str]:
        """Detect language ecosystems present in the repository."""
        ecosystems = []
        if (self.root / "pyproject.toml").exists() or (self.root / "setup.py").exists() or (self.root / "requirements.txt").exists() or (self.root / "uv.lock").exists():
            ecosystems.append("python")
        if (self.root / "package.json").exists() or (self.root / "tsconfig.json").exists():
            ecosystems.append("javascript/typescript")
        if (self.root / "go.mod").exists():
            ecosystems.append("go")
        if (self.root / "Cargo.toml").exists():
            ecosystems.append("rust")
        if (self.root / "pom.xml").exists() or (self.root / "build.gradle").exists():
            ecosystems.append("java")
        return ecosystems

    def discover_files(self, max_files: int = 10000) -> list[Path]:
        """Discover all valid source and project files within the repository."""
        discovered: list[Path] = []
        scanned_count = 0

        for root_dir, dirs, files in os.walk(self.root):
            # Exclude ignored directories in-place
            dirs[:] = [
                d for d in dirs
                if d not in IGNORED_DIRECTORIES
                and not d.startswith(".venv")
                and (not d.startswith(".") or d in {".github", ".vscode", ".nexus"})
            ]

            for filename in sorted(files):
                scanned_count += 1
                if scanned_count > 50000 or len(discovered) >= max_files:
                    break

                if filename in IGNORED_DIRECTORIES or filename.startswith(".#"):
                    continue

                filepath = Path(root_dir) / filename
                if filepath.is_symlink() or not filepath.is_file():
                    continue

                # Exclude paths escaping repository root via symlink or canonical resolution
                try:
                    filepath.resolve().relative_to(self.root)
                except ValueError:
                    continue

                if filepath.suffix.lower() in SUPPORTED_EXTENSIONS or filename.lower() in {
                    "makefile", "dockerfile", "codeowners", ".gitignore", ".env.example"
                }:
                    discovered.append(filepath)

            if scanned_count > 50000 or len(discovered) >= max_files:
                break

        return sorted(discovered)

    @staticmethod
    def calculate_file_hash(filepath: Path) -> str:
        """Hash actual bytes using a stable read, never metadata alone."""
        path = Path(filepath)
        for _attempt in range(3):
            before = path.stat()
            data = path.read_bytes()
            after = path.stat()
            if (
                before.st_mtime_ns == after.st_mtime_ns
                and before.st_size == after.st_size
                and len(data) == after.st_size
            ):
                return hashlib.sha256(data).hexdigest()
        raise OSError(f"File changed repeatedly while hashing: {path}")

    def calculate_tree_hash(self, files: Iterable[Path]) -> str:
        """Calculate an immutable repository snapshot hash from paths and bytes."""
        hasher = hashlib.sha256()
        for filepath in sorted(files):
            try:
                rel_path = filepath.relative_to(self.root).as_posix()
                content_hash = self.calculate_file_hash(filepath)
                hasher.update(rel_path.encode("utf-8"))
                hasher.update(b"\0")
                hasher.update(content_hash.encode("ascii"))
                hasher.update(b"\n")
            except (ValueError, OSError):
                continue
        return hasher.hexdigest()
