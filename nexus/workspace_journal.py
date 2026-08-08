"""Content-addressed workspace transaction journal.

Shell commands can mutate files outside the explicit file-tool path.  This
module snapshots regular files and symlinks before execution, stores immutable
preimages, and computes a digest-based union diff afterwards.  It deliberately
fails closed when configured safety budgets are exceeded.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class WorkspaceSnapshotError(RuntimeError):
    """Raised when a complete, trustworthy snapshot cannot be produced."""


@dataclass(frozen=True)
class WorkspaceFileState:
    relative_path: str
    kind: str
    sha256: str
    size: int
    mode: int
    preimage_path: str = ""
    link_target: str = ""


@dataclass(frozen=True)
class WorkspaceSnapshot:
    root: str
    entries: dict[str, WorkspaceFileState]
    total_bytes: int


@dataclass(frozen=True)
class WorkspaceMutation:
    relative_path: str
    change_type: str
    before: WorkspaceFileState | None
    after: WorkspaceFileState | None


class ContentAddressedWorkspaceJournal:
    """Capture and reconcile workspace state with immutable preimages."""

    DEFAULT_IGNORED_PARTS = frozenset(
        {
            ".git",
            ".nexus",
            ".nexusai",
            "node_modules",
            "venv",
            ".venv",
            "__pycache__",
            ".pytest_cache",
            ".mypy_cache",
            ".ruff_cache",
        }
    )

    def __init__(
        self,
        root: str | Path,
        *,
        preimage_dir: str | Path,
        max_files: int | None = None,
        max_bytes: int | None = None,
        ignored_parts: Iterable[str] | None = None,
        excluded_roots: Iterable[str | Path] | None = None,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.preimage_dir = Path(preimage_dir).expanduser().resolve()
        self.max_files = max_files or int(
            os.environ.get("NEXUS_COMMAND_SNAPSHOT_MAX_FILES", "20000")
        )
        self.max_bytes = max_bytes or int(
            os.environ.get("NEXUS_COMMAND_SNAPSHOT_MAX_BYTES", str(512 * 1024 * 1024))
        )
        self.ignored_parts = frozenset(ignored_parts or self.DEFAULT_IGNORED_PARTS)
        candidates = [self.preimage_dir, *(excluded_roots or ())]
        self.excluded_roots = tuple(
            resolved
            for raw in candidates
            if (resolved := Path(raw).expanduser().resolve()) != self.root
            and self._is_within(resolved, self.root)
        )
        if not self.root.is_dir():
            raise WorkspaceSnapshotError(f"Workspace does not exist: {self.root}")
        self.preimage_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _digest_bytes(data: bytes) -> str:
        return hashlib.sha256(data).hexdigest()

    @staticmethod
    def _digest_file(path: Path) -> tuple[str, int]:
        digest = hashlib.sha256()
        total = 0
        with path.open("rb") as handle:
            while True:
                chunk = handle.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
                total += len(chunk)
        return digest.hexdigest(), total

    @staticmethod
    def _is_within(path: Path, parent: Path) -> bool:
        try:
            path.relative_to(parent)
            return True
        except ValueError:
            return False

    def _is_excluded(self, path: Path) -> bool:
        resolved = path.resolve(strict=False)
        return any(
            resolved == excluded or self._is_within(resolved, excluded)
            for excluded in self.excluded_roots
        )

    def _is_ignored(self, path: Path) -> bool:
        try:
            relative = path.relative_to(self.root)
        except ValueError:
            return True
        return self._is_excluded(path) or any(
            part in self.ignored_parts for part in relative.parts
        )

    def _store_preimage(self, source: Path, digest: str) -> str:
        destination = self.preimage_dir / digest[:2] / digest
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            # The digest is encoded in the immutable filename.  Restoration
            # verifies bytes against the recorded digest before claiming success.
            if not destination.is_file():
                raise WorkspaceSnapshotError(
                    f"Invalid preimage store entry for digest {digest}"
                )
            return str(destination)
        temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
        try:
            shutil.copyfile(source, temporary)
            copied_digest, _ = self._digest_file(temporary)
            if copied_digest != digest:
                raise WorkspaceSnapshotError(
                    f"File changed while snapshotting: {source}"
                )
            os.replace(temporary, destination)
            try:
                destination.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            except OSError:
                pass
            return str(destination)
        finally:
            temporary.unlink(missing_ok=True)

    def capture(self, *, store_preimages: bool) -> WorkspaceSnapshot:
        entries: dict[str, WorkspaceFileState] = {}
        total_bytes = 0
        count = 0

        def candidates():
            def fail_walk(error: OSError) -> None:
                raise WorkspaceSnapshotError(
                    f"Unable to enumerate workspace: {error}"
                ) from error

            try:
                for directory, dirnames, filenames in os.walk(
                    self.root,
                    topdown=True,
                    onerror=fail_walk,
                    followlinks=False,
                ):
                    base = Path(directory)
                    dirnames[:] = sorted(
                        name
                        for name in dirnames
                        if name not in self.ignored_parts
                        and not self._is_excluded(base / name)
                    )
                    for name in sorted(dirnames + filenames):
                        path = base / name
                        if path.is_symlink() or path.is_file():
                            yield path
            except OSError as exc:
                raise WorkspaceSnapshotError(
                    f"Unable to enumerate workspace: {exc}"
                ) from exc

        for path in candidates():
            if self._is_ignored(path):
                continue
            try:
                relative = path.relative_to(self.root).as_posix()
                if path.is_symlink():
                    target = os.readlink(path)
                    payload = target.encode("utf-8", errors="surrogateescape")
                    state = WorkspaceFileState(
                        relative_path=relative,
                        kind="symlink",
                        sha256=self._digest_bytes(payload),
                        size=len(payload),
                        mode=path.lstat().st_mode & 0o7777,
                        link_target=target,
                    )
                elif path.is_file():
                    before = path.stat()
                    digest, size = self._digest_file(path)
                    after = path.stat()
                    if (
                        before.st_size != after.st_size
                        or before.st_mtime_ns != after.st_mtime_ns
                        or before.st_ino != after.st_ino
                    ):
                        digest, size = self._digest_file(path)
                        stable = path.stat()
                        if stable.st_size != size:
                            raise WorkspaceSnapshotError(
                                f"File remained unstable while snapshotting: {path}"
                            )
                    preimage = self._store_preimage(path, digest) if store_preimages else ""
                    state = WorkspaceFileState(
                        relative_path=relative,
                        kind="file",
                        sha256=digest,
                        size=size,
                        mode=after.st_mode & 0o7777,
                        preimage_path=preimage,
                    )
                else:
                    continue
            except (OSError, UnicodeError) as exc:
                raise WorkspaceSnapshotError(f"Unable to snapshot {path}: {exc}") from exc

            count += 1
            total_bytes += state.size
            if count > self.max_files:
                raise WorkspaceSnapshotError(
                    f"Workspace snapshot exceeds {self.max_files} entries"
                )
            if total_bytes > self.max_bytes:
                raise WorkspaceSnapshotError(
                    f"Workspace snapshot exceeds {self.max_bytes} bytes"
                )
            entries[relative] = state

        return WorkspaceSnapshot(str(self.root), entries, total_bytes)

    @staticmethod
    def _remove_path(path: Path) -> None:
        """Remove a file, symlink, or directory without following symlinks."""
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)

    def _validate_restore_preimages(self, snapshot: WorkspaceSnapshot) -> None:
        """Validate every file preimage before making any destructive change."""
        for relative, state in snapshot.entries.items():
            if state.kind != "file":
                continue
            if not state.preimage_path:
                raise WorkspaceSnapshotError(f"Missing preimage for {relative}")
            preimage = Path(state.preimage_path)
            if not preimage.is_file():
                raise WorkspaceSnapshotError(f"Missing preimage for {relative}")
            try:
                digest, size = self._digest_file(preimage)
            except OSError as exc:
                raise WorkspaceSnapshotError(
                    f"Unable to validate preimage for {relative}: {exc}"
                ) from exc
            if digest != state.sha256 or size != state.size:
                raise WorkspaceSnapshotError(
                    f"Corrupt preimage for {relative}: expected {state.sha256}, got {digest}"
                )

    def restore(self, snapshot: WorkspaceSnapshot) -> None:
        """Restore a complete preimage snapshot and verify the resulting tree."""
        if Path(snapshot.root).resolve() != self.root:
            raise WorkspaceSnapshotError("Snapshot root does not match journal root")

        # Preflight the entire restore set before deleting or overwriting anything.
        self._validate_restore_preimages(snapshot)
        current = self.capture(store_preimages=False)
        for relative in sorted(set(current.entries) - set(snapshot.entries), reverse=True):
            target = self.root / relative
            try:
                self._remove_path(target)
            except OSError as exc:
                raise WorkspaceSnapshotError(
                    f"Unable to remove created path {target}: {exc}"
                ) from exc

        for relative, state in snapshot.entries.items():
            target = self.root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                self._remove_path(target)
                if state.kind == "symlink":
                    target.symlink_to(state.link_target)
                else:
                    if not state.preimage_path or not Path(state.preimage_path).is_file():
                        raise WorkspaceSnapshotError(f"Missing preimage for {relative}")
                    shutil.copyfile(state.preimage_path, target)
                    os.chmod(target, state.mode)
            except OSError as exc:
                raise WorkspaceSnapshotError(f"Unable to restore {target}: {exc}") from exc

        restored = self.capture(store_preimages=False)
        remaining = self.diff(snapshot, restored)
        if remaining:
            paths = ", ".join(item.relative_path for item in remaining[:10])
            raise WorkspaceSnapshotError(f"Rollback verification failed for: {paths}")

    @staticmethod
    def diff(
        before: WorkspaceSnapshot,
        after: WorkspaceSnapshot,
    ) -> list[WorkspaceMutation]:
        mutations: list[WorkspaceMutation] = []
        for relative in sorted(set(before.entries) | set(after.entries)):
            old = before.entries.get(relative)
            new = after.entries.get(relative)
            if old is None:
                mutations.append(WorkspaceMutation(relative, "created", None, new))
            elif new is None:
                mutations.append(WorkspaceMutation(relative, "deleted", old, None))
            elif (
                old.kind != new.kind
                or old.sha256 != new.sha256
                or old.mode != new.mode
                or old.link_target != new.link_target
            ):
                mutations.append(WorkspaceMutation(relative, "modified", old, new))
        return mutations
