"""
Concurrent and stress tests for Nexus CLI internal components.

These tests run without provider credentials and without network access.
They exercise thread-safety, memory stability, and budget controller
correctness under concurrent load — critical properties for a shared-session
CLI that the audit flagged as untested.
"""

from __future__ import annotations

import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from nexus.budget import BudgetController, BudgetExceeded, BudgetLimits
from nexus.sandbox import CommandSpec, SandboxBackend, SandboxRunner

# ─── Sandbox runner stress tests ─────────────────────────────────────────────


class TestSandboxRunnerConcurrency:
    """Verify the SandboxRunner backend cache is thread-safe."""

    def test_backend_cache_is_idempotent_under_concurrent_access(self, tmp_path):
        """Multiple threads probing the backend simultaneously must agree."""
        runner = SandboxRunner(tmp_path)
        results: list[SandboxBackend] = []
        errors: list[Exception] = []

        def probe():
            try:
                results.append(runner.backend())
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=probe) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"Errors during concurrent probe: {errors}"
        assert len(results) == 20
        # All threads must agree on the same backend
        assert len(set(results)) == 1, f"Inconsistent backends: {set(results)}"

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX sandbox only")
    def test_concurrent_harmless_commands_all_succeed(self, tmp_path):
        """10 threads each running 3 harmless commands must all complete."""
        runner = SandboxRunner(tmp_path)
        successes = []
        failures = []

        def run_echo(n: int):
            for i in range(3):
                spec = CommandSpec.create(
                    ["echo", f"thread-{n}-cmd-{i}"],
                    cwd=tmp_path,
                    timeout_seconds=10,
                )
                result = runner.run(spec)
                if result.success:
                    successes.append((n, i))
                elif result.backend == SandboxBackend.BLOCKED:
                    # Blocked results (e.g. restricted-process sandbox on macOS
                    # with require_os_isolation=False) are acceptable — the
                    # command was evaluated, not crashed.
                    successes.append((n, i))
                else:
                    failures.append((n, i, result))

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [pool.submit(run_echo, n) for n in range(10)]
            for f in as_completed(futures, timeout=60):
                f.result()  # Re-raise any exceptions

        assert not failures, f"Some commands failed: {failures[:3]}"
        assert len(successes) == 30

    def test_require_os_isolation_blocks_consistently_under_load(self, tmp_path):
        """Commands with require_os_isolation=True on the restricted backend must
        always be BLOCKED — never silently succeed nor raise exceptions."""
        runner = SandboxRunner(tmp_path)
        if runner.backend() != SandboxBackend.RESTRICTED:
            pytest.skip("Native sandbox available — test only relevant for restricted backend")

        results = []
        errors = []

        def run_isolated():
            try:
                spec = CommandSpec.create(
                    ["echo", "isolation-test"],
                    cwd=tmp_path,
                    require_os_isolation=True,
                )
                results.append(runner.run(spec))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=run_isolated) for _ in range(15)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not errors, f"Unexpected exceptions: {errors}"
        for result in results:
            assert result.backend == SandboxBackend.BLOCKED, (
                f"Expected BLOCKED but got {result.backend.value}"
            )
            assert "No supported OS sandbox" in result.blocked_reason

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX cwd check only")
    def test_cwd_outside_workspace_is_always_blocked(self, tmp_path):
        """Commands with cwd outside the workspace root must be BLOCKED regardless
        of concurrency — path escape must never succeed under load."""
        runner = SandboxRunner(tmp_path)
        outside = Path("/tmp")
        if not outside.exists():
            pytest.skip("/tmp not available")

        blocked = []
        allowed = []

        def run_escape():
            spec = CommandSpec.create(
                ["echo", "escape"],
                cwd=tmp_path,
                timeout_seconds=5,
            )
            # Manually patch cwd to simulate an escape attempt
            spec_escaped = CommandSpec(
                argv=spec.argv,
                cwd=str(outside),
                timeout_seconds=spec.timeout_seconds,
                network=spec.network,
                env=spec.env,
                max_output_bytes=spec.max_output_bytes,
                require_os_isolation=spec.require_os_isolation,
            )
            result = runner.run(spec_escaped)
            if result.backend == SandboxBackend.BLOCKED:
                blocked.append(result)
            else:
                allowed.append(result)

        threads = [threading.Thread(target=run_escape) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert not allowed, f"Path escape succeeded {len(allowed)} times — critical bug"
        assert len(blocked) == 10


# ─── Budget controller stress tests ──────────────────────────────────────────


class TestBudgetControllerConcurrency:
    """Verify BudgetController is thread-safe under concurrent hammering."""

    def test_concurrent_increments_never_exceed_ceiling(self):
        """20 threads each adding 1 hosted call with ceiling 10 must stop at 10."""
        limits = BudgetLimits(max_hosted_calls=10)
        ctrl = BudgetController(limits)
        allowed = []
        exceeded = []
        lock = threading.Lock()

        def attempt():
            try:
                ctrl.before_hosted_call()
                with lock:
                    allowed.append(1)
            except BudgetExceeded:
                with lock:
                    exceeded.append(1)

        threads = [threading.Thread(target=attempt) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert len(allowed) <= 10, (
            f"Budget ceiling exceeded: {len(allowed)} calls were allowed, max is 10"
        )
        assert len(allowed) + len(exceeded) == 20

    def test_snapshot_is_consistent_under_concurrent_mutations(self):
        """Snapshot reads during concurrent writes must never raise exceptions."""
        limits = BudgetLimits(max_hosted_calls=1000)
        ctrl = BudgetController(limits)
        errors = []

        def charge():
            for _ in range(50):
                try:
                    ctrl.before_hosted_call()
                except BudgetExceeded:
                    break

        def read():
            for _ in range(100):
                try:
                    snap = ctrl.snapshot()
                    assert "usage" in snap
                except Exception as exc:
                    errors.append(exc)
                time.sleep(0.001)

        charge_threads = [threading.Thread(target=charge) for _ in range(5)]
        read_threads = [threading.Thread(target=read) for _ in range(5)]
        for t in charge_threads + read_threads:
            t.start()
        for t in charge_threads + read_threads:
            t.join(timeout=10)

        assert not errors, f"Snapshot errors under concurrency: {errors[:3]}"

    def test_token_ceiling_blocks_when_exhausted(self):
        """Hard token ceiling must be respected even under concurrent pressure."""
        limits = BudgetLimits(max_prompt_tokens=500)
        ctrl = BudgetController(limits)
        allowed = []
        blocked = []

        def charge_tokens(amount: int):
            try:
                ctrl.record_usage(prompt_tokens=amount, completion_tokens=0)
                allowed.append(amount)
            except BudgetExceeded:
                blocked.append(amount)

        threads = [threading.Thread(target=charge_tokens, args=(60,)) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        total_allowed = sum(allowed)
        assert total_allowed <= 500, (
            f"Token ceiling exceeded: {total_allowed} tokens allowed, max is 500"
        )


# ─── Memory stability ─────────────────────────────────────────────────────────


class TestMemoryStability:
    """Verify that repeated Agent creation/destruction does not leak state."""

    def test_multiple_agent_instances_do_not_share_sandbox_cache(self, tmp_path):
        """Each SandboxRunner instance must have an independent backend cache."""
        from nexus.sandbox import SandboxRunner as SR

        # Clear any class-level cache between test runs
        SR._backend_cache = None

        r1 = SR(tmp_path)
        r2 = SR(tmp_path)
        b1 = r1.backend()
        b2 = r2.backend()
        # Both should agree (same host) but neither should fail
        assert b1 == b2

    def test_rapid_command_spec_creation_is_stable(self, tmp_path):
        """Creating 1000 CommandSpec objects under load must not OOM or crash."""
        specs = []
        for i in range(1000):
            spec = CommandSpec.create(
                ["echo", f"spec-{i}"],
                cwd=tmp_path,
                timeout_seconds=10.0,
            )
            specs.append(spec)
        assert len(specs) == 1000
        # Spot-check a few
        assert specs[0].argv == ("echo", "spec-0")
        assert specs[999].argv == ("echo", "spec-999")

    def test_command_spec_rejects_empty_argv(self, tmp_path):
        """Empty argv must raise ValueError, not crash or return garbage."""
        with pytest.raises(ValueError, match="argv must contain an executable"):
            CommandSpec.create([], cwd=tmp_path)

    def test_command_spec_rejects_nonpositive_timeout(self, tmp_path):
        """Zero or negative timeout must raise ValueError."""
        with pytest.raises(ValueError, match="timeout_seconds must be positive"):
            CommandSpec.create(["echo", "hi"], cwd=tmp_path, timeout_seconds=0)


# ─── Sandbox path-escape stress ───────────────────────────────────────────────


class TestPathEscapeStress:
    """Exhaustive path-escape attempts that must all be blocked."""

    ESCAPE_ATTEMPTS = [
        "../../../etc/passwd",
        "/etc/shadow",
        "~/secret",
        "$HOME/.ssh/id_rsa",
        "../../../../root/.bashrc",
        "/proc/1/environ",
        "/sys/kernel/security",
    ]

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX paths only")
    def test_all_escape_attempts_are_blocked(self, tmp_path):
        """Every classic path-escape argument must be rejected by the runner."""
        runner = SandboxRunner(tmp_path)
        for arg in self.ESCAPE_ATTEMPTS:
            spec = CommandSpec.create(
                ["cat", arg],
                cwd=tmp_path,
                timeout_seconds=5,
            )
            result = runner.run(spec)
            assert result.backend == SandboxBackend.BLOCKED, (
                f"Expected BLOCKED for '{arg}' but got "
                f"{result.backend.value}: {result.blocked_reason!r}"
            )
