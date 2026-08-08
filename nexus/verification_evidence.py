"""Structured, fail-closed verification evidence for test execution.

This module deliberately separates *safe command execution* from *valid test
verification*.  A command may be safe and return zero without proving that any
relevant test executed.  Completion gates consume the structured profiles and
validation results produced here instead of relying on command-text substrings.
"""
from __future__ import annotations

import ast
import hashlib
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

_SHELL_OPERATORS = {"&&", "||", ";", "|", "&", ">", ">>", "<", "<<"}
_TEST_DIRS = {"tests", "test", "spec", "specs", "__tests__"}


@dataclass(frozen=True)
class TestCommandProfile:
    valid: bool
    runner: str = ""
    scope: str = "unknown"  # full_suite | targeted | script | unknown
    targets: tuple[str, ...] = ()
    normalized_command: str = ""
    reason: str = ""
    project_gate: bool = False


def _normalise_target(value: str) -> str:
    value = str(value or "").strip().strip("'\"").replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value.rstrip("/")


def _profile(*, valid: bool, runner: str = "", scope: str = "unknown",
             targets: Iterable[str] = (), command: str = "", reason: str = "") -> TestCommandProfile:
    normalized_targets = tuple(dict.fromkeys(_normalise_target(item) for item in targets if _normalise_target(item)))
    return TestCommandProfile(
        valid=valid,
        runner=runner,
        scope=scope,
        targets=normalized_targets,
        normalized_command=command,
        reason=reason,
        project_gate=bool(valid and scope == "full_suite"),
    )


def _tokens(command: str | Sequence[str]) -> tuple[list[str], str, str]:
    if isinstance(command, (list, tuple)):
        values = [str(item) for item in command]
        return values, shlex.join(values), ""
    text = str(command or "").strip()
    if not text:
        return [], "", "empty verification command"
    if "\n" in text or "\r" in text or "`" in text or "$(" in text:
        return [], text, "compound or interpolated shell command is not valid test evidence"
    try:
        values = shlex.split(text, comments=True, posix=True)
    except ValueError as exc:
        return [], text, f"invalid command quoting: {exc}"
    if any(token in _SHELL_OPERATORS for token in values):
        return [], text, "compound shell commands cannot satisfy test verification"
    return values, shlex.join(values), ""


def _looks_like_path(token: str) -> bool:
    token = _normalise_target(token)
    if not token or token.startswith("-") or token.startswith("selector:"):
        return False
    if "::" in token:
        return True
    suffix = Path(token).suffix.lower()
    return "/" in token or suffix in {".py", ".js", ".jsx", ".ts", ".tsx", ".rb", ".php", ".go", ".rs"}


def _pytest_targets(args: list[str]) -> list[str]:
    targets: list[str] = []
    options_with_values = {
        "-k", "-m", "--maxfail", "--tb", "--capture", "--rootdir", "--confcutdir",
        "--basetemp", "--junitxml", "--cov", "--cov-report", "--durations",
    }
    skip_next = False
    for token in args:
        if skip_next:
            skip_next = False
            continue
        if token in options_with_values:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        if _looks_like_path(token):
            targets.append(_normalise_target(token))
        elif "::" in token:
            targets.append(_normalise_target(token))
    return targets


