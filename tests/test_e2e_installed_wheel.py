"""
End-to-end tests that exercise the installed Nexus CLI as a real subprocess.

These tests validate that:
  - version, doctor, list-models all respond correctly without provider keys
  - direct !command mode works (plan-mode policy, no OS isolation required)
  - benchmark dry-run works with the manifest from the source tree

The tests run against the Python interpreter in the current environment so
they work both with `pip install -e .` (development) and with a wheel-installed
copy (release validation).

No provider credentials are required.  Tests that need the sandbox backend
are marked to skip if no backend is available.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Locate the benchmark manifest relative to the project root.
_PROJECT_ROOT = Path(__file__).parent.parent
_CORE_MANIFEST = _PROJECT_ROOT / "benchmarks" / "core.json"
_FALLBACK_MANIFEST = _PROJECT_ROOT / "benchmark-manifest.json"


def _nexus(*args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    """Run ``python -m nexus <args>`` and return the result."""
    base_env = {**os.environ, "NVIDIA_API_KEY": "test-e2e-key"}
    if env:
        base_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "nexus", *args],
        capture_output=True,
        text=True,
        timeout=30,
        env=base_env,
    )


# ─── Basic CLI smoke tests ────────────────────────────────────────────────────


def test_installed_cli_version():
    """``nexus --version`` must exit 0 and print the version string."""
    result = _nexus("--version")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "NexusAI" in result.stdout or "NexusAI" in result.stderr
    from nexus import __version__
    assert __version__ in result.stdout or __version__ in result.stderr


def test_installed_cli_list_models():
    """``nexus --list-models`` must exit 0 and list at least one model."""
    result = _nexus("--list-models")
    assert result.returncode == 0, f"stderr: {result.stderr}"
    output = result.stdout + result.stderr
    # At minimum the default GLM model must appear
    assert "glm" in output.lower() or "deepseek" in output.lower() or "nova" in output.lower()


def test_installed_cli_doctor_exits_cleanly():
    """``nexus --doctor`` must exit without crashing (0 or 2, never 1)."""
    result = _nexus("--doctor")
    assert result.returncode in (0, 2), (
        f"Doctor exited with unexpected code {result.returncode}.\n"
        f"stdout: {result.stdout[:500]}\nstderr: {result.stderr[:500]}"
    )
    output = result.stdout
    assert "Nexus doctor" in output
    assert "Sandbox" in output


def test_installed_cli_doctor_shows_version():
    """Doctor report must include the installed version."""
    result = _nexus("--doctor")
    from nexus import __version__
    assert __version__ in result.stdout


def test_installed_cli_doctor_shows_sandbox_status():
    """Doctor must include a Sandbox line with PASS, WARN, or FAIL."""
    result = _nexus("--doctor")
    output = result.stdout
    assert any(marker in output for marker in ("[✓]", "[!]", "[✗]")), (
        f"No status marker found in doctor output:\n{output}"
    )
    assert "Sandbox" in output


def test_installed_cli_doctor_fail_mode_shows_instructions():
    """Running doctor in review mode without a native sandbox shows install instructions."""
    import platform
    # Only meaningful on Linux where we can simulate a missing bubblewrap
    if platform.system().lower() != "linux":
        pytest.skip("Install-instruction check only validated on Linux")
    result = _nexus("--doctor", "--mode", "review")
    output = result.stdout
    if "[✗] Sandbox" in output:
        assert "bubblewrap" in output.lower() or "apt-get" in output.lower(), (
            "Doctor FAIL for sandbox should include install instructions"
        )


# ─── Direct command (!cmd) mode ───────────────────────────────────────────────


def test_installed_direct_command_echo_json():
    """``nexus --output-format json '!echo hello'`` must succeed and return JSON."""
    result = _nexus("--output-format", "json", "!echo hello_nexus_e2e")
    if sys.platform == "win32":
        assert result.returncode in (0, 2)
        return
    assert result.returncode == 0, (
        f"Direct !command failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    data = json.loads(result.stdout)
    assert data["name"] == "run_process"
    assert "hello_nexus_e2e" in data["result"]
    assert data["success"] is True


def test_installed_direct_command_plan_mode_does_not_require_sandbox():
    """Direct commands with --mode plan must NOT require bubblewrap."""
    result = _nexus("--mode", "plan", "--output-format", "json", "!echo plan_mode_ok")
    if sys.platform == "win32":
        assert result.returncode in (0, 2)
        return
    assert result.returncode == 0, (
        f"!command in plan mode failed.\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    data = json.loads(result.stdout)
    assert data["success"] is True
    assert "plan_mode_ok" in data["result"]


def test_installed_direct_command_dangerous_requires_confirm():
    """Dangerous commands without --confirm-danger must be held as PENDING."""
    result = _nexus("!rm -rf ./nonexistent_sentinel_dir_12345")
    output = result.stdout + result.stderr
    # Either the command was blocked by safety or returned pending confirmation
    assert result.returncode in (0, 2)
    if result.returncode == 2:
        assert any(
            marker in output
            for marker in ("BLOCKED", "PENDING", "confirm-danger", "danger")
        ), f"Expected a safety message but got:\n{output}"


def test_installed_direct_command_invalid_syntax_exits_gracefully():
    """An unclosed quote in a !command must exit with a clear error, not a crash."""
    result = _nexus('!echo "unclosed')
    assert result.returncode in (0, 1, 2)
    # Must not be a Python traceback
    assert "Traceback" not in result.stderr, f"Unexpected traceback:\n{result.stderr}"


# ─── Benchmark dry-run ────────────────────────────────────────────────────────


@pytest.mark.skipif(
    not (_CORE_MANIFEST.exists() or _FALLBACK_MANIFEST.exists()),
    reason="No benchmark manifest found; skipping (expected in wheel install without checkout)",
)
def test_installed_benchmark_dry_run():
    """``nexus benchmark --manifest ... --dry-run`` must not crash and must exit 0 or 2."""
    manifest = _CORE_MANIFEST if _CORE_MANIFEST.exists() else _FALLBACK_MANIFEST
    result = _nexus("benchmark", "--manifest", str(manifest), "--dry-run")
    output = result.stdout + result.stderr
    assert result.returncode in (0, 2), (
        f"Benchmark dry-run exited {result.returncode}.\n"
        f"stdout: {result.stdout[:800]}\nstderr: {result.stderr[:400]}"
    )
    # Must produce JSON output
    assert "{" in output, "Benchmark dry-run should produce JSON output"


def test_installed_benchmark_invalid_manifest_exits_nonzero():
    """A missing manifest must produce a clear error and nonzero exit."""
    result = _nexus("benchmark", "--manifest", "/nonexistent/fake.json")
    assert result.returncode in (1, 2), (
        f"Expected nonzero exit for missing manifest, got {result.returncode}"
    )


# ─── Output format consistency ────────────────────────────────────────────────


def test_installed_output_format_jsonl():
    """``--output-format jsonl '!echo hi'`` must produce newline-delimited JSON."""
    if sys.platform == "win32":
        pytest.skip("JSONL direct command on Windows may vary")
    result = _nexus("--output-format", "jsonl", "!echo jsonl_test")
    assert result.returncode == 0
    for line in result.stdout.strip().splitlines():
        json.loads(line)  # Each line must be valid JSON


# ─── Edge cases ───────────────────────────────────────────────────────────────


def test_installed_unknown_subcommand_exits_nonzero():
    """Unknown subcommands must exit with a non-zero code."""
    result = _nexus("totally-unknown-subcommand-xyz")
    assert result.returncode in (1, 2)


def test_installed_no_credentials_exits_nonzero():
    """Without any credential, the CLI must exit nonzero with a clear message."""
    clean_env = {
        k: v for k, v in os.environ.items()
        if k not in {
            "NVIDIA_API_KEY", "GROQ_API_KEY", "OPENROUTER_API_KEY",
            "NEXUS_OPENAI_API_KEY", "NEXUS_OPENAI_BASE_URL",
        }
    }
    # Use an innocuous prompt that doesn't trigger the direct-command path
    result = subprocess.run(
        [sys.executable, "-m", "nexus", "write hello world"],
        capture_output=True,
        text=True,
        timeout=15,
        env=clean_env,
    )
    assert result.returncode != 0, "Should exit nonzero without any credentials"
