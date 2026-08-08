"""Immutable content-addressed workspace snapshots."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

from nexus.intelligence.repository.discovery import RepositoryDiscovery
from nexus.paths import nexus_home


@dataclass(frozen=True)
class WorkspaceSnapshot:
    root: str
    revision: str
    files: dict[str, str]

    @classmethod
    def capture(
        cls, root: str | Path, *, paths: Iterable[str | Path] | None = None
    ) -> "WorkspaceSnapshot":
        repository_root = Path(root).expanduser().resolve()
        discovery = RepositoryDiscovery(repository_root)
        if paths is None:
            files = discovery.discover_files()
        else:
            files = []
            for raw in paths:
                path = Path(raw)
                if not path.is_absolute():
                    path = repository_root / path
                path = path.resolve(strict=False)
                try:
                    path.relative_to(repository_root)
                except ValueError:
                    continue
                if path.is_file():
                    files.append(path)

        # Nexus operational state is not repository truth.  Test and local
        # deployments may deliberately place NEXUS_HOME beneath the workspace;
        # exclude that exact directory so evidence/log writes cannot invalidate
        # a source revision after a successful check.
        state_root = nexus_home().expanduser().resolve(strict=False)
        filtered: list[Path] = []
        for path in files:
            resolved = path.resolve(strict=False)
            try:
                resolved.relative_to(state_root)
            except ValueError:
                filtered.append(path)
        files = filtered
        hashes: dict[str, str] = {}
        for path in sorted(set(files)):
            try:
                rel = path.relative_to(repository_root).as_posix()
                hashes[rel] = discovery.calculate_file_hash(path)
            except (OSError, ValueError):
                continue
        revision = discovery.calculate_tree_hash(
            [repository_root / path for path in hashes]
        )
        return cls(str(repository_root), revision, hashes)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def changed_paths(self, newer: "WorkspaceSnapshot") -> list[str]:
        paths = set(self.files) | set(newer.files)
        return sorted(path for path in paths if self.files.get(path) != newer.files.get(path))


def workspace_revision(root: str | Path) -> str:
    return WorkspaceSnapshot.capture(root).revision
