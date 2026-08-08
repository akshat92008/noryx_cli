"""
Verification Engine — auto-detects project type and runs appropriate checks
(tests, linting, type checking, compilation) after code changes.

Architecture:
    Detect Project Type → Select Checks → Execute → Report Results
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field, replace
from enum import Enum
from pathlib import Path

from nexus.verification_evidence import analyse_test_command, validate_test_execution


class CheckType(str, Enum):
    """Types of verification checks."""

    TEST = "test"
    SYNTAX = "syntax"
    IMPORTS = "imports"
    LINT = "lint"
    TYPE_CHECK = "type_check"
    BUILD = "build"
    FORMAT = "format"
    SECURITY = "security"
    API = "api"
    BROWSER = "browser"
    DATABASE = "database"


class CheckStatus(str, Enum):
    """Status of a verification check."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"
    NOT_APPLICABLE = "not_applicable"
    INHERITED = "inherited_failure"


@dataclass
class CheckResult:
    """Result of a single verification check."""

    check_type: CheckType
    status: CheckStatus
    command: str
    output: str = ""
    error_count: int = 0
    warning_count: int = 0
    duration_ms: int = 0

    @property
    def passed(self) -> bool:
        return self.status in {CheckStatus.PASSED, CheckStatus.INHERITED}

    def format_result(self) -> str:
        icons = {
            CheckStatus.PASSED: "✅",
            CheckStatus.FAILED: "❌",
            CheckStatus.SKIPPED: "⏭️",
            CheckStatus.ERROR: "💥",
            CheckStatus.NOT_APPLICABLE: "➖",
            CheckStatus.INHERITED: "⚠️",
        }
        icon = icons.get(self.status, "❓")
        line = f"{icon} {self.check_type.value}: {self.status.value}"
        if self.error_count:
            line += f" ({self.error_count} errors)"
        if self.warning_count:
            line += f" ({self.warning_count} warnings)"
        if self.command:
            line += f"  [{self.command}]"
        return line


@dataclass
class VerificationReport:
    """Full report from a verification run."""

    project_type: str
    checks: list[CheckResult] = field(default_factory=list)
    all_passed: bool = False

    def format_report(self) -> str:
        lines = [f"🔍 Verification Report — {self.project_type}"]
        lines.append("")
        for check in self.checks:
            lines.append(f"  {check.format_result()}")
            if check.output:
                for output_line in check.output.splitlines():
                    lines.append(f"    │ {output_line}")

        passed = sum(1 for c in self.checks if c.passed)
        total = len(self.checks)
        lines.append("")
        lines.append(f"  Summary: {passed}/{total} checks passed")

        return "\n".join(lines)


# ── Project Type Detection ───────────────────────────────────────────────────

_DEFAULT_COMMANDS: dict[str, dict[str, str]] = {
    "python": {
        "test": "python -m pytest -x -q",
        "lint": "ruff check . --no-fix",
        "type_check": "mypy . --ignore-missing-imports",
        "format": "ruff format --check .",
    },
    "javascript": {
        "test": "npm test",
        "lint": "npx eslint . --no-error-on-unmatched-pattern",
        "type_check": "npx tsc --noEmit",
        "build": "npm run build",
    },
    "typescript": {
        "test": "npm test",
        "lint": "npx eslint . --no-error-on-unmatched-pattern",
        "type_check": "npx tsc --noEmit",
        "build": "npm run build",
    },
    "rust": {
        "test": "cargo test --quiet",
        "lint": "cargo clippy --quiet -- -D warnings",
        "build": "cargo check --quiet",
        "format": "cargo fmt --check",
    },
    "go": {
        "test": "go test ./... -count=1",
        "lint": "go vet ./...",
        "build": "go build ./...",
        "format": "gofmt -l .",
    },
    "java": {
        "test": "mvn test -q",
        "build": "mvn compile -q",
    },
    "ruby": {
        "test": "bundle exec rspec --no-color",
        "lint": "bundle exec rubocop --no-color",
    },
    "php": {
        "test": "vendor/bin/phpunit",
        "lint": "vendor/bin/phpstan analyse",
    },
    "dart": {
        "test": "dart test",
        "lint": "dart analyze",
        "format": "dart format --set-exit-if-changed .",
    },
    "elixir": {
        "test": "mix test",
        "lint": "mix credo",
        "build": "mix compile --warnings-as-errors",
    },
}