def analyse_test_command(command: str | Sequence[str], *, root: str | Path | None = None) -> TestCommandProfile:
    """Recognise an actual supported test-runner invocation.

    The parser rejects command chaining, redirection, interpolation, and generic
    commands that merely contain words such as ``pytest`` or ``test``.
    """
    argv, normalized, error = _tokens(command)
    if error:
        return _profile(valid=False, command=normalized, reason=error)
    if not argv:
        return _profile(valid=False, command=normalized, reason="empty verification command")

    exe = Path(argv[0]).name.lower()
    args = argv[1:]

    # python -m pytest / unittest
    if exe.startswith("python") or exe in {"py", "pypy", "pypy3"}:
        if len(args) >= 2 and args[0] == "-m" and args[1] in {"pytest", "unittest"}:
            runner = args[1]
            tail = args[2:]
            if runner == "pytest":
                targets = _pytest_targets(tail)
                scope = "targeted" if targets or any(t in {"-k", "-m"} for t in tail) else "full_suite"
            else:
                selectors = [item for item in tail if not item.startswith("-") and item != "discover"]
                targets = [_normalise_target(item) for item in selectors]
                scope = "targeted" if targets or "discover" not in tail and selectors else "full_suite"
            return _profile(valid=True, runner=runner, scope=scope, targets=targets, command=normalized)

        # A direct repository assertion script may be accepted only when it is
        # workspace-contained and visibly intended for verification.
        script_args = [item for item in args if item != "-u"]
        if script_args and not script_args[0].startswith("-"):
            script = _normalise_target(script_args[0])
            candidate = Path(script)
            if root is not None:
                base = Path(root).expanduser().resolve()
                candidate = (base / script).resolve()
                try:
                    candidate.relative_to(base)
                except ValueError:
                    return _profile(valid=False, command=normalized, reason="verification script escapes the workspace")
            stem = Path(script).stem.lower()
            if Path(script).suffix.lower() == ".py" and stem.startswith(("test", "verify", "check")):
                if root is None or candidate.is_file():
                    return _profile(valid=True, runner="python_script", scope="script", targets=(script,), command=normalized)
        return _profile(valid=False, command=normalized, reason="python command is not a recognised test invocation")

    if exe in {"pytest", "py.test"}:
        targets = _pytest_targets(args)
        scope = "targeted" if targets or any(t in {"-k", "-m"} for t in args) else "full_suite"
        return _profile(valid=True, runner="pytest", scope=scope, targets=targets, command=normalized)

    if exe in {"npm", "npm.cmd", "yarn", "yarn.cmd", "pnpm", "pnpm.cmd", "bun", "bun.exe"}:
        package_runner = exe.split(".", 1)[0]
        if args and args[0] in {"test", "t"}:
            tail = args[1:]
            # selectors after '--' are targeted; other runner-specific flags do
            # not by themselves prove a subset.
            targets = []
            if "--" in tail:
                targets = [_normalise_target(x) for x in tail[tail.index("--") + 1:] if not x.startswith("-")]
            return _profile(valid=True, runner=f"{package_runner}_test", scope="targeted" if targets else "full_suite", targets=targets, command=normalized)
        if package_runner == "yarn" and args and args[0] == "run" and len(args) > 1 and args[1] == "test":
            return _profile(valid=True, runner="yarn_test", scope="full_suite", command=normalized)
        return _profile(valid=False, command=normalized, reason="package-manager command is not a test invocation")

    if exe == "cargo" and args and args[0] == "test":
        targets = [f"selector:{item}" for item in args[1:] if item and not item.startswith("-") and item != "--"]
        return _profile(valid=True, runner="cargo_test", scope="targeted" if targets else "full_suite", targets=targets, command=normalized)

    if exe == "go" and args and args[0] == "test":
        targets = [_normalise_target(item) for item in args[1:] if item and not item.startswith("-")]
        full = not targets or targets == ["./..."]
        return _profile(valid=True, runner="go_test", scope="full_suite" if full else "targeted", targets=() if full else targets, command=normalized)

    if exe in {"mvn", "mvnw", "mvnw.cmd"} or exe.endswith("mvnw"):
        if any(item in {"test", "verify"} for item in args):
            targeted = [item.split("=", 1)[1] for item in args if item.startswith("-Dtest=") and "=" in item]
            return _profile(valid=True, runner="maven_test", scope="targeted" if targeted else "full_suite", targets=targeted, command=normalized)

    if exe in {"gradle", "gradlew", "gradlew.bat"} or exe.endswith("gradlew"):
        if any(item == "test" or item.endswith(":test") for item in args):
            targeted = [args[i + 1] for i, item in enumerate(args[:-1]) if item == "--tests"]
            return _profile(valid=True, runner="gradle_test", scope="targeted" if targeted else "full_suite", targets=targeted, command=normalized)

    if exe == "bundle" and len(args) >= 2 and args[0] == "exec" and args[1] == "rspec":
        targets = [_normalise_target(item) for item in args[2:] if _looks_like_path(item)]
        return _profile(valid=True, runner="rspec", scope="targeted" if targets else "full_suite", targets=targets, command=normalized)
    if exe == "rspec":
        targets = [_normalise_target(item) for item in args if _looks_like_path(item)]
        return _profile(valid=True, runner="rspec", scope="targeted" if targets else "full_suite", targets=targets, command=normalized)

    if exe in {"phpunit", "vendor/bin/phpunit"} or exe.endswith("phpunit"):
        targets = [_normalise_target(item) for item in args if _looks_like_path(item)]
        return _profile(valid=True, runner="phpunit", scope="targeted" if targets else "full_suite", targets=targets, command=normalized)

    if exe in {"dart", "flutter"} and args and args[0] == "test":
        targets = [_normalise_target(item) for item in args[1:] if _looks_like_path(item)]
        return _profile(valid=True, runner=f"{exe}_test", scope="targeted" if targets else "full_suite", targets=targets, command=normalized)

    if exe == "mix" and args and args[0] == "test":
        targets = [_normalise_target(item) for item in args[1:] if _looks_like_path(item)]
        return _profile(valid=True, runner="mix_test", scope="targeted" if targets else "full_suite", targets=targets, command=normalized)

    return _profile(valid=False, command=normalized, reason="command is not a recognised test runner")


