"""Deterministic static triage for state and concurrency defects."""
from __future__ import annotations

import ast
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class ConcurrencyFinding:
    path: str
    line: int
    severity: str
    kind: str
    evidence: str
    required_check: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ConcurrencyAnalyzer:
    """Find shared-state and lifecycle patterns that require explicit proof."""

    _TEXT_PATTERNS = (
        (re.compile(r"\b(?:threading\.)?Lock\s*\("), "lock_boundary", "medium", "Run lock-order and repeated contention tests."),
        (re.compile(r"\b(?:asyncio\.)?(?:Lock|Semaphore|Queue)\s*\("), "async_synchronization", "medium", "Run cancellation and concurrent-task cleanup tests."),
        (re.compile(r"\b(?:BEGIN|COMMIT|ROLLBACK|transaction|isolation_level)\b", re.I), "transaction_boundary", "high", "Run concurrent writer and rollback-integrity tests."),
        (re.compile(r"\b(?:Popen|create_subprocess|ThreadPoolExecutor|ProcessPoolExecutor)\b"), "process_or_worker_lifecycle", "high", "Verify termination, joins, descriptor cleanup and repeated lifecycle runs."),
        (re.compile(r"if\s+.+\s+not\s+in\s+.+:\s*$"), "check_then_act", "high", "Prove the check-and-mutate sequence is atomic under contention."),
    )

    @classmethod
    def analyze(cls, root: str | Path, paths: Iterable[str]) -> list[ConcurrencyFinding]:
        repository = Path(root).expanduser().resolve()
        findings: list[ConcurrencyFinding] = []
        for raw in paths:
            path = (repository / raw).resolve(strict=False)
            try:
                relative = path.relative_to(repository).as_posix()
            except ValueError:
                continue
            if not path.is_file() or path.suffix not in {".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".kt"}:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for number, line in enumerate(content.splitlines(), 1):
                for pattern, kind, severity, check in cls._TEXT_PATTERNS:
                    if pattern.search(line):
                        findings.append(ConcurrencyFinding(relative, number, severity, kind, line.strip()[:300], check))
            if path.suffix == ".py":
                findings.extend(cls._python_globals(relative, content))
        unique: dict[tuple[str, int, str], ConcurrencyFinding] = {}
        for item in findings:
            unique[(item.path, item.line, item.kind)] = item
        return sorted(unique.values(), key=lambda item: (item.path, item.line, item.kind))

    @staticmethod
    def _python_globals(path: str, content: str) -> list[ConcurrencyFinding]:
        try:
            tree = ast.parse(content)
        except SyntaxError:
            return []
        findings: list[ConcurrencyFinding] = []
        mutable_nodes = (ast.Dict, ast.List, ast.Set, ast.Call)
        for node in tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = getattr(node, "value", None)
                if isinstance(value, mutable_nodes):
                    names: list[str] = []
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                    for target in targets:
                        if isinstance(target, ast.Name) and not target.id.isupper():
                            names.append(target.id)
                    for name in names:
                        findings.append(
                            ConcurrencyFinding(
                                path=path,
                                line=getattr(node, "lineno", 1),
                                severity="high",
                                kind="module_mutable_state",
                                evidence=f"module-level mutable state: {name}",
                                required_check="Prove synchronization or isolate state per task/process; run concurrent mutation stress.",
                            )
                        )
        return findings
