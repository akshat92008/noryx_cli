"""Machine-enforced architecture health checks for Nexus release artifacts.

The release gate intentionally validates both Python's import view and the physical
source layout. Import-only scans miss shadowed files such as ``module.py`` living
beside ``module/__init__.py``; source-only scans miss import-time failures. Nexus
requires both views to agree before an artifact can qualify.
"""

from __future__ import annotations

import ast
import importlib
import importlib.util
import pkgutil
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import nexus


@dataclass(frozen=True)
class ArchitectureCheck:
    name: str
    passed: bool
    detail: str
    failures: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ArchitectureHealthReport:
    passed: bool
    checks: tuple[ArchitectureCheck, ...]
    package_modules: int
    imported_modules: int
    source_root: str

    @property
    def failures(self) -> tuple[str, ...]:
        return tuple(
            failure
            for check in self.checks
            if not check.passed
            for failure in check.failures
        )

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "package_modules": self.package_modules,
            "imported_modules": self.imported_modules,
            "source_root": self.source_root,
            "checks": [check.to_dict() for check in self.checks],
            "failures": list(self.failures),
        }


_REQUIRED_MODULES = (
    "nexus.doctor",
    "nexus.model_doctor",
    "nexus.plugins.manifest",
    "nexus.release.qualification",
    "nexus.collaboration.worker_runtime",
    "nexus.execution.controller",
    "nexus.recovery.controller",
    "nexus.verified_repair",
    "nexus.intelligence.engineering.brain",
    "nexus.matched_benchmark",
    "nexus.offline_reliability_benchmark",
    "nexus.sbom",
    "nexus.hidden_benchmark",
)

# These paths remain compatibility imports only. They must never regain an
# independent implementation while downstream integrations still import them.
_FACADE_MODULES = {
    "nexus/cli/main.py": "nexus.cli.cli_impl",
    "nexus/cli/parser.py": "nexus.cli.cli_impl",
    "nexus/cli/commands.py": "nexus.cli.cli_impl",
    "nexus/cli/utils.py": "nexus.cli.cli_impl",
    "nexus/tools/core.py": "nexus.tools.tools_impl",
    "nexus/tools/filesystem.py": "nexus.tools.tools_impl",
    "nexus/tools/process.py": "nexus.tools.tools_impl",
    "nexus/tools/search.py": "nexus.tools.tools_impl",
    "nexus/tools/git.py": "nexus.tools.tools_impl",
    "nexus/tools/web.py": "nexus.tools.tools_impl",
    "nexus/tools/github.py": "nexus.tools.tools_impl",
    "nexus/tools/registry.py": "nexus.tools.tools_impl",
}

_CRITICAL_IMPLEMENTATION_FILES = (
    "nexus/doctor.py",
    "nexus/model_doctor.py",
    "nexus/release/qualification.py",
    "nexus/collaboration/worker_runtime.py",
    "nexus/execution/controller.py",
    "nexus/recovery/controller.py",
    "nexus/recovery/rollback.py",
    "nexus/verified_repair.py",
    "nexus/intelligence/engineering/brain.py",
    "nexus/intelligence/engineering/semantic.py",
    "nexus/matched_benchmark.py",
    "nexus/offline_reliability_benchmark.py",
    "nexus/sbom.py",
    "nexus/hidden_benchmark.py",
)

# These are regression ceilings, not an assertion that the current monoliths
# are ideal. They prevent new growth while the next refactor splits them behind
# stable contracts.
_MAX_FILE_LINES = 3_300
_MAX_FUNCTION_LINES = 500
_MAX_FUNCTION_BRANCHES = 130
_BRANCH_NODES = (
    ast.If,
    ast.IfExp,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.BoolOp,
    ast.Match,
    ast.comprehension,
)

