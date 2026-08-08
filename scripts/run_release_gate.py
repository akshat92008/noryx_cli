#!/usr/bin/env python3
"""Run the deterministic checks required for a Nexus launch candidate."""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from nexus.provenance import resolve_source_identity
from nexus.qualification_environment import qualify_environment

REPO = Path(__file__).resolve().parents[1]
PROVIDER_SECRET_PREFIXES = (
    "NVIDIA_API_KEY",
    "NVIDIA_FALLBACK_API_KEY",
    "GROQ_API_KEY",
    "GROQ_FALLBACK_API_KEY",
    "OPENROUTER_API_KEY",
)
PROVIDER_SECRET_NAMES = {"NEXUS_OPENAI_API_KEY", "NEXUS_OPENAI_BASE_URL"}


_SOURCE_HASH_IGNORED_PARTS = {
    ".git",
    ".venv",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "build",
    "dist",
    "verification_evidence",
    "runs",
}


def source_revision(repo: Path = REPO) -> str:
    """Return the exact Git or deterministic archive revision for qualification."""
    return resolve_source_identity(repo).revision


def deterministic_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """Create an offline environment for deterministic release checks."""
    env = dict(os.environ if base is None else base)
    for name in list(env):
        if name in PROVIDER_SECRET_NAMES or name.startswith(PROVIDER_SECRET_PREFIXES):
            env.pop(name, None)
    env["NEXUS_DISABLE_NETWORK"] = "1"
    env.setdefault("UV_CACHE_DIR", str(Path(tempfile.gettempdir()) / "nexus-uv-cache"))
    env.setdefault("UV_LINK_MODE", "copy")
    return env


def _requirement_name(value: str) -> str:
    """Normalize a simple PEP 508 requirement to its distribution name."""
    name = value.strip().split(";", 1)[0].strip().split("[", 1)[0]
    for separator in ("===", "==", ">=", "<=", "~=", "!=", ">", "<"):
        name = name.split(separator, 1)[0]
    return name.strip().lower().replace("_", "-")


