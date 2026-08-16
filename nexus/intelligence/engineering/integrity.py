"""Authenticated integrity primitives for persistent engineering state.

Repository-local state is writable by the task process.  Noryx therefore stores the
signing key outside the repository (or accepts it from the environment) and protects
state with HMAC-SHA256 rather than an adjacent, unkeyed checksum.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_ENV_KEYS = ("NORYX_STATE_HMAC_KEY", "NEXUS_STATE_HMAC_KEY")


def _canonical_bytes(payload: Any) -> bytes:
    import json

    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _repository_id(repository_root: Path) -> str:
    return hashlib.sha256(str(repository_root.resolve()).encode("utf-8")).hexdigest()[:24]


def _user_home() -> Path:
    """Return the configured user home consistently across supported platforms.

    ``pathlib.Path.home()`` follows ``USERPROFILE`` on Windows and therefore
    ignores a deliberately overridden ``HOME``.  Noryx respects ``HOME`` when
    it is explicitly present so isolated CI, containers, and embedders can
    redirect external signing material without placing it in the repository.
    The repository-boundary check below still rejects any home that resolves
    inside the workspace.
    """

    configured = os.environ.get("HOME", "").strip()
    return Path(configured).expanduser() if configured else Path.home()


def _load_or_create_key(repository_root: Path) -> bytes:
    configured = next(
        (os.environ.get(name, "") for name in _ENV_KEYS if os.environ.get(name, "")),
        "",
    ).encode("utf-8")
    if configured:
        return hashlib.sha256(configured).digest()

    # Keep signing material outside the editable repository and outside
    # NORYX_HOME, because embedders commonly place NORYX_HOME inside a
    # workspace for portable state.  A repository-local key would allow the
    # task process to rewrite both state and signature.
    uid = getattr(os, "getuid", lambda: 0)()
    configured_dir = os.environ.get("NORYX_STATE_KEY_DIR", "").strip()
    candidates = [
        Path(configured_dir).expanduser() if configured_dir else None,
        _user_home() / ".noryx" / "state-keys",
        Path.cwd() / ".noryx" / "state-keys",
        Path(tempfile.gettempdir()) / f".noryx-state-keys-{uid}",
    ]
    key_dir: Path | None = None
    root = repository_root.resolve()
    for candidate in candidates:
        if candidate is None:
            continue
        resolved = candidate.expanduser().resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            pass
        else:
            continue
        try:
            resolved.mkdir(parents=True, exist_ok=True)
            probe = resolved / f".write-probe-{os.getpid()}"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError:
            continue
        key_dir = resolved
        break
    if key_dir is None:
        raise RuntimeError(
            "Authenticated state needs a writable external key directory; set "
            "NORYX_STATE_KEY_DIR or NORYX_STATE_HMAC_KEY"
        )
    try:
        os.chmod(key_dir, 0o700)
    except OSError:
        pass
    key_path = key_dir / f"{_repository_id(repository_root)}.key"
    try:
        existing = key_path.read_bytes()
    except FileNotFoundError:
        pass
    else:
        if len(existing) != 32:
            raise RuntimeError(f"Authenticated-state key has invalid length: {key_path}") from None
        try:
            os.chmod(key_path, 0o600)
        except OSError:
            pass
        return existing

    key = os.urandom(32)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    try:
        descriptor = os.open(key_path, flags, 0o600)
    except FileExistsError as exc:
        existing = key_path.read_bytes()
        if len(existing) != 32:
            raise RuntimeError(f"Authenticated-state key has invalid length: {key_path}") from exc
        return existing
    try:
        os.write(descriptor, key)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    return key


@dataclass(frozen=True)
class StateAuthenticator:
    """Repository-scoped HMAC signer whose secret is external to the repository."""

    key: bytes = field(repr=False)
    key_id: str = ""
    scheme: str = "hmac-sha256-v1"

    @classmethod
    def for_repository(cls, repository_root: str | Path) -> "StateAuthenticator":
        root = Path(repository_root).expanduser().resolve()
        key = _load_or_create_key(root)
        return cls(key=key, key_id=hashlib.sha256(key).hexdigest()[:16])

    def sign(self, payload: Any) -> str:
        return hmac.new(self.key, _canonical_bytes(payload), hashlib.sha256).hexdigest()

    def verify(self, payload: Any, signature: str, *, key_id: str, scheme: str) -> bool:
        if scheme != self.scheme or key_id != self.key_id or not signature:
            return False
        return hmac.compare_digest(self.sign(payload), signature)
