"""Adversarial sandbox trust-isolation boundary tests for Noryx 3.8.5.

Golden invariant (from the remediation spec):

    MODEL / TOOLS / SUBPROCESSES → ~/.noryx/trust/ = DENIED
    HOST-SIDE USER APPROVAL PATH → ~/.noryx/trust/ = ALLOWED

Every test proves that the SandboxRunner enforces this invariant at the
OS sandbox level (macOS sandbox-exec / bubblewrap) AND at the Python-level
path-violation layer (RESTRICTED fallback defense-in-depth).

Test inventory
--------------
1.  shell_cannot_read_trust_file
2.  shell_cannot_list_trust_dir
3.  shell_cannot_write_trust_file
4.  python_subprocess_cannot_read_trust
5.  python_subprocess_cannot_write_trust
6.  directory_listing_denied
7.  delete_attempt_denied
8.  symlink_into_trust_denied
9.  path_traversal_denied
10. trust_state_unchanged_after_all_attacks
11. noryx_home_env_stripped_from_subprocess
12. trust_authority_approval_still_works_after_isolation
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from nexus.sandbox import CommandSpec, SandboxBackend, SandboxRunner
from nexus.trust import TrustAuthority, TrustReader, _trust_file_for


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _project(tmp_path: Path) -> Path:
    """Create a minimal project directory."""
    p = tmp_path / "project"
    p.mkdir()
    return p


def _authority(project: Path, monkeypatch, noryx_home: Path) -> TrustAuthority:
    """Create a TrustAuthority backed by noryx_home/trust/."""
    monkeypatch.setenv("NORYX_HOME", str(noryx_home))
    return TrustAuthority(str(project))


def _runner(project: Path, trust_dir: Path) -> SandboxRunner:
    """Create a SandboxRunner with a known trust_dir for testing."""
    return SandboxRunner(str(project), trust_dir=trust_dir)


def _unisolated_spec(argv: list[str], cwd: str) -> CommandSpec:
    """Command spec that runs without native isolation (RESTRICTED backend test)."""
    return CommandSpec.create(
        argv=argv,
        cwd=cwd,
        require_os_isolation=False,
        allow_unisolated_host_process=True,
    )


def _native_spec(argv: list[str], cwd: str) -> CommandSpec:
    """Command spec that requires native isolation (macOS / bwrap)."""
    return CommandSpec.create(
        argv=argv,
        cwd=cwd,
        require_os_isolation=True,
    )


def _backend_supports_native(runner: SandboxRunner) -> bool:
    return runner.backend() in (SandboxBackend.BUBBLEWRAP, SandboxBackend.MACOS)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Full test environment: project, noryx_home, approved trust record, runner."""
    project = _project(tmp_path)
    noryx_home = tmp_path / "noryx_home"
    noryx_home.mkdir()

    monkeypatch.setenv("NORYX_HOME", str(noryx_home))

    trust_dir = noryx_home / "trust"
    trust_dir.mkdir()

    authority = TrustAuthority(str(project))
    noryx_md = project / "NORYX.md"
    noryx_md.write_text("# Approved\n")
    authority.approve(str(noryx_md))

    # Write a recognisable sentinel into the trust file so we can detect leaks.
    trust_file = _trust_file_for(project)
    sentinel = "TRUST_SENTINEL_VALUE_DO_NOT_EXPOSE"
    raw = json.loads(trust_file.read_text())
    raw["_sentinel"] = sentinel
    trust_file.write_text(json.dumps(raw))

    runner = _runner(project, trust_dir)

    return {
        "project": project,
        "noryx_home": noryx_home,
        "trust_dir": trust_dir,
        "trust_file": trust_file,
        "noryx_md": noryx_md,
        "runner": runner,
        "authority": authority,
        "sentinel": sentinel,
        "cwd": str(project),
    }


# ---------------------------------------------------------------------------
# 1. Shell cannot read trust file
# ---------------------------------------------------------------------------