_COUNT_PATTERNS = (
    re.compile(r"\bRan\s+(\d+)\s+tests?\b", re.I),
    re.compile(r"\b(\d+)\s+passed\b", re.I),
    re.compile(r"\bTests:\s+.*?\b(\d+)\s+total\b", re.I),
    re.compile(r"\b(\d+)\s+tests? completed\b", re.I),
    re.compile(r"\b(\d+)\s+examples?,\s+0\s+failures?\b", re.I),
    re.compile(r"\bTests run:\s*(\d+)\b", re.I),
    re.compile(r"\b(\d+)\s+tests?,\s*0\s+failures?\b", re.I),
    re.compile(r"\btest result:\s*ok\.\s*(\d+)\s+passed\b", re.I),
)
_ZERO_PATTERNS = (
    re.compile(r"\bRan\s+0\s+tests?\b", re.I),
    re.compile(r"\b0\s+passed\b", re.I),
    re.compile(r"\bTests:\s+0\s+total\b", re.I),
    re.compile(r"\b0\s+examples?\b", re.I),
    re.compile(r"\bTests run:\s*0\b", re.I),
    re.compile(r"\btest result:\s*ok\.\s*0\s+passed\b", re.I),
)


def observed_test_count(output: str) -> int | None:
    text = str(output or "")
    counts = [int(match.group(1)) for pattern in _COUNT_PATTERNS for match in pattern.finditer(text)]
    positive = [count for count in counts if count > 0]
    if positive:
        return max(positive)
    if counts or any(pattern.search(text) for pattern in _ZERO_PATTERNS):
        return 0
    return None


