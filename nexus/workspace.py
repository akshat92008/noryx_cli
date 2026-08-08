"""Opt-in Git worktree isolation for Nexus modifying sessions."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from nexus.paths import nexus_home


class WorktreeError(RuntimeError):
    """Raised when an isolated workspace cannot be created safely."""


@dataclass
class WorktreeInfo:
    source_repository: str
    path: str
    base_commit: str
    branch: str
    created_at: str
    backend: str = "git-worktree"
    source_was_dirty: bool = False
    source_state_hash: str = ""
    snapshot_commit: str = ""


class GitWorktreeSession:
    """Create a dedicated branch and worktree without changing the source tree."""

    def __init__(
        self,
        repository: str | Path,
        session_id: str,
        *,
        state_root: str | Path | None = None,
        force_copy: bool = False,
    ):
        self.repository = Path(repository).expanduser().resolve()
        self.session_id = session_id
        root = Path(state_root).expanduser().resolve() if state_root else nexus_home()
        try:
            root.relative_to(self.repository)
            root = Path(tempfile.gettempdir()) / "nexus-worktrees"
        except ValueError:
            pass
        self.path = root / "worktrees" / session_id
        self.info_path = root / "worktrees" / f"{session_id}.json"
        self.info: WorktreeInfo | None = None
        self.force_copy = force_copy

    def create(self) -> WorktreeInfo:
        """Create an isolated worktree, preserving the source's current state.

        Dirty repositories are snapshotted into the new branch instead of being
        rejected. The source tree is never modified. Unmerged/conflicted states
        still fail closed because they cannot be represented deterministically.
        """
        if self.path.exists():
            raise WorktreeError(f"Worktree path already exists: {self.path}")
        if self.force_copy or not (self.repository / ".git").exists():
            return self._create_temporary_copy()
        top = self._git(["rev-parse", "--show-toplevel"]).strip()
        if Path(top).resolve() != self.repository:
            raise WorktreeError(
                f"Working directory must be the Git repository root: {self.repository}"
            )

        status_bytes = self._git_bytes(["status", "--porcelain=v1", "-z"])
        status_entries = [item for item in status_bytes.split(b"\0") if item]
        if any(self._is_unmerged_status(item) for item in status_entries):
            raise WorktreeError(
                "Source repository has unresolved merge conflicts. Resolve them "
                "before creating an isolated Nexus workspace."
            )

        source_was_dirty = bool(status_entries)
        source_state_hash = self._source_state_hash() if source_was_dirty else ""
        base_commit = self._git(["rev-parse", "HEAD"]).strip()
        branch_suffix = re.sub(r"[^A-Za-z0-9._-]+", "-", self.session_id).strip("-")
        branch = f"nexus/{branch_suffix}"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "git",
            "worktree",
            "add",
            "-b",
            branch,
            str(self.path),
            base_commit,
        ]
        # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
        result = subprocess.run(
            command,
            cwd=self.repository,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise WorktreeError((result.stderr or result.stdout).strip())

        snapshot_commit = ""
        try:
            if source_was_dirty:
                self._materialize_source_snapshot()
                # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
                subprocess.run(["git", "add", "-A"], cwd=self.path, check=True)
                # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
                committed = subprocess.run(
                    [
                        "git",
                        "-c",
                        "user.name=Nexus",
                        "-c",
                        "user.email=nexus@localhost",
                        "commit",
                        "-m",
                        "Nexus source workspace snapshot",
                    ],
                    cwd=self.path,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if committed.returncode != 0:
                    raise WorktreeError((committed.stderr or committed.stdout).strip())
                # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
                snapshot_commit = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=self.path,
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip()
        except (OSError, subprocess.SubprocessError, WorktreeError) as exc:
            # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
            subprocess.run(
                ["git", "worktree", "remove", "--force", str(self.path)],
                cwd=self.repository,
                capture_output=True,
            )
            # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
            subprocess.run(
                ["git", "branch", "-D", branch],
                cwd=self.repository,
                capture_output=True,
            )
            if isinstance(exc, WorktreeError):
                raise
            raise WorktreeError(f"Could not snapshot dirty repository: {exc}") from exc

        self.info = WorktreeInfo(
            source_repository=str(self.repository),
            path=str(self.path),
            base_commit=base_commit,
            branch=branch,
            created_at=datetime.now(timezone.utc).isoformat(),
            source_was_dirty=source_was_dirty,
            source_state_hash=source_state_hash,
            snapshot_commit=snapshot_commit,
        )
        self.info_path.write_text(
            json.dumps(asdict(self.info), indent=2) + "\n",
            encoding="utf-8",
        )
        return self.info

    @staticmethod
    def _is_unmerged_status(entry: bytes) -> bool:
        code = entry[:2].decode("ascii", errors="ignore")
        return code in {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}

    def _git_bytes(self, args: list[str], *, cwd: Path | None = None) -> bytes:
        # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
        result = subprocess.run(
            ["git", *args],
            cwd=cwd or self.repository,
            capture_output=True,
            timeout=30,
        )
        if result.returncode != 0:
            raise WorktreeError(result.stderr.decode(errors="replace").strip())
        return result.stdout

    def _untracked_paths(self) -> list[Path]:
        raw = self._git_bytes(["ls-files", "--others", "--exclude-standard", "-z"])
        return [Path(os.fsdecode(item)) for item in raw.split(b"\0") if item]

    def _source_state_hash(self) -> str:
        digest = hashlib.sha256()
        digest.update(self._git_bytes(["diff", "--binary", "HEAD"]))
        digest.update(self._git_bytes(["status", "--porcelain=v1", "-z"]))
        for relative in sorted(self._untracked_paths(), key=lambda item: item.as_posix()):
            source = (self.repository / relative).resolve()
            try:
                source.relative_to(self.repository)
            except ValueError as exc:
                raise WorktreeError(f"Untracked path escapes repository: {relative}") from exc
            digest.update(relative.as_posix().encode("utf-8", errors="surrogateescape"))
            if source.is_symlink():
                digest.update(b"SYMLINK\0" + os.readlink(source).encode())
            elif source.is_file():
                digest.update(source.read_bytes())
        return digest.hexdigest()

    def _materialize_source_snapshot(self) -> None:
        patch = self._git_bytes(["diff", "--binary", "HEAD"])
        if patch:
            # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
            applied = subprocess.run(
                ["git", "apply", "--binary", "--whitespace=nowarn", "-"],
                cwd=self.path,
                input=patch,
                capture_output=True,
                timeout=30,
            )
            if applied.returncode != 0:
                raise WorktreeError(applied.stderr.decode(errors="replace").strip())
        for relative in self._untracked_paths():
            source = (self.repository / relative).resolve()
            target = (self.path / relative).resolve()
            try:
                source.relative_to(self.repository)
                target.relative_to(self.path)
            except ValueError as exc:
                raise WorktreeError(f"Untracked path escapes repository: {relative}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            if source.is_symlink():
                target.symlink_to(os.readlink(source))
            elif source.is_file():
                shutil.copy2(source, target)

    def _create_temporary_copy(self) -> WorktreeInfo:
        """Isolate a greenfield/non-Git directory with a persistent temporary copy."""
        self.path.parent.mkdir(parents=True, exist_ok=True)

        def ignore(_directory: str, names: list[str]) -> set[str]:
            return {
                name
                for name in names
                if name
                in {
                    ".git",
                    ".nexusai",
                    ".pytest_cache",
                    ".ruff_cache",
                    "__pycache__",
                    "node_modules",
                    ".venv",
                    "venv",
                    "dist",
                    "build",
                }
            }

        try:
            shutil.copytree(self.repository, self.path, ignore=ignore)
            baseline_path = Path(str(self.path) + "_baseline")
            if not baseline_path.exists():
                shutil.copytree(self.repository, baseline_path, ignore=ignore)
        except OSError as exc:
            raise WorktreeError(f"Could not create temporary isolated copy: {exc}") from exc
        self.info = WorktreeInfo(
            source_repository=str(self.repository),
            path=str(self.path),
            base_commit="",
            branch="",
            created_at=datetime.now(timezone.utc).isoformat(),
            backend="temporary-copy",
        )
        self.info_path.write_text(
            __import__("json").dumps(asdict(self.info), indent=2) + "\n",
            encoding="utf-8",
        )
        return self.info

    def status(self) -> dict[str, str]:
        """Return branch and diff status without mutating either worktree."""
        if not self.info:
            return {}
        if self.info.backend != "git-worktree":
            return {
                **asdict(self.info),
                "git_status": "Non-Git source isolated in a persistent temporary copy.",
            }
        # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
        result = subprocess.run(
            ["git", "status", "--short", "--branch"],
            cwd=self.path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return {
            **asdict(self.info),
            "git_status": (result.stdout or result.stderr).strip(),
        }

    def _git(self, args: list[str]) -> str:
        # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
        result = subprocess.run(
            ["git", *args],
            cwd=self.repository,
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode != 0:
            raise WorktreeError((result.stderr or result.stdout).strip())
        return result.stdout

    def diff(self) -> str:
        """Return the unified diff of changes in the worktree."""
        if not self.info:
            return ""
        if self.info.backend == "git-worktree":
            # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
            result = subprocess.run(
                [
                    "git",
                    "diff",
                    "--binary",
                    self.info.snapshot_commit or self.info.base_commit,
                ],
                cwd=self.path,
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                raise WorktreeError((result.stderr or result.stdout).strip())
            patches = [result.stdout]
            # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
            untracked = subprocess.run(
                ["git", "ls-files", "--others", "--exclude-standard", "-z"],
                cwd=self.path,
                capture_output=True,
                timeout=30,
            )
            if untracked.returncode != 0:
                raise WorktreeError(untracked.stderr.decode(errors="replace").strip())
            for encoded_path in untracked.stdout.split(b"\0"):
                if not encoded_path:
                    continue
                relative = os.fsdecode(encoded_path)
                # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
                addition = subprocess.run(
                    ["git", "diff", "--no-index", "--binary", "--", os.devnull, relative],
                    cwd=self.path,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                if addition.returncode not in {0, 1}:
                    raise WorktreeError((addition.stderr or addition.stdout).strip())
                patches.append(addition.stdout)
            return "".join(patches)
        else:
            # Python-native unified diff — cross-platform, no Unix diff required
            import difflib

            lines: list[str] = []
            src = self.path
            dst = self.repository
            _ignore = {
                ".git",
                ".nexusai",
                ".pytest_cache",
                "__pycache__",
                "node_modules",
                ".venv",
                "venv",
                "dist",
                "build",
            }

            def _collect_files(base: "Path") -> list["Path"]:
                out = []
                for entry in base.rglob("*"):
                    if entry.is_file() and not any(part in _ignore for part in entry.parts):
                        out.append(entry)
                return out

            src_files = {f.relative_to(src) for f in _collect_files(src)}
            dst_files = {f.relative_to(dst) for f in _collect_files(dst)}

            for rel in sorted(src_files | dst_files):
                src_f = src / rel
                dst_f = dst / rel
                src_lines = (
                    src_f.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
                    if src_f.exists()
                    else []
                )
                dst_lines = (
                    dst_f.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
                    if dst_f.exists()
                    else []
                )
                diff_lines = list(
                    difflib.unified_diff(
                        dst_lines,
                        src_lines,
                        fromfile=f"a/{rel}",
                        tofile=f"b/{rel}",
                    )
                )
                lines.extend(diff_lines)

            return "".join(lines)

    def _commit_workspace_changes(self) -> None:
        # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.path,
            capture_output=True,
            text=True,
            check=True,
        )
        if not status.stdout.strip():
            return
        # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
        subprocess.run(["git", "add", "-A"], cwd=self.path, check=True)
        # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
        committed = subprocess.run(
            [
                "git",
                "-c",
                "user.name=Nexus",
                "-c",
                "user.email=nexus@localhost",
                "commit",
                "-m",
                "Nexus workspace apply",
            ],
            cwd=self.path,
            capture_output=True,
            text=True,
        )
        if committed.returncode != 0:
            raise WorktreeError((committed.stderr or committed.stdout).strip())

    def _apply_to_dirty_source(self) -> None:
        if not self.info or not self.info.snapshot_commit:
            raise WorktreeError("Dirty workspace metadata is incomplete; refusing to apply.")
        current_hash = self._source_state_hash()
        if current_hash != self.info.source_state_hash:
            raise WorktreeError(
                "Source repository changed after the Nexus snapshot was created. "
                "Review the workspace diff and apply it manually to avoid overwriting work."
            )

        delta = self._git_bytes(
            ["diff", "--binary", self.info.snapshot_commit, "HEAD"],
            cwd=self.path,
        )
        if not delta:
            return

        recovery_dir = self.info_path.parent / "recovery"
        recovery_dir.mkdir(parents=True, exist_ok=True)
        patch_path = recovery_dir / f"{self.session_id}.patch"
        patch_path.write_bytes(delta)
        # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
        applied = subprocess.run(
            ["git", "apply", "--binary", "--whitespace=nowarn", "-"],
            cwd=self.repository,
            input=delta,
            capture_output=True,
            timeout=60,
        )
        if applied.returncode != 0:
            raise WorktreeError(
                "Could not apply Nexus changes over the preserved dirty source: "
                f"{applied.stderr.decode(errors='replace').strip()}. "
                f"Recovery patch: {patch_path}"
            )

    def apply(self) -> None:
        """Apply workspace changes back without losing concurrent user work.

        Clean repositories use a normal branch merge with a recovery ref. Dirty
        repositories use the immutable snapshot commit created at session start:
        Nexus computes only its delta from that snapshot and applies the delta to
        the unchanged source working tree. This preserves the user's staged,
        unstaged, and untracked state instead of forcing a stash or commit.
        """
        if not self.info:
            return
        if self.info.backend == "git-worktree":
            self._commit_workspace_changes()
            if self.info.source_was_dirty:
                self._apply_to_dirty_source()
                return

            # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
            dirty_now = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=self.repository,
                capture_output=True,
                text=True,
            ).stdout.strip()
            if dirty_now:
                raise WorktreeError(
                    "Source repository changed after the workspace was created. "
                    "Refusing to merge so concurrent work is not overwritten."
                )

            backup_ref = f"refs/nexus/pre-apply-{self.session_id}"
            # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
            head_sha = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.repository,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
            subprocess.run(
                ["git", "update-ref", backup_ref, head_sha],
                cwd=self.repository,
                check=True,
                capture_output=True,
            )

            # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
            result = subprocess.run(
                ["git", "merge", self.info.branch, "--no-edit"],
                cwd=self.repository,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
                subprocess.run(
                    ["git", "merge", "--abort"],
                    cwd=self.repository,
                    capture_output=True,
                )
                raise WorktreeError(
                    f"Failed to merge branch '{self.info.branch}': "
                    f"{result.stderr or result.stdout}\n"
                    "The source repository has been automatically restored. "
                    f"Backup ref is available at {backup_ref}."
                )
        else:
            # ── Python-native sync (cross-platform, no rsync required) ────
            import shutil as _shutil

            src = self.path
            dst = self.repository
            baseline = Path(str(self.path) + "_baseline")
            backup = Path(str(self.path) + "_backup")

            _ignore = {
                ".git",
                ".nexusai",
                ".pytest_cache",
                ".ruff_cache",
                "__pycache__",
                "node_modules",
                ".venv",
                "venv",
                "dist",
                "build",
            }

            def _collect_files(base: "Path") -> list["Path"]:
                out = []
                for entry in base.rglob("*"):
                    if entry.is_file() and not any(part in _ignore for part in entry.parts):
                        out.append(entry)
                return out

            def _check_concurrent_changes(base: Path, current: Path) -> bool:
                if not base.exists() or not current.exists():
                    return False
                base_files = {f.relative_to(base): f for f in _collect_files(base)}
                curr_files = {f.relative_to(current): f for f in _collect_files(current)}
                if set(base_files.keys()) != set(curr_files.keys()):
                    return True
                for rel in base_files:
                    try:
                        if base_files[rel].read_bytes() != curr_files[rel].read_bytes():
                            return True
                    except OSError:
                        return True
                return False

            if baseline.exists() and _check_concurrent_changes(baseline, dst):
                raise WorktreeError(
                    "Concurrent changes detected in source repository. "
                    "Refusing to apply non-Git temporary copy to prevent data loss."
                )

            if not backup.exists():
                try:

                    def _ignore_func(_d: str, names: list[str]) -> set[str]:
                        return {n for n in names if n in _ignore}

                    _shutil.copytree(dst, backup, ignore=_ignore_func)
                except OSError as exc:
                    raise WorktreeError(
                        f"Could not create backup of source repository: {exc}"
                    ) from exc

            def _sync(src_dir: "Path", dst_dir: "Path") -> None:
                dst_dir.mkdir(parents=True, exist_ok=True)
                src_names = {e.name for e in src_dir.iterdir()}
                for entry in src_dir.iterdir():
                    if entry.name in _ignore:
                        continue
                    dst_entry = dst_dir / entry.name
                    if entry.is_dir():
                        _sync(entry, dst_entry)
                    else:
                        _shutil.copy2(str(entry), str(dst_entry))
                # Remove files in dst that are absent in src (mirrors rsync --delete)
                if dst_dir.exists():
                    for entry in dst_dir.iterdir():
                        if entry.name not in src_names and entry.name not in _ignore:
                            if entry.is_dir():
                                _shutil.rmtree(entry, ignore_errors=True)
                            else:
                                entry.unlink(missing_ok=True)

            try:
                _sync(src, dst)
            except OSError as exc:
                rollback_error = ""
                try:
                    _sync(backup, dst)
                except OSError as rollback_exc:
                    rollback_error = f" Rollback also failed: {rollback_exc}."
                detail = (
                    "Source was restored from the pre-apply backup."
                    if not rollback_error
                    else f"Recovery copy remains at {backup}.{rollback_error}"
                )
                raise WorktreeError(f"Failed to sync temporary copy: {exc}. {detail}") from exc

    def discard(self) -> dict[str, object]:
        """Discard an isolated workspace and report every cleanup result."""

        report: dict[str, object] = {"removed": [], "errors": []}
        if not self.info:
            return report
        if self.info.backend == "git-worktree":
            # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
            removed = subprocess.run(
                ["git", "worktree", "remove", "--force", self.path],
                cwd=self.repository,
                capture_output=True,
                text=True,
            )
            if removed.returncode == 0:
                report["removed"].append(str(self.path))
            else:
                report["errors"].append((removed.stderr or removed.stdout).strip())
            # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
            branch = subprocess.run(
                ["git", "branch", "-D", self.info.branch],
                cwd=self.repository,
                capture_output=True,
                text=True,
            )
            if branch.returncode == 0:
                report["removed"].append(self.info.branch)
            else:
                report["errors"].append((branch.stderr or branch.stdout).strip())
        else:
            for target in (Path(self.path), Path(str(self.path) + "_baseline")):
                if not target.exists():
                    continue
                try:
                    shutil.rmtree(target)
                    report["removed"].append(str(target))
                except OSError as exc:
                    report["errors"].append(f"{target}: {exc}")
            # We explicitly keep the backup directory in case the user needs it
        if not report["errors"]:
            try:
                self.info_path.unlink(missing_ok=True)
                report["removed"].append(str(self.info_path))
                self.info = None
            except OSError as exc:
                report["errors"].append(f"{self.info_path}: {exc}")
        return report


class WorkspaceManager:
    """Manages global isolation sessions for Nexus."""

    def __init__(self, state_root: str | Path | None = None):
        self.state_root = Path(state_root).expanduser().resolve() if state_root else nexus_home()
        self.worktrees_dir = self.state_root / "worktrees"

    def list_worktrees(self) -> list[WorktreeInfo]:
        """List all active isolated worktrees."""
        if not self.worktrees_dir.exists():
            return []

        results = []
        for p in self.worktrees_dir.glob("*.json"):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                results.append(WorktreeInfo(**data))
            except (TypeError, ValueError):
                pass

        # Sort by creation time descending
        results.sort(key=lambda w: w.created_at, reverse=True)
        return results

    def resolve_worktree(self, session_id: str) -> GitWorktreeSession | None:
        """Resolve a specific session ID to an active worktree session."""
        info_path = self.worktrees_dir / f"{session_id}.json"
        if not info_path.exists():
            return None

        try:
            data = json.loads(info_path.read_text(encoding="utf-8"))
            info = WorktreeInfo(**data)
            session = GitWorktreeSession(
                info.source_repository,
                session_id,
                state_root=self.state_root,
                force_copy=info.backend == "temporary-copy",
            )
            session.info = info
            session.path = Path(info.path)
            return session
        except (TypeError, ValueError):
            return None
