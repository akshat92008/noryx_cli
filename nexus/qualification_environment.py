"""Release-environment validation against Noryx' declared dependency contract."""

from __future__ import annotations

import importlib.metadata
import json
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 compatibility
    import tomli as tomllib

try:
    from packaging.requirements import Requirement
    from packaging.utils import canonicalize_name
except ImportError as exc:  # pragma: no cover - release environments install dev extras
    raise RuntimeError("qualification requires the 'packaging' distribution") from exc

from nexus.provenance import resolve_source_identity


@dataclass(frozen=True)
class DependencyCheck:
    requirement: str
    name: str
    installed_version: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class EnvironmentQualification:
    python: str
    executable: str
    platform: str
    dependencies: tuple[DependencyCheck, ...]
    pip_check_passed: bool
    pip_check_output: str
    constraints_valid: bool
    constraint_issues: tuple[str, ...]
    source_identity: dict[str, object]

    @property
    def passed(self) -> bool:
        return (
            self.pip_check_passed
            and self.constraints_valid
            and all(item.passed for item in self.dependencies)
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["passed"] = self.passed
        payload["dependencies"] = [asdict(item) for item in self.dependencies]
        return payload


def _project_dependencies(root: Path) -> list[str]:
    payload = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return [str(item) for item in payload.get("project", {}).get("dependencies", [])]


def validate_release_constraints(root: str | Path) -> tuple[bool, tuple[str, ...]]:
    """Require one exact, range-compatible release pin for every runtime dependency."""

    base = Path(root).expanduser().resolve()
    path = base / "release-constraints.txt"
    if not path.is_file():
        return False, ("release-constraints.txt is missing",)

    pins: dict[str, Requirement] = {}
    issues: list[str] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            requirement = Requirement(line)
        except Exception as exc:
            issues.append(f"invalid constraint {line!r}: {exc}")
            continue
        name = canonicalize_name(requirement.name)
        if name in pins:
            issues.append(f"duplicate constraint for {name}")
        pins[name] = requirement

    for raw in _project_dependencies(base):
        declared = Requirement(raw)
        if declared.marker is not None and not declared.marker.evaluate():
            continue
        name = canonicalize_name(declared.name)
        pin = pins.get(name)
        if pin is None:
            issues.append(f"missing runtime pin for {name}")
            continue
        exact = [
            item
            for item in pin.specifier
            if item.operator in {"==", "==="} and "*" not in item.version
        ]
        if len(exact) != 1 or len(list(pin.specifier)) != 1:
            issues.append(f"runtime pin for {name} must be one exact == version")
            continue
        version = exact[0].version
        if not declared.specifier.contains(version, prereleases=True):
            issues.append(f"runtime pin {name}=={version} violates {declared.specifier}")

    return not issues, tuple(sorted(set(issues)))


def qualify_environment(root: str | Path) -> EnvironmentQualification:
    """Validate direct runtime requirements plus the installed dependency graph."""

    base = Path(root).expanduser().resolve()
    checks: list[DependencyCheck] = []
    for raw in _project_dependencies(base):
        requirement = Requirement(raw)
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        try:
            installed = importlib.metadata.version(requirement.name)
        except importlib.metadata.PackageNotFoundError:
            checks.append(DependencyCheck(raw, requirement.name, "", False, "not installed"))
            continue
        passed = requirement.specifier.contains(installed, prereleases=True)
        checks.append(
            DependencyCheck(
                raw,
                requirement.name,
                installed,
                passed,
                "satisfies declared range" if passed else "outside declared range",
            )
        )

    pip_check_passed = False
    pip_output = ""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "check"],
            cwd=base,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
        pip_output = ((result.stdout or "") + (result.stderr or "")).strip()
        pip_check_passed = result.returncode == 0
    except (OSError, subprocess.TimeoutExpired) as exc:
        pip_output = f"pip check unavailable: {type(exc).__name__}: {exc}"

    constraints_valid, constraint_issues = validate_release_constraints(base)

    return EnvironmentQualification(
        python=platform.python_version(),
        executable=sys.executable,
        platform=f"{sys.platform}/{platform.machine()}/{platform.python_implementation()}",
        dependencies=tuple(checks),
        pip_check_passed=pip_check_passed,
        pip_check_output=pip_output,
        constraints_valid=constraints_valid,
        constraint_issues=constraint_issues,
        source_identity=resolve_source_identity(base).to_dict(),
    )


def write_environment_qualification(
    root: str | Path, output: str | Path
) -> EnvironmentQualification:
    report = qualify_environment(root)
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report