def _python_script_assertion_count(profile: TestCommandProfile, root: str | Path | None) -> int:
    if profile.runner != "python_script" or not profile.targets or root is None:
        return 0
    base = Path(root).expanduser().resolve()
    candidate = (base / profile.targets[0]).resolve()
    try:
        candidate.relative_to(base)
        tree = ast.parse(candidate.read_text(encoding="utf-8"), filename=str(candidate))
    except (ValueError, OSError, UnicodeError, SyntaxError):
        return 0
    count = sum(isinstance(node, ast.Assert) for node in ast.walk(tree))
    assertion_calls = {
        "assertEqual", "assertNotEqual", "assertTrue", "assertFalse", "assertIs",
        "assertIsNot", "assertIn", "assertNotIn", "assertRaises", "fail",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else node.func.attr if isinstance(node.func, ast.Attribute) else ""
        if name in assertion_calls:
            count += 1
    return count


def _runner_success_marker(profile: TestCommandProfile, output: str) -> bool:
    text = str(output or "")
    patterns: dict[str, tuple[str, ...]] = {
        "go_test": (r"(?m)^ok\s+\S+",),
        "cargo_test": (r"test result:\s*ok\.",),
        "maven_test": (r"BUILD SUCCESS", r"Tests run:\s*[1-9]\d*"),
        "gradle_test": (r"BUILD SUCCESSFUL", r"> Task .*test"),
        "rspec": (r"\b[1-9]\d* examples?, 0 failures?\b",),
        "phpunit": (r"\bOK \([1-9]\d* tests?",),
        "mix_test": (r"\b[1-9]\d* tests?, 0 failures?\b",),
        "dart_test": (r"All tests passed!",),
        "flutter_test": (r"All tests passed!",),
        "npm_test": (r"Test Suites:\s+.*passed", r"Tests:\s+.*passed"),
        "yarn_test": (r"Test Suites:\s+.*passed", r"Tests:\s+.*passed"),
        "pnpm_test": (r"Test Suites:\s+.*passed", r"Tests:\s+.*passed"),
        "bun_test": (r"\b[1-9]\d* pass\b",),
    }
    options = patterns.get(profile.runner, ())
    return bool(options) and all(re.search(pattern, text, re.I | re.M) for pattern in options)


def validate_test_execution(profile: TestCommandProfile, *, output: str, exit_code: int | None,
                            require_observed_tests: bool = True,
                            root: str | Path | None = None) -> tuple[bool, str, int | None]:
    if not profile.valid:
        return False, profile.reason or "unrecognised test command", None
    if exit_code != 0:
        return False, f"test runner exited with status {exit_code}", observed_test_count(output)
    count = observed_test_count(output)
    if count == 0:
        return False, "test runner executed zero tests", 0
    if count is not None and count > 0:
        return True, f"observed {count} executed tests", count
    script_assertions = _python_script_assertion_count(profile, root)
    if profile.scope == "script" and script_assertions > 0:
        return True, f"executed assertion script with {script_assertions} assertion(s)", script_assertions
    if _runner_success_marker(profile, output):
        return True, "observed runner-specific success evidence", None
    if require_observed_tests:
        return False, "test runner returned success without observable executed-test evidence", None
    return True, "recognised test runner exited successfully", None


def _looks_like_test_path(path: str) -> bool:
    normalized = _normalise_target(path).split("::", 1)[0]
    name = Path(normalized).name.lower()
    parts = {part.lower() for part in Path(normalized).parts}
    return (
        name.startswith(("test_", "verify_", "check_"))
        or name.endswith(("_test.py", ".test.js", ".test.jsx", ".test.ts", ".test.tsx", ".spec.js", ".spec.jsx", ".spec.ts", ".spec.tsx"))
        or bool(parts & _TEST_DIRS)
    )


def _current_test_paths(root: str | Path) -> set[str]:
    base = Path(root).expanduser().resolve()
    ignored = {".git", ".nexusai", ".pytest_cache", ".ruff_cache", ".mypy_cache", "__pycache__", "node_modules", ".venv", "venv", "dist", "build", "target", ".gradle"}
    found: set[str] = set()
    for path in base.rglob("*"):
        if not path.is_file():
            continue
        try:
            relative = path.relative_to(base)
        except ValueError:
            continue
        if any(part in ignored for part in relative.parts):
            continue
        value = relative.as_posix()
        if _looks_like_test_path(value):
            found.add(value)
    return found


def _path_provenance(path: str, expected: dict[str, str | None], root: str | Path | None) -> str:
    normalized = _normalise_target(path).split("::", 1)[0]
    if normalized not in expected:
        if root is not None and (Path(root).expanduser().resolve() / normalized).is_file():
            return "model_generated"
        return "unknown"
    expected_digest = expected[normalized]
    if expected_digest is None:
        return "model_generated"
    if root is None:
        return "pre_existing"
    candidate = (Path(root).expanduser().resolve() / normalized).resolve()
    try:
        candidate.relative_to(Path(root).expanduser().resolve())
    except ValueError:
        return "unknown"
    if not candidate.is_file():
        return "unknown"
    try:
        current_digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    except OSError:
        return "unknown"
    return "pre_existing" if current_digest == expected_digest else "modified_pre_existing"


def test_origin_for_profile(profile: TestCommandProfile, expected_hashes: dict[str, str | None],
                            *, root: str | Path | None = None) -> str:
    if not profile.valid:
        return "unknown"
    expected = {_normalise_target(path): digest for path, digest in expected_hashes.items()}
    explicit = {_normalise_target(target).split("::", 1)[0] for target in profile.targets if target and not target.startswith("selector:")}
    if profile.scope == "full_suite":
        paths = _current_test_paths(root) if root is not None else {p for p, digest in expected.items() if digest is not None and _looks_like_test_path(p)}
    else:
        paths = explicit
    if not paths:
        return "unknown"
    states = {_path_provenance(path, expected, root) for path in paths}
    if states == {"pre_existing"}:
        return "pre_existing"
    if states == {"model_generated"}:
        return "model_generated"
    if states == {"modified_pre_existing"}:
        return "modified_pre_existing"
    if states == {"unknown"}:
        return "unknown"
    return "mixed"


def verification_identity(item: dict[str, Any]) -> str:
    metadata = item.get("metadata", {}) or {}
    check_type = str(metadata.get("check_type") or "")
    if check_type in {"test", "tests"}:
        targets = tuple(sorted(str(value) for value in (metadata.get("test_targets", []) or [])))
        return f"test|{metadata.get('test_runner') or 'unknown'}|{metadata.get('verification_scope') or 'unknown'}|{targets!r}"
    command = str(item.get("command") or "")
    fingerprint = str(metadata.get("command_fingerprint") or hashlib.sha256(command.encode()).hexdigest())
    return f"{check_type}|{fingerprint}"


def effective_verification_evidence(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return current evidence without allowing narrow passes to erase broad failures."""
    latest: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in records:
        identity = verification_identity(item)
        if identity not in latest:
            order.append(identity)
        latest[identity] = item
    selected = [latest[key] for key in order]

    # A validated full-suite pass at a revision covers targeted obligations for
    # the same runner and revision.  The inverse is intentionally impossible.
    broad_passes = [
        item for item in selected
        if item.get("status") == "verified"
        and (item.get("metadata") or {}).get("verification_scope") == "full_suite"
        and (item.get("metadata") or {}).get("verification_valid") is True
    ]
    covered: set[str] = set()
    for broad in broad_passes:
        meta = broad.get("metadata") or {}
        runner = meta.get("test_runner")
        revision = meta.get("workspace_revision")
        for item in selected:
            imeta = item.get("metadata") or {}
            if item is broad:
                continue
            if imeta.get("check_type") not in {"test", "tests"}:
                continue
            if imeta.get("verification_scope") != "targeted":
                continue
            if imeta.get("test_runner") == runner and imeta.get("workspace_revision") == revision:
                covered.add(str(item.get("id")))
    return [item for item in selected if str(item.get("id")) not in covered]


def command_fingerprint(command: str) -> str:
    return hashlib.sha256(str(command).encode("utf-8")).hexdigest()