def test_shell_cannot_read_trust_file(env):
    """cat of the trust file from within the sandbox must fail or produce no output."""
    runner: SandboxRunner = env["runner"]
    trust_file: Path = env["trust_file"]
    sentinel: str = env["sentinel"]
    cwd: str = env["cwd"]

    # Test at both the Python path-violation level (RESTRICTED) and the native level.
    for spec in [
        _unisolated_spec(["cat", str(trust_file)], cwd),
    ]:
        result = runner.run(spec)
        # Either blocked (exit 1 / blocked) or sentinel not in output.
        assert sentinel not in result.stdout, (
            f"Trust file sentinel leaked into stdout via 'cat'. "
            f"exit={result.exit_code} stdout={result.stdout[:200]}"
        )

    if _backend_supports_native(runner):
        result = runner.run(_native_spec(["cat", str(trust_file)], cwd))
        assert sentinel not in result.stdout, (
            "Trust file sentinel leaked into stdout via native sandbox 'cat'."
        )
        assert not result.success, (
            "cat of trust file must not succeed inside native sandbox."
        )


# ---------------------------------------------------------------------------
# 2. Shell cannot list trust dir
# ---------------------------------------------------------------------------

def test_shell_cannot_list_trust_dir(env):
    """ls of the trust directory must not reveal its contents."""
    runner: SandboxRunner = env["runner"]
    trust_dir: Path = env["trust_dir"]
    trust_file: Path = env["trust_file"]
    cwd: str = env["cwd"]

    for spec in [
        _unisolated_spec(["ls", str(trust_dir)], cwd),
    ]:
        result = runner.run(spec)
        assert trust_file.name not in result.stdout, (
            f"Trust directory listing leaked trust file name into stdout."
        )

    if _backend_supports_native(runner):
        result = runner.run(_native_spec(["ls", str(trust_dir)], cwd))
        assert trust_file.name not in result.stdout, (
            "Trust directory listing must not succeed inside native sandbox."
        )


# ---------------------------------------------------------------------------
# 3. Shell cannot write trust file
# ---------------------------------------------------------------------------

def test_shell_cannot_write_trust_file(env):
    """An attempt to overwrite the trust file via shell must fail."""
    runner: SandboxRunner = env["runner"]
    trust_file: Path = env["trust_file"]
    original_content = trust_file.read_text()
    cwd: str = env["cwd"]

    # Python path-violation level (RESTRICTED backend).
    result = runner.run(
        _unisolated_spec(
            ["/bin/sh", "-c", f"echo HACKED > '{trust_file}'"],
            cwd,
        )
    )
    # If the shell ran at all (some RESTRICTED hosts permit it), verify the file is unchanged.
    assert trust_file.read_text() == original_content, (
        "Trust file was modified by a sandboxed shell write attempt."
    )

    if _backend_supports_native(runner):
        runner.run(
            _native_spec(
                ["/bin/sh", "-c", f"echo HACKED > '{trust_file}'"],
                cwd,
            )
        )
        assert trust_file.read_text() == original_content, (
            "Trust file was modified inside the native sandbox."
        )


# ---------------------------------------------------------------------------
# 4 & 5. Python subprocess cannot read or write trust dir
# ---------------------------------------------------------------------------

def test_python_subprocess_cannot_read_trust(env):
    """A Python subprocess spawned inside the sandbox cannot open the trust file."""
    runner: SandboxRunner = env["runner"]
    trust_file: Path = env["trust_file"]
    sentinel: str = env["sentinel"]
    cwd: str = env["cwd"]

    read_script = (
        f"import sys; "
        f"content = open('{trust_file}').read(); "
        f"print(content)"
    )

    result = runner.run(
        _unisolated_spec(["python3", "-c", read_script], cwd)
    )
    assert sentinel not in result.stdout, (
        "Trust file sentinel exposed to sandboxed Python subprocess."
    )

    if _backend_supports_native(runner):
        result = runner.run(_native_spec(["python3", "-c", read_script], cwd))
        assert sentinel not in result.stdout, (
            "Trust file sentinel exposed to Python subprocess in native sandbox."
        )


def test_python_subprocess_cannot_write_trust(env):
    """A Python subprocess inside the sandbox cannot write to the trust dir."""
    runner: SandboxRunner = env["runner"]
    trust_file: Path = env["trust_file"]
    original_content = trust_file.read_text()
    trust_dir: Path = env["trust_dir"]
    cwd: str = env["cwd"]

    write_script = (
        f"open('{trust_dir / 'attack.json'}', 'w').write('{{}}')"
    )
    runner.run(_unisolated_spec(["python3", "-c", write_script], cwd))

    attack_file = trust_dir / "attack.json"
    assert not attack_file.exists(), (
        "Sandboxed Python subprocess created a file inside the trust directory."
    )
    assert trust_file.read_text() == original_content, (
        "Trust file was modified by sandboxed Python subprocess."
    )

    if _backend_supports_native(runner):
        runner.run(_native_spec(["python3", "-c", write_script], cwd))
        assert not attack_file.exists(), (
            "Sandboxed Python subprocess created file in trust dir inside native sandbox."
        )