_SECRET_PATTERNS = (
    re.compile(r"\bnvapi-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
)

_FAKE_SUCCESS_MARKERS = (
    "Stub" + " execution",
    "STUB" + "_PASS",
    '"status": "success", "message": "' + "Stub",
)


def _source_root(package_root: str | Path | None) -> Path:
    if package_root is not None:
        root = Path(package_root).expanduser().resolve()
        return root.parent if root.name == "nexus" else root
    return Path(nexus.__file__).resolve().parent.parent


def _module_name(root: Path, path: Path) -> str:
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _decorator_names(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names: set[str] = set()
    for decorator in node.decorator_list:
        if isinstance(decorator, ast.Name):
            names.add(decorator.id)
        elif isinstance(decorator, ast.Attribute):
            names.add(decorator.attr)
        elif isinstance(decorator, ast.Call):
            target = decorator.func
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
    return names


def _raises_not_implemented(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for child in ast.walk(node):
        if not isinstance(child, ast.Raise) or child.exc is None:
            continue
        target: ast.expr = child.exc
        if isinstance(target, ast.Call):
            target = target.func
        if isinstance(target, ast.Name) and target.id == "NotImplementedError":
            return True
        if isinstance(target, ast.Attribute) and target.attr == "NotImplementedError":
            return True
    return False


def _check_required_modules() -> ArchitectureCheck:
    failures: list[str] = []
    for name in _REQUIRED_MODULES:
        try:
            importlib.import_module(name)
        except Exception as exc:  # noqa: BLE001 - reported as release evidence.
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
    imported = len(_REQUIRED_MODULES) - len(failures)
    return ArchitectureCheck(
        "required_modules",
        not failures,
        f"{imported}/{len(_REQUIRED_MODULES)} required modules import",
        tuple(failures),
    )


def _check_facades(root: Path) -> ArchitectureCheck:
    failures: list[str] = []
    for relative, canonical in _FACADE_MODULES.items():
        path = root / relative
        if not path.is_file():
            failures.append(f"missing compatibility facade: {relative}")
            continue
        try:
            tree = _parse(path)
        except (OSError, SyntaxError) as exc:
            failures.append(f"invalid facade {relative}: {exc}")
            continue
        definitions = [
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        imported_modules = {
            node.module
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module
        }
        if canonical not in imported_modules:
            failures.append(f"{relative} does not delegate to {canonical}")
        if definitions:
            failures.append(
                f"{relative} contains independent definitions: {', '.join(definitions)}"
            )
    return ArchitectureCheck(
        "canonical_facades",
        not failures,
        f"{len(_FACADE_MODULES)} compatibility paths checked",
        tuple(failures),
    )


def _check_critical_implementations(root: Path) -> ArchitectureCheck:
    failures: list[str] = []
    for relative in _CRITICAL_IMPLEMENTATION_FILES:
        path = root / relative
        if not path.is_file():
            failures.append(f"missing critical implementation: {relative}")
            continue
        try:
            tree = _parse(path)
        except (OSError, SyntaxError) as exc:
            failures.append(f"invalid critical implementation {relative}: {exc}")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _raises_not_implemented(node) and "abstractmethod" not in _decorator_names(node):
                failures.append(
                    f"concrete NotImplementedError: {relative}:{node.lineno} ({node.name})"
                )
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker in _FAKE_SUCCESS_MARKERS:
            if marker in text:
                failures.append(f"fake-success marker {marker!r}: {relative}")
    return ArchitectureCheck(
        "critical_implementations",
        not failures,
        f"{len(_CRITICAL_IMPLEMENTATION_FILES)} critical runtime paths checked",
        tuple(failures),
    )


def _check_entrypoint(root: Path) -> ArchitectureCheck:
    init_path = root / "nexus" / "cli" / "__init__.py"
    pyproject = root / "pyproject.toml"
    failures: list[str] = []
    if not init_path.is_file() or "cli_impl" not in init_path.read_text(encoding="utf-8"):
        failures.append("nexus.cli does not export the canonical cli_impl runtime")

    entrypoint_ok = False
    if pyproject.is_file():
        entrypoint_ok = 'nexus = "nexus.cli:main"' in pyproject.read_text(encoding="utf-8")
    else:
        try:
            from importlib.metadata import distribution

            dist = distribution("nexusai-cli")
            entrypoint_ok = any(
                item.group == "console_scripts"
                and item.name == "nexus"
                and item.value == "nexus.cli:main"
                for item in dist.entry_points
            )
        except Exception:  # noqa: BLE001 - reported as a failed release check.
            entrypoint_ok = False
    if not entrypoint_ok:
        failures.append("console entrypoint is not bound to nexus.cli:main")
    return ArchitectureCheck(
        "console_entrypoint",
        not failures,
        "Console entrypoint resolves through the canonical CLI runtime",
        tuple(failures),
    )


def _check_source_layout(root: Path) -> ArchitectureCheck:
    package = root / "nexus"
    failures: list[str] = []
    module_paths: dict[str, list[str]] = {}
    parsed: dict[Path, ast.Module] = {}

    if not package.is_dir():
        return ArchitectureCheck(
            "source_layout_integrity",
            False,
            "Nexus package source is missing",
            (f"missing package directory: {package}",),
        )

    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(root).as_posix()
        module_paths.setdefault(_module_name(root, path), []).append(relative)
        try:
            parsed[path] = _parse(path)
        except (OSError, SyntaxError) as exc:
            failures.append(f"unparseable source module: {relative}: {exc}")

    for module, paths in sorted(module_paths.items()):
        if len(paths) > 1:
            failures.append(f"module/package collision for {module}: {', '.join(paths)}")
            continue
        try:
            spec = importlib.util.find_spec(module)
        except (ImportError, AttributeError, ModuleNotFoundError, ValueError) as exc:
            failures.append(f"unreachable source module: {paths[0]} -> {module}: {exc}")
            continue
        if spec is None:
            failures.append(f"unreachable source module: {paths[0]} -> {module}")

    for path, tree in parsed.items():
        relative = path.relative_to(root).as_posix()
        top_level_names: dict[str, list[int]] = {}
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                top_level_names.setdefault(node.name, []).append(node.lineno)
        for name, lines in sorted(top_level_names.items()):
            if len(lines) > 1:
                failures.append(
                    f"duplicate top-level definition: {relative}:{name} at lines "
                    + ", ".join(str(line) for line in lines)
                )
        text = path.read_text(encoding="utf-8", errors="replace")
        for marker in _FAKE_SUCCESS_MARKERS:
            if marker in text:
                failures.append(f"fake-success marker {marker!r}: {relative}")
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _raises_not_implemented(node) and "abstractmethod" not in _decorator_names(node):
                failures.append(
                    f"concrete NotImplementedError: {relative}:{node.lineno} ({node.name})"
                )

    return ArchitectureCheck(
        "source_layout_integrity",
        not failures,
        f"{len(module_paths)} source module paths checked",
        tuple(failures),
    )


def _check_complexity_budgets(root: Path) -> ArchitectureCheck:
    package = root / "nexus"
    failures: list[str] = []
    file_count = 0
    function_count = 0

    for path in sorted(package.rglob("*.py")):
        file_count += 1
        relative = path.relative_to(root).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        line_count = len(text.splitlines())
        if line_count > _MAX_FILE_LINES:
            failures.append(
                f"file complexity budget exceeded: {relative} has {line_count} lines "
                f"(limit {_MAX_FILE_LINES})"
            )
        try:
            tree = ast.parse(text, filename=str(path))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            function_count += 1
            end_line = getattr(node, "end_lineno", node.lineno)
            function_lines = max(1, end_line - node.lineno + 1)
            branch_count = sum(isinstance(child, _BRANCH_NODES) for child in ast.walk(node))
            if function_lines > _MAX_FUNCTION_LINES:
                failures.append(
                    f"function size budget exceeded: {relative}:{node.lineno} {node.name} "
                    f"has {function_lines} lines (limit {_MAX_FUNCTION_LINES})"
                )
            if branch_count > _MAX_FUNCTION_BRANCHES:
                failures.append(
                    f"function branch budget exceeded: {relative}:{node.lineno} {node.name} "
                    f"has {branch_count} branches (limit {_MAX_FUNCTION_BRANCHES})"
                )

    return ArchitectureCheck(
        "complexity_budgets",
        not failures,
        f"{file_count} files and {function_count} functions checked against regression ceilings",
        tuple(failures),
    )


def scan_source_secrets(
    root: str | Path,
    *,
    include_roots: Iterable[str] = ("nexus", "scripts"),
) -> tuple[bool, tuple[str, ...]]:
    """Scan production source for high-confidence embedded credential material."""
    base = Path(root).expanduser().resolve()
    findings: list[str] = []
    for relative_root in include_roots:
        scan_root = base / relative_root
        if not scan_root.exists():
            continue
        for path in scan_root.rglob("*"):
            if not path.is_file() or path.suffix in {".pyc", ".zip", ".whl", ".png", ".jpg"}:
                continue
            if "__pycache__" in path.parts:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for pattern in _SECRET_PATTERNS:
                if pattern.search(text):
                    findings.append(str(path.relative_to(base)))
                    break
    return not findings, tuple(sorted(set(findings)))


def run_architecture_health(package_root: str | Path | None = None) -> ArchitectureHealthReport:
    root = _source_root(package_root)
    modules = sorted(module.name for module in pkgutil.walk_packages(nexus.__path__, prefix="nexus."))
    import_failures: list[str] = []
    imported = 0
    for name in modules:
        try:
            importlib.import_module(name)
            imported += 1
        except Exception as exc:  # noqa: BLE001 - reported as release evidence.
            import_failures.append(f"{name}: {type(exc).__name__}: {exc}")
    import_check = ArchitectureCheck(
        "package_imports",
        not import_failures,
        f"{imported}/{len(modules)} packaged modules import successfully",
        tuple(import_failures),
    )
    checks = (
        import_check,
        _check_required_modules(),
        _check_facades(root),
        _check_critical_implementations(root),
        _check_source_layout(root),
        _check_complexity_budgets(root),
        _check_entrypoint(root),
    )
    return ArchitectureHealthReport(
        passed=all(check.passed for check in checks),
        checks=checks,
        package_modules=len(modules),
        imported_modules=imported,
        source_root=str(root),
    )