def _shell_executable(value: str, platform: str | None = None) -> str:
    """Quote an executable for the platform shell used by ``SandboxRunner``."""

    return subprocess.list2cmdline([value]) if (platform or os.name) == "nt" else shlex.quote(value)


class VerificationEngine:
    """
    Auto-detects project type and runs appropriate verification checks.

    Supports custom commands from NEXUS.md project rules, falling back to
    sensible defaults for each language/framework.

    Usage:
        engine = VerificationEngine("/path/to/project")
        report = engine.run_all()
        print(report.format_report())

        # Or run specific checks
        result = engine.run_check(CheckType.TEST)
        result = engine.run_check(CheckType.LINT)
    """

    def __init__(
        self,
        working_dir: str,
        custom_commands: dict[str, str] | None = None,
        *,
        require_os_isolation: bool = False,
        allow_unisolated_host_process: bool = False,
        timeout_seconds: float | None = None,
    ):
        self.root = Path(working_dir).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError(f"Verification root does not exist: {self.root}")
        self.working_dir = str(self.root)
        self.project_type = self._detect_project_type()
        self.commands = self._resolve_commands(custom_commands or {})
        self.require_os_isolation = bool(require_os_isolation)
        self.allow_unisolated_host_process = bool(allow_unisolated_host_process)
        configured_timeout = timeout_seconds or float(os.getenv("NEXUS_VERIFY_TIMEOUT", "300"))
        self.timeout_seconds = min(1800.0, max(30.0, float(configured_timeout)))

    def verify_syntax(self) -> CheckResult:
        """Compile Python sources without importing or executing project code."""
        import time

        start = time.time()
        if not any(self._iter_python_files()):
            return CheckResult(
                CheckType.SYNTAX,
                CheckStatus.NOT_APPLICABLE,
                "",
                duration_ms=int((time.time() - start) * 1000),
            )

        try:
            import sys
            # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
            result = subprocess.run(
                [os.environ.get("PYTHON", sys.executable), "-m", "compileall", "-q", "-f", "."],
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            passed = result.returncode == 0
            return CheckResult(
                check_type=CheckType.SYNTAX,
                status=CheckStatus.PASSED if passed else CheckStatus.FAILED,
                command="python -m compileall -q -f .",
                output=result.stderr or result.stdout,
                duration_ms=int((time.time() - start) * 1000),
            )
        except (ImportError, LookupError, OSError, RuntimeError, TypeError, ValueError) as e:
            return CheckResult(CheckType.SYNTAX, CheckStatus.ERROR, "", str(e))

    def verify_imports(self) -> CheckResult:
        """Resolve Python imports without importing or executing project code.

        Relative and repository-local imports are checked against the source
        tree.  Standard-library and installed third-party top-level modules are
        resolved with ``find_spec``.  This intentionally does not claim that a
        referenced attribute exists inside an external package.
        """
        import ast
        import importlib.util
        import sys
        import time

        start = time.time()

        paths = list(self._iter_python_files())
        if not paths:
            return CheckResult(
                CheckType.IMPORTS,
                CheckStatus.NOT_APPLICABLE,
                "",
                duration_ms=int((time.time() - start) * 1000),
            )

        errors: list[str] = []
        stdlib_modules = set(getattr(sys, "stdlib_module_names", ()))
        source_roots = [self.root]
        for candidate in (self.root / "src", self.root / "lib"):
            if candidate.is_dir():
                source_roots.append(candidate)
        local_top_levels = {
            path.name if path.is_dir() else path.stem
            for source_root in source_roots
            for path in source_root.iterdir()
            if (path.is_dir() and (path / "__init__.py").is_file())
            or (path.is_file() and path.suffix == ".py")
        }

        def local_module_exists(parts: list[str]) -> bool:
            if not parts:
                return True
            return any(
                (source_root.joinpath(*parts)).with_suffix(".py").is_file()
                or (source_root.joinpath(*parts) / "__init__.py").is_file()
                for source_root in source_roots
            )

        def relative_module_exists(source: Path, level: int, module: str | None) -> bool:
            package_parts = list(source.relative_to(self.root).parent.parts)
            ascend = max(0, level - 1)
            if ascend > len(package_parts):
                return False
            base = package_parts[: len(package_parts) - ascend]
            return local_module_exists([*base, *((module or "").split("."))])

        for path in paths:
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                for node in ast.walk(tree):
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            parts = alias.name.split(".")
                            top = parts[0]
                            if top in local_top_levels and not local_module_exists(parts):
                                errors.append(
                                    f"{path.relative_to(self.root)}: missing local import {alias.name}"
                                )
                            elif top not in local_top_levels and top not in stdlib_modules:
                                try:
                                    resolved = importlib.util.find_spec(top)
                                except (ImportError, AttributeError, ValueError):
                                    resolved = None
                                if resolved is None:
                                    errors.append(
                                        f"{path.relative_to(self.root)}: unresolved import {alias.name}"
                                    )
                    elif isinstance(node, ast.ImportFrom):
                        if node.level:
                            if not relative_module_exists(path, node.level, node.module):
                                dotted = "." * node.level + (node.module or "")
                                errors.append(
                                    f"{path.relative_to(self.root)}: missing relative import {dotted}"
                                )
                            continue
                        if not node.module:
                            continue
                        parts = node.module.split(".")
                        top = parts[0]
                        if top in local_top_levels and not local_module_exists(parts):
                            errors.append(
                                f"{path.relative_to(self.root)}: missing local import {node.module}"
                            )
                        elif top not in local_top_levels and top not in stdlib_modules:
                            try:
                                resolved = importlib.util.find_spec(top)
                            except (ImportError, AttributeError, ValueError):
                                resolved = None
                            if resolved is None:
                                errors.append(
                                    f"{path.relative_to(self.root)}: unresolved import {node.module}"
                                )
            except SyntaxError as e:
                errors.append(f"{path.relative_to(self.root)}: {e}")
            except (OSError, UnicodeError) as exc:
                errors.append(f"{path.relative_to(self.root)}: could not inspect imports: {exc}")

        passed = len(errors) == 0
        return CheckResult(
            check_type=CheckType.IMPORTS,
            status=CheckStatus.PASSED if passed else CheckStatus.FAILED,
            command="nexus:resolve_python_imports",
            output="\n".join(errors),
            error_count=len(errors),
            duration_ms=int((time.time() - start) * 1000),
        )

    def run_all(self, checks: list[CheckType] | None = None) -> VerificationReport:
        """Run all applicable verification checks."""
        if checks is None:
            checks = [
                CheckType.SYNTAX,
                CheckType.IMPORTS,
                CheckType.LINT,
                CheckType.TYPE_CHECK,
                CheckType.TEST,
                CheckType.BUILD,
                CheckType.SECURITY,
                CheckType.DATABASE,
                CheckType.API,
                CheckType.BROWSER,
            ]

        report = VerificationReport(project_type=self.project_type)

        for check_type in checks:
            result = self.run_check(check_type)
            if result.status != CheckStatus.NOT_APPLICABLE:
                report.checks.append(result)

        report.all_passed = bool(report.checks) and all(
            c.passed for c in report.checks if c.status != CheckStatus.NOT_APPLICABLE
        )

        return report

    def run_change_aware(
        self,
        changed_paths: list[str] | tuple[str, ...],
        *,
        impacted_tests: list[str] | tuple[str, ...] = (),
    ) -> VerificationReport:
        """Run fast, dependency-focused checks before the complete gate.

        A failing targeted test immediately returns high-signal evidence to the
        repair loop. When focused checks pass, Nexus still runs the complete
        project verification suite so optimization never weakens the release bar.
        """
        normalized = [
            str(Path(path))
            for path in changed_paths
            if str(path).strip()
        ]
        focused: list[CheckResult] = []
        if self.project_type == "python" and impacted_tests:
            interpreter = _shell_executable(os.environ.get("PYTHON") or sys.executable)
            tests = " ".join(shlex.quote(str(path)) for path in impacted_tests[:50])
            command = f"{interpreter} -m pytest -q -x {tests}"
            targeted = self._execute_check(CheckType.TEST, command, timeout_seconds=180)
            targeted.command = f"{command}  # targeted from {len(normalized)} changed files"
            focused.append(targeted)
            if not targeted.passed:
                report = VerificationReport(
                    project_type=self.project_type,
                    checks=focused,
                    all_passed=False,
                )
                return report

        report = self.run_all()
        if focused:
            report.checks = focused + report.checks
            report.all_passed = all(item.passed for item in report.checks)
        return report

    @staticmethod
    def _normalize_failure_output(output: str, roots: tuple[Path, ...] = ()) -> str:
        """Normalize nondeterministic details before baseline comparison.

        Verification output is compared for semantic equivalence, not terminal
        rendering. Pytest progress bars, wall-clock durations, transient temp-root
        counters, and memory addresses must never turn identical inherited debt
        into a new regression.
        """
        normalized = str(output or "").replace("\r\n", "\n").replace("\r", "\n")
        for root in roots:
            normalized = normalized.replace(str(root), "<WORKSPACE>")
        normalized = re.sub(r"pytest-(?:of-[^/]+/)?pytest-\d+", "pytest-<RUN>", normalized)
        normalized = re.sub(r"\b\d+(?:\.\d+)?s\b", "<TIME>", normalized)
        normalized = re.sub(r"duration_ms[=: ]+\d+", "duration_ms=<TIME>", normalized)
        normalized = re.sub(r"0x[0-9a-fA-F]+", "0x<ADDR>", normalized)
        normalized = re.sub(r"pid=\d+", "pid=<PID>", normalized)

        # For pytest, extract semantic signatures
        if "=================================== FAILURES ===================================" in normalized or "short test summary info" in normalized:
            signatures = []
            for line in normalized.splitlines():
                stripped = line.rstrip()
                if stripped.startswith("FAILED ") or stripped.startswith("ERROR "):
                    signatures.append(stripped)
                elif stripped.startswith("E   ") or stripped.startswith("E "):
                    signatures.append(stripped)
            if signatures:
                return "\n".join(signatures)

        stable_lines: list[str] = []
        for line in normalized.strip().splitlines():
            stripped = line.rstrip()
            # Terminal progress rendering is scheduling-dependent and carries no
            # failure identity. The failure sections and node IDs remain below.
            if re.fullmatch(r"[.FEsxX]+\s+\[\s*\d+%\]", stripped.strip()):
                continue
            if re.fullmatch(r"=+\s*(?:short test summary info|FAILURES|ERRORS)\s*=+", stripped.strip()):
                continue
            # Pytest's final aggregate count varies in timing only; individual
            # FAILED/ERROR node lines and assertion content are retained.
            if re.fullmatch(
                r"=+\s*\d+ (?:failed|passed|error|errors|skipped|xfailed|xpassed).*?=+",
                stripped.strip(),
            ):
                continue
            stable_lines.append(stripped)
        return "\n".join(stable_lines)

    def reconcile_with_baseline(
        self,
        report: VerificationReport,
        baseline_root: str | Path,
    ) -> VerificationReport:
        """Mark byte-equivalent pre-existing failures as inherited.

        Only deterministic development checks are eligible. Security, database,
        API, browser, syntax, and import failures always remain blocking.
        """
        eligible = {
            CheckType.TEST,
            CheckType.LINT,
            CheckType.TYPE_CHECK,
            CheckType.BUILD,
            CheckType.FORMAT,
        }
        baseline_path = Path(baseline_root).expanduser().resolve()
        if baseline_path == self.root or not baseline_path.is_dir():
            return report

        baseline_engine = VerificationEngine(
            str(baseline_path),
            custom_commands=dict(self.commands),
            require_os_isolation=self.require_os_isolation,
            allow_unisolated_host_process=self.allow_unisolated_host_process,
            timeout_seconds=self.timeout_seconds,
        )
        reconciled: list[CheckResult] = []
        for current in report.checks:
            if current.check_type not in eligible or current.status not in {
                CheckStatus.FAILED,
                CheckStatus.ERROR,
            }:
                reconciled.append(current)
                continue
            baseline = baseline_engine.run_check(current.check_type)
            if baseline.status not in {CheckStatus.FAILED, CheckStatus.ERROR}:
                reconciled.append(current)
                continue
            current_output = self._normalize_failure_output(
                current.output, (self.root, baseline_path)
            )
            baseline_output = self._normalize_failure_output(
                baseline.output, (self.root, baseline_path)
            )
            if current.command == baseline.command and current_output == baseline_output:
                reconciled.append(
                    replace(
                        current,
                        status=CheckStatus.INHERITED,
                        output=(
                            current.output.rstrip()
                            + "\n\n[NEXUS] Inherited baseline failure; no new regression detected."
                        ).strip(),
                    )
                )
            else:
                reconciled.append(current)

        report.checks = reconciled
        report.all_passed = bool(report.checks) and all(check.passed for check in report.checks)
        return report

    def run_check(self, check_type: CheckType) -> CheckResult:
        """Run a single verification check."""
        if check_type == CheckType.SYNTAX:
            return self.verify_syntax()
        if check_type == CheckType.IMPORTS:
            return self.verify_imports()
        if check_type == CheckType.SECURITY:
            return self._run_security()
        if check_type == CheckType.DATABASE:
            return self._run_database()
        if check_type == CheckType.API:
            return self._run_api()
        if check_type == CheckType.BROWSER:
            return self._run_browser()
        command = self.commands.get(check_type.value)

        if not command:
            return CheckResult(
                check_type=check_type,
                status=CheckStatus.NOT_APPLICABLE,
                command="",
            )

        return self._execute_check(check_type, command)

    def run_tests(self) -> CheckResult:
        """Shortcut to run just tests."""
        return self.run_check(CheckType.TEST)

    def run_lint(self) -> CheckResult:
        """Shortcut to run just linting."""
        return self.run_check(CheckType.LINT)

    def get_available_checks(self) -> list[CheckType]:
        """Get which checks are available for this project."""
        available = []
        for check_type in CheckType:
            if check_type.value in self.commands:
                available.append(check_type)
        return available

    # ── Private Methods ──────────────────────────────────────────────────

    def _detect_project_type(self) -> str:
        """Detect the project's primary language/framework."""
        root = Path(self.working_dir)

        indicators = {
            "python": ["pyproject.toml", "setup.py", "requirements.txt", "Pipfile"],
            "typescript": ["tsconfig.json"],
            "javascript": ["package.json"],
            "rust": ["Cargo.toml"],
            "go": ["go.mod"],
            "java": ["pom.xml", "build.gradle", "build.gradle.kts"],
            "ruby": ["Gemfile"],
            "php": ["composer.json"],
            "dart": ["pubspec.yaml"],
            "elixir": ["mix.exs"],
        }

        for lang, files in indicators.items():
            for filename in files:
                if (root / filename).exists():
                    # TypeScript check: package.json alone means JS
                    if lang == "javascript" and (root / "tsconfig.json").exists():
                        continue  # Will be caught as typescript
                    return lang

        return "unknown"

    def _iter_python_files(self):
        ignored = {
            ".git",
            ".nexusai",
            ".pytest_cache",
            ".ruff_cache",
            ".mypy_cache",
            "__pycache__",
            "node_modules",
            ".venv",
            "venv",
            "dist",
            "build",
        }
        for path in self.root.rglob("*.py"):
            relative = path.relative_to(self.root)
            if not any(part in ignored for part in relative.parts):
                yield path

    def _resolve_commands(self, custom: dict[str, str]) -> dict[str, str]:
        """Resolve verification commands, preferring custom over defaults."""
        defaults = _DEFAULT_COMMANDS.get(self.project_type, {})
        resolved = {**defaults}

        # Override with custom commands
        for key, cmd in custom.items():
            if cmd:
                # Map common aliases
                key_map = {
                    "test_command": "test",
                    "lint_command": "lint",
                    "build_command": "build",
                    "format_command": "format",
                }
                resolved_key = key_map.get(key, key)
                resolved[resolved_key] = cmd

        if self.project_type == "python":
            interpreter = _shell_executable(os.environ.get("PYTHON") or sys.executable)
            resolved = {
                key: (
                    interpreter + command[len("python") :]
                    if command.startswith("python ")
                    else command
                )
                for key, command in resolved.items()
            }

        # Check if tools are actually available (for Python projects)
        if self.project_type == "python":
            if not self._command_exists("ruff"):
                if "lint" in resolved and "ruff" in resolved["lint"]:
                    if self._python_module_exists("flake8"):
                        resolved["lint"] = "python -m flake8 ."
                    else:
                        resolved.pop("lint", None)
                if "format" in resolved and "ruff" in resolved["format"]:
                    resolved.pop("format", None)
            if not self._command_exists("mypy"):
                resolved.pop("type_check", None)

        return resolved

    def _python_module_exists(self, module: str) -> bool:
        """Check a Python module without importing it or masking failures."""
        try:
            # SECURITY CLASSIFICATION: INTERNAL_GIT_OP
            result = subprocess.run(
                [
                    os.environ.get("PYTHON", "python3"),
                    "-c",
                    f"import importlib.util; raise SystemExit(importlib.util.find_spec('{module}') is None)",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=self.working_dir,
            )
            return result.returncode == 0
        except (subprocess.TimeoutExpired, OSError):
            return False

    def _command_exists(self, cmd: str) -> bool:
        """Check if a command is available."""
        return shutil.which(cmd) is not None

    def _execute_check(
        self,
        check_type: CheckType,
        command: str,
        *,
        timeout_seconds: float | None = None,
    ) -> CheckResult:
        """Execute a verification command and parse the result."""
        import time

        from nexus.safety import SafetyLayer, SafetyLevel
        from nexus.sandbox import SandboxRunner

        start = time.monotonic()

        try:
            safety = SafetyLayer().check_command(command)
            if safety.level in {SafetyLevel.BLOCKED, SafetyLevel.DANGEROUS}:
                return CheckResult(
                    check_type=check_type,
                    status=CheckStatus.ERROR,
                    command=command,
                    output=f"Verification command rejected: {safety.reason}",
                )
            result = SandboxRunner(self.working_dir).run_shell(
                command,
                cwd=self.working_dir,
                timeout_seconds=timeout_seconds or self.timeout_seconds,
                network=False,
                require_os_isolation=self.require_os_isolation,
                allow_unisolated_host_process=self.allow_unisolated_host_process,
            )

            duration = int((time.monotonic() - start) * 1000)
            output = result.stdout
            if result.stderr:
                output += "\n" + result.stderr

            status = CheckStatus.PASSED if result.success else CheckStatus.FAILED
            if check_type == CheckType.TEST:
                profile = analyse_test_command(command, root=self.working_dir)
                valid, detail, _count = validate_test_execution(
                    profile,
                    output=output,
                    exit_code=result.exit_code,
                    root=self.working_dir,
                )
                if not valid:
                    status = CheckStatus.FAILED
                    output = (output.rstrip() + f"\n\n[NEXUS] Test evidence rejected: {detail}").strip()

            # Count errors and warnings from output
            error_count = output.lower().count("error")
            warning_count = output.lower().count("warning")

            return CheckResult(
                check_type=check_type,
                status=status,
                command=command,
                output=output.strip(),
                error_count=error_count,
                warning_count=warning_count,
                duration_ms=duration,
            )

        except (OSError, RuntimeError, TypeError, ValueError) as e:
            return CheckResult(
                check_type=check_type,
                status=CheckStatus.ERROR,
                command=command,
                output=f"Error: {e}",
            )

    def _run_security(self) -> CheckResult:
        from nexus.behavioral import SecurityScanner

        result = SecurityScanner().scan(self.working_dir)
        return CheckResult(
            check_type=CheckType.SECURITY,
            status=CheckStatus.PASSED if result.passed else CheckStatus.FAILED,
            command="nexus:security_scan",
            output=json.dumps(result.to_dict(), indent=2),
            error_count=len(result.evidence.get("findings", [])),
            duration_ms=result.duration_ms,
        )

    def _run_database(self) -> CheckResult:
        from nexus.behavioral import DatabaseVerifier

        root = Path(self.working_dir)
        databases = sorted(
            {
                *root.glob("*.db"),
                *root.glob("*.sqlite"),
                *root.glob("*.sqlite3"),
                *root.glob("data/*.db"),
                *root.glob("data/*.sqlite"),
            }
        )
        migration_files = sorted(
            {
                *root.glob("migrations/**/*.sql"),
                *root.glob("alembic/**/*.sql"),
                *root.glob("prisma/migrations/**/*.sql"),
            }
        )
        if not databases and not migration_files:
            return CheckResult(
                check_type=CheckType.DATABASE,
                status=CheckStatus.NOT_APPLICABLE,
                command="",
            )
        verifier = DatabaseVerifier()
        results = [verifier.verify_sqlite(path) for path in databases]
        migration_risks = []
        for path in migration_files:
            try:
                sql = path.read_text(encoding="utf-8")
            except OSError:
                continue
            for finding in verifier.migration_risks(sql):
                migration_risks.append({"path": str(path.relative_to(root)), **finding})
        passed = all(item.passed for item in results) and not migration_risks
        return CheckResult(
            check_type=CheckType.DATABASE,
            status=CheckStatus.PASSED if passed else CheckStatus.FAILED,
            command="nexus:database_check",
            output=json.dumps(
                {
                    "databases": [item.to_dict() for item in results],
                    "migration_risks": migration_risks,
                },
                indent=2,
            ),
            error_count=sum(not item.passed for item in results) + len(migration_risks),
            duration_ms=sum(item.duration_ms for item in results),
        )

    def _verification_config(self) -> dict:
        path = Path(self.working_dir) / ".nexus" / "verify.json"
        if not path.is_file():
            return {}
        from nexus.trust import TrustStore

        if not TrustStore(self.working_dir).is_approved(path):
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _run_api(self) -> CheckResult:
        from nexus.behavioral import ApiProbeSpec, ApiVerifier

        configured = self._verification_config().get("api", [])
        if not isinstance(configured, list) or not configured:
            return CheckResult(
                check_type=CheckType.API,
                status=CheckStatus.NOT_APPLICABLE,
                command="",
            )
        results = []
        for raw in configured:
            if not isinstance(raw, dict) or "url" not in raw:
                continue
            results.append(
                ApiVerifier().verify(
                    ApiProbeSpec(
                        method=str(raw.get("method", "GET")),
                        url=str(raw["url"]),
                        expected_status=int(raw.get("expected_status", 200)),
                        expected_json=raw.get("expected_json"),
                        expected_text=str(raw.get("expected_text", "")),
                        json_body=raw.get("json_body"),
                        allow_external=False,
                    )
                )
            )
        if not results:
            return CheckResult(
                check_type=CheckType.API,
                status=CheckStatus.ERROR,
                command="nexus:api_check",
                output="No valid API probes were configured.",
            )
        return CheckResult(
            check_type=CheckType.API,
            status=(
                CheckStatus.PASSED if all(item.passed for item in results) else CheckStatus.FAILED
            ),
            command="nexus:api_check",
            output=json.dumps([item.to_dict() for item in results], indent=2),
            error_count=sum(not item.passed for item in results),
            duration_ms=sum(item.duration_ms for item in results),
        )

    def _run_browser(self) -> CheckResult:
        from nexus.behavioral import BrowserProbeSpec, BrowserStep, BrowserVerifier

        configured = self._verification_config().get("browser", [])
        if not isinstance(configured, list) or not configured:
            return CheckResult(
                check_type=CheckType.BROWSER,
                status=CheckStatus.NOT_APPLICABLE,
                command="",
            )
        results = []
        for raw in configured:
            if not isinstance(raw, dict) or "url" not in raw:
                continue
            steps = tuple(
                BrowserStep(
                    action=str(item.get("action", "")),
                    selector=str(item.get("selector", "")),
                    value=str(item.get("value", "")),
                )
                for item in raw.get("steps", [])
                if isinstance(item, dict)
            )
            results.append(
                BrowserVerifier().verify(
                    BrowserProbeSpec(
                        url=str(raw["url"]),
                        steps=steps,
                        screenshot_path=str(raw.get("screenshot_path", "")),
                        allow_external=False,
                    )
                )
            )
        if not results:
            return CheckResult(
                check_type=CheckType.BROWSER,
                status=CheckStatus.ERROR,
                command="nexus:browser_check",
                output="No valid browser probes were configured.",
            )
        return CheckResult(
            check_type=CheckType.BROWSER,
            status=(
                CheckStatus.PASSED if all(item.passed for item in results) else CheckStatus.FAILED
            ),
            command="nexus:browser_check",
            output=json.dumps([item.to_dict() for item in results], indent=2),
            error_count=sum(not item.passed for item in results),
            duration_ms=sum(item.duration_ms for item in results),
        )