# ---------------------------------------------------------------------------
# 6. Directory listing denied
# ---------------------------------------------------------------------------

def test_directory_listing_denied_for_find(env):
    """find / stat targeting the trust dir must not enumerate its contents."""
    runner: SandboxRunner = env["runner"]
    trust_dir: Path = env["trust_dir"]
    trust_file: Path = env["trust_file"]
    cwd: str = env["cwd"]

    result = runner.run(
        _unisolated_spec(
            ["/bin/sh", "-c", f"find '{trust_dir}' -type f 2>&1 || true"],
            cwd,
        )
    )
    assert trust_file.name not in result.stdout, (
        f"find enumerated trust dir contents: {result.stdout[:200]}"
    )

    if _backend_supports_native(runner):
        result = runner.run(
            _native_spec(
                ["/bin/sh", "-c", f"find '{trust_dir}' -type f 2>&1 || true"],
                cwd,
            )
        )
        assert trust_file.name not in result.stdout, (
            "find enumerated trust dir inside native sandbox."
        )


# ---------------------------------------------------------------------------
# 7. Delete attempt denied
# ---------------------------------------------------------------------------

def test_delete_attempt_denied(env):
    """rm -rf of the trust directory must leave it intact."""
    runner: SandboxRunner = env["runner"]
    trust_file: Path = env["trust_file"]
    trust_dir: Path = env["trust_dir"]
    cwd: str = env["cwd"]

    # RESTRICTED: Python path-violation blocks paths outside workspace.
    runner.run(
        _unisolated_spec(
            ["/bin/sh", "-c", f"rm -rf '{trust_dir}' 2>&1; true"],
            cwd,
        )
    )
    assert trust_dir.exists(), "Trust directory was deleted by sandboxed shell."
    assert trust_file.exists(), "Trust file was deleted by sandboxed shell."

    if _backend_supports_native(runner):
        runner.run(
            _native_spec(
                ["/bin/sh", "-c", f"rm -rf '{trust_dir}' 2>&1; true"],
                cwd,
            )
        )
        assert trust_dir.exists(), "Trust directory deleted inside native sandbox."
        assert trust_file.exists(), "Trust file deleted inside native sandbox."


# ---------------------------------------------------------------------------
# 8. Symlink into trust dir denied
# ---------------------------------------------------------------------------

def test_symlink_into_trust_denied(env):
    """A symlink inside the workspace pointing to the trust dir must be blocked."""
    runner: SandboxRunner = env["runner"]
    project: Path = env["project"]
    trust_dir: Path = env["trust_dir"]
    trust_file: Path = env["trust_file"]
    sentinel: str = env["sentinel"]
    cwd: str = env["cwd"]

    # Create a symlink inside the workspace that points to the trust dir.
    link = project / "evil_link"
    link.symlink_to(trust_dir)

    # Attempt to read through the symlink.
    read_through_link = f"cat '{link}/{trust_file.name}'"
    result = runner.run(
        _unisolated_spec(["/bin/sh", "-c", read_through_link], cwd)
    )
    assert sentinel not in result.stdout, (
        "Trust sentinel readable through workspace symlink at RESTRICTED level."
    )

    if _backend_supports_native(runner):
        result = runner.run(_native_spec(["/bin/sh", "-c", read_through_link], cwd))
        assert sentinel not in result.stdout, (
            "Trust sentinel readable through workspace symlink inside native sandbox."
        )

    link.unlink()


# ---------------------------------------------------------------------------
# 9. Path traversal denied
# ---------------------------------------------------------------------------

def test_path_traversal_denied(env):
    """Relative traversal paths targeting trust dir must be blocked by path-violation."""
    runner: SandboxRunner = env["runner"]
    project: Path = env["project"]
    trust_file: Path = env["trust_file"]
    sentinel: str = env["sentinel"]
    cwd: str = env["cwd"]

    # Compute relative path from workspace to trust_file.
    try:
        rel = os.path.relpath(trust_file, project)
    except ValueError:
        pytest.skip("relpath not available on this platform")

    result = runner.run(
        _unisolated_spec(["/bin/sh", "-c", f"cat '{rel}' 2>&1 || true"], cwd)
    )
    # Either the command was blocked (path violation) or the sentinel is not in output.
    # On macOS, the path might resolve outside workspace before the shell runs,
    # but the Python layer should catch it first.
    assert sentinel not in result.stdout, (
        f"Trust sentinel exposed via relative path traversal: {rel}"
    )