def assert_dependency_mirror(repo: Path = REPO) -> None:
    """Require requirements.txt to mirror canonical project dependencies."""
    pyproject = (repo / "pyproject.toml").read_text(encoding="utf-8")
    marker = "dependencies ="
    start = pyproject.find(marker)
    if start < 0:
        raise RuntimeError("pyproject.toml has no project dependencies list")
    list_start = pyproject.find("[", start + len(marker))
    if list_start < 0:
        raise RuntimeError("could not parse pyproject.toml project dependencies")
    dependencies = None
    for index in range(list_start + 1, len(pyproject)):
        if pyproject[index] != "]":
            continue
        try:
            candidate = ast.literal_eval(pyproject[list_start : index + 1])
        except (SyntaxError, ValueError):
            continue
        if isinstance(candidate, list):
            dependencies = candidate
            break
    if dependencies is None:
        raise RuntimeError("could not parse pyproject.toml project dependencies")
    canonical = {
        _requirement_name(item)
        for item in dependencies
    }
    mirrored = {
        _requirement_name(line)
        for line in (repo / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if canonical != mirrored:
        raise RuntimeError(
            "requirements.txt drifted from pyproject.toml: "
            f"missing={sorted(canonical - mirrored)} extra={sorted(mirrored - canonical)}"
        )


def run(command: list[str], *, cwd: Path = REPO, env: dict[str, str] | None = None) -> None:
    """Run one release check and stop immediately on failure."""
    printable = " ".join(command)
    print(f"\n==> {printable}", flush=True)
    subprocess.run(command, cwd=cwd, env=env, check=True)


def wheel_install_command(python: str, target: Path, wheel: Path) -> list[str]:
    """Build a wheel smoke-install command for pip-full and uv-managed venvs."""

    if importlib.util.find_spec("pip") is not None:
        installer = [python, "-m", "pip", "install"]
    elif uv := shutil.which("uv"):
        installer = [uv, "pip", "install", "--python", python]
    else:
        raise RuntimeError("release gate requires pip or uv to smoke-install the wheel")
    return [*installer, "--no-deps", "--target", str(target), str(wheel)]


def run_pytest_shards(
    python: str,
    *,
    env: dict[str, str],
    repo: Path = REPO,
    shard_count: int | None = None,
) -> tuple[int, int]:
    """Run test files in isolated processes and combine branch coverage.

    Nexus intentionally exercises subprocesses, web servers, and background
    workers. Process-level sharding prevents one test's global runtime state
    from contaminating later tests while still enforcing aggregate coverage.
    """
    import re

    test_files = sorted((repo / "tests").glob("test_*.py"))
    if not test_files:
        raise RuntimeError("release gate found no test files")
    requested = (
        int(shard_count)
        if shard_count is not None
        else int(os.environ.get("NEXUS_TEST_SHARDS", "0"))
    )
    # Default to one test module per process. Nexus tests deliberately create
    # subprocesses, web servers, and global agent state; module isolation makes
    # release results deterministic instead of order-dependent.
    count = len(test_files) if requested <= 0 else min(len(test_files), max(1, requested))
    shards = [test_files[index::count] for index in range(count)]
    timeout_seconds = int(os.environ.get("NEXUS_TEST_SHARD_TIMEOUT", "300"))

    for stale in repo.glob(".coverage.shard-*"):
        stale.unlink(missing_ok=True)
    subprocess.run(
        [python, "-m", "coverage", "erase"],
        cwd=repo,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    total_passed = 0
    total_skipped = 0
    for index, shard in enumerate(shards, start=1):
        command = [
            python,
            "-m",
            "coverage",
            "run",
            "--branch",
            "--source=nexus",
        ]
        command.extend(
            [
                "-m",
                "pytest",
                "-q",
                "--disable-warnings",
                *(str(path.relative_to(repo)) for path in shard),
            ]
        )
        print(
            f"\n==> pytest shard {index}/{len(shards)} ({len(shard)} files)",
            flush=True,
        )
        shard_env = dict(env)
        shard_env["COVERAGE_FILE"] = str(repo / f".coverage.shard-{index:03d}")
        try:
            result = subprocess.run(
                command,
                cwd=repo,
                env=shard_env,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"pytest shard {index} exceeded {timeout_seconds}s"
            ) from exc
        print(result.stdout, end="")
        if result.returncode != 0:
            print(result.stderr, file=sys.stderr)
            raise RuntimeError(f"pytest shard {index} failed with exit code {result.returncode}")
        passed = re.search(r"(\d+) passed", result.stdout)
        skipped = re.search(r"(\d+) skipped", result.stdout)
        if not passed:
            raise RuntimeError(f"pytest shard {index} reported no passing tests")
        total_passed += int(passed.group(1))
        total_skipped += int(skipped.group(1)) if skipped else 0

    run([python, "-m", "coverage", "combine"], cwd=repo, env=env)
    run(
        [python, "-m", "coverage", "report", "--show-missing", "--fail-under=60"],
        cwd=repo,
        env=env,
    )
    run([python, "-m", "coverage", "xml"], cwd=repo, env=env)
    return total_passed, total_skipped


def main() -> int:
    python = sys.executable
    identity = resolve_source_identity(REPO)
    revision = identity.revision
    offline_env = deterministic_env()
    assert_dependency_mirror()

    environment = qualify_environment(REPO)
    if not environment.passed:
        failed = [item.requirement for item in environment.dependencies if not item.passed]
        raise RuntimeError(
            "release environment violates pyproject/pip dependency contract: "
            f"failed={failed}; pip_check={environment.pip_check_output!r}"
        )
    run([python, "-m", "ruff", "check", "nexus", "tests", "scripts"], env=offline_env)
    
    test_count, skipped_count = run_pytest_shards(
        python,
        env=offline_env,
        repo=REPO,
    )
    minimum_tests = int(os.environ.get("NEXUS_RELEASE_MIN_TESTS", "400"))
    if test_count < minimum_tests:
        raise RuntimeError(
            f"release gate collected only {test_count} passing tests; expected at least {minimum_tests}"
        )
    run([python, "-m", "compileall", "-q", "nexus", "tests"], env=offline_env)

    with tempfile.TemporaryDirectory(prefix="nexus-runtime-qualification-") as runtime_temp:
        runtime_root = Path(runtime_temp)
        shared_report = runtime_root / "shared-process.json"
        run(
            [
                python,
                "scripts/qualify_shared_process.py",
                "--timeout",
                os.environ.get("NEXUS_SHARED_PROCESS_TIMEOUT", "1200"),
                "--output",
                str(shared_report),
            ],
            env=offline_env,
        )
        shared_payload = json.loads(shared_report.read_text(encoding="utf-8"))
        if not shared_payload.get("source_tree_stable"):
            raise RuntimeError("shared-process qualification changed the source tree")
        if shared_payload.get("clean_exit_observed") is not True:
            raise RuntimeError("shared-process qualification did not observe a clean interpreter exit")

        sandbox_workspace = runtime_root / "sandbox-workspace"
        sandbox_report = runtime_root / "sandbox-qualification.json"
        run(
            [
                python,
                "scripts/qualify_native_sandbox.py",
                "--workspace",
                str(sandbox_workspace),
                "--output",
                str(sandbox_report),
            ],
            env=offline_env,
        )
        sandbox_payload = json.loads(sandbox_report.read_text(encoding="utf-8"))
        supported_mode = str(sandbox_payload.get("supported_mode") or "blocked")
        autonomous_ready = sandbox_payload.get("autonomous_ready") is True
        if supported_mode == "blocked":
            raise RuntimeError("host sandbox qualification blocks all execution modes")
        if os.environ.get("NEXUS_RELEASE_REQUIRE_AUTONOMOUS") == "1" and not autonomous_ready:
            raise RuntimeError(
                "autonomous release requested but native sandbox qualification did not pass"
            )

    with tempfile.TemporaryDirectory(prefix="nexus-release-gate-") as temp:
        root = Path(temp)
        # Copy the whole repo to the temp directory so concurrent builds don't collide
        build_src = root / "src_copy"
        shutil.copytree(
            REPO,
            build_src,
            ignore=shutil.ignore_patterns(
                ".git",
                ".venv",
                "__pycache__",
                "dist",
                "build",
                "*.egg-info",
                "legacy",
                "verification_evidence",
                "coding_agent",
                "bakeoff",
                "runs",
            ),
        )

        dist = root / "dist"
        # Dependencies were already installed from the lockfile. Reusing that
        # environment keeps the release gate deterministic and prevents an
        # isolated build environment from reaching a package index mid-gate.
        run(
            [python, "-m", "build", "--no-isolation", "--outdir", str(dist)],
            cwd=build_src,
        )

        wheels = sorted(dist.glob("nexusai_cli-*.whl"))
        if len(wheels) != 1:
            raise RuntimeError(f"expected one nexusai-cli wheel, found: {wheels}")

        expected_members = {
            path.relative_to(build_src).as_posix()
            for path in (build_src / "nexus").rglob("*")
            if path.is_file()
            and (
                path.suffix == ".py"
                or "nexus/webapp/static" in path.relative_to(build_src).as_posix()
            )
        }
        with zipfile.ZipFile(wheels[0]) as archive:
            packaged_members = set(archive.namelist())
        missing_members = sorted(expected_members - packaged_members)
        if missing_members:
            raise RuntimeError(
                "wheel is missing source files from the tested commit: "
                + ", ".join(missing_members)
            )
        wheel_sha256 = hashlib.sha256(wheels[0].read_bytes()).hexdigest()

        installed = root / "installed"
        run(
            wheel_install_command(python, installed, wheels[0]),
            cwd=build_src,
            env=offline_env,
        )
        smoke_env = deterministic_env()
        smoke_env["PYTHONPATH"] = str(installed)
        run(
            [
                python,
                "-c",
                (
                    "import importlib.metadata; import nexus; import nexus.nova_backend; "
                    "import nexus.two_node_backend; import nexus.behavioral; "
                    "import nexus.benchmark; import nexus.execution; "
                    "import nexus.extensions; import nexus.language_intelligence; "
                    "import nexus.policy; import nexus.run_catalog; import nexus.sandbox; "
                    "from nexus.webapp.server import create_app; "
                    "assert create_app('release-smoke').routes; "
                    "dist = importlib.metadata.distribution('nexusai-cli'); "
                    "assert any(ep.name == 'nexus' for ep in dist.entry_points); "
                    "assert nexus.__version__ == dist.version"
                ),
            ],
            cwd=root,
            env=smoke_env,
        )
        run([python, "-m", "nexus", "--version"], cwd=root, env=smoke_env)
        run([python,"-m","nexus","benchmark","--installed-core","--dry-run","--output",str(root/"installed-benchmark.json")],cwd=root,env=smoke_env)
        run(
            [
                python,
                "-m",
                "nexus",
                "benchmark",
                "offline-reliability",
                "--output",
                str(root / "installed-offline-reliability.json"),
            ],
            cwd=root,
            env=smoke_env,
        )

        doctor_env = dict(smoke_env)
        doctor_env["NVIDIA_API_KEY"] = "nvapi-release-smoke"
        doctor_env.pop("GROQ_API_KEY", None)
        doctor_env.pop("OPENROUTER_API_KEY", None)
        workspace_dir = root / "workspace"
        workspace_dir.mkdir(parents=True, exist_ok=True)
        run(
            [
                python,
                "-m",
                "nexus",
                "--doctor",
                "--working-dir",
                str(workspace_dir),
            ],
            cwd=root,
            env=doctor_env,
        )

        provenance_str = (
            f"source={revision} tree={identity.source_tree_sha256} "
            f"lock={identity.dependency_lock_sha256 or 'none'} "
            f"wheel={wheels[0].name} sha256={wheel_sha256} "
            f"packaged_source_files={len(expected_members)}"
        )
        print(f"\nRelease provenance: {provenance_str}", flush=True)

        from nexus import __version__

        readiness_file = REPO / f"LAUNCH_READINESS_{__version__}.md"
        readiness_content = f"""# Nexus {__version__} Launch Readiness

## Automated Release Gates
- **Tests Passed**: {test_count}
- **Tests Skipped**: {skipped_count}
- **Shared-process clean exit**: yes
- **Dependency contract**: passed (`pip check` + declared ranges)
- **Sandbox supported mode**: {supported_mode}
- **Autonomous sandbox ready**: {autonomous_ready}
- **Provenance**: {provenance_str}

## Status
Deterministic software, packaging, lifecycle, and supervised execution gates passed.
Autonomous execution is qualified only when **Autonomous sandbox ready** is `True`;
model-intelligence or Claude Code parity requires separate live unseen-task evidence.
"""
        readiness_file.write_text(readiness_content, encoding="utf-8")

    print("\nNexus release gate passed.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