# ---------------------------------------------------------------------------
# 10. Trust state unchanged after all attacks
# ---------------------------------------------------------------------------

def test_trust_state_unchanged_after_all_attacks(env, monkeypatch):
    """After running all attack commands, the trust decision must still be approved."""
    runner: SandboxRunner = env["runner"]
    trust_file: Path = env["trust_file"]
    trust_dir: Path = env["trust_dir"]
    noryx_md: Path = env["noryx_md"]
    project: Path = env["project"]
    cwd: str = env["cwd"]

    attacks = [
        ["/bin/sh", "-c", f"cat '{trust_file}'"],
        ["/bin/sh", "-c", f"ls '{trust_dir}'"],
        ["/bin/sh", "-c", f"echo HACKED > '{trust_file}'"],
        ["/bin/sh", "-c", f"rm -f '{trust_file}'"],
        ["python3", "-c", f"open('{trust_file}','w').write('{{}}')"],
    ]
    for argv in attacks:
        runner.run(_unisolated_spec(argv, cwd))

    # Re-read trust state — must still be approved despite all attack attempts.
    monkeypatch.setenv("NORYX_HOME", str(env["noryx_home"]))
    reader = TrustReader(str(project))
    assert reader.is_approved(str(noryx_md)), (
        "Trust approval was revoked or corrupted by sandbox attack attempts."
    )
    # File must still exist and be valid JSON.
    assert trust_file.exists(), "Trust file was deleted by sandbox attacks."
    data = json.loads(trust_file.read_text())
    assert "approvals" in data, "Trust file structure was corrupted."


# ---------------------------------------------------------------------------
# 11. NORYX_HOME env stripped from subprocess
# ---------------------------------------------------------------------------

def test_noryx_home_env_stripped_from_subprocess(env, monkeypatch):
    """NORYX_HOME must not appear in the environment of a sandboxed subprocess."""
    runner: SandboxRunner = env["runner"]
    cwd: str = env["cwd"]

    # Set NORYX_HOME in the parent process environment.
    monkeypatch.setenv("NORYX_HOME", str(env["noryx_home"]))
    monkeypatch.setenv("NEXUS_HOME", str(env["noryx_home"]))

    result = runner.run(
        _unisolated_spec(
            ["/bin/sh", "-c", "env"],
            cwd,
        )
    )
    assert "NORYX_HOME" not in result.stdout, (
        "NORYX_HOME leaked into sandboxed subprocess environment:\n"
        + result.stdout[:400]
    )
    assert "NEXUS_HOME" not in result.stdout, (
        "NEXUS_HOME leaked into sandboxed subprocess environment:\n"
        + result.stdout[:400]
    )


# ---------------------------------------------------------------------------
# 12. Host-side TrustAuthority still works after sandbox isolation
# ---------------------------------------------------------------------------

def test_trust_authority_approval_still_works_after_isolation(env, monkeypatch):
    """The host-side TrustAuthority must remain fully functional.

    The sandbox restrictions target model-controlled subprocesses; they must
    not interfere with the host-side approval code path.
    """
    project: Path = env["project"]
    noryx_home: Path = env["noryx_home"]

    monkeypatch.setenv("NORYX_HOME", str(noryx_home))
    authority = TrustAuthority(str(project))

    new_file = project / "NEXUS.md"
    new_file.write_text("# New rules approved by user\n")

    # Host-side approve must succeed.
    decision = authority.approve(str(new_file))
    assert decision.approved, "TrustAuthority.approve() failed on host side."

    # Host-side inspect must return approved.
    assert authority.is_approved(str(new_file)), (
        "TrustAuthority.is_approved() returned False after successful approval."
    )

    # Host-side revoke must succeed.
    authority.revoke(str(new_file))
    assert not authority.is_approved(str(new_file)), (
        "TrustAuthority.is_approved() still True after revoke()."
    )

    # TrustReader (read-only) must also see the revocation.
    reader = TrustReader(str(project))
    assert not reader.is_approved(str(new_file)), (
        "TrustReader sees stale approval after TrustAuthority.revoke()."
    )
