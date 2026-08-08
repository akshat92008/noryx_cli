"""Fail-closed syntax, compile, truncation, and entrypoint checks for generated code."""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class CodeCheck:
    path: str
    command: list[str]
    passed: bool
    output: str
    exit_code: int | None

    def format(self) -> str:
        command = " ".join(self.command) if self.command else "static parser"
        status = "PASS" if self.passed else "FAIL"
        return f"{status} {self.path} [{command}] exit={self.exit_code}\n{self.output}".rstrip()


class GeneratedCodeValidator:
    """Validate files in an isolated candidate workspace before approval."""

    def __init__(self, workspace_dir: str, timeout: int = 30):
        self.workspace_dir = Path(workspace_dir).resolve()
        self.timeout = timeout

    def validate(self, files: Iterable, prompt: str = "") -> list[CodeCheck]:
        checks: list[CodeCheck] = []
        for action in files:
            path = (self.workspace_dir / action.path.lstrip("/\\")).resolve()
            try:
                path.relative_to(self.workspace_dir)
            except ValueError:
                checks.append(
                    CodeCheck(action.path, [], False, "path escapes verification workspace", None)
                )
                continue
            if not path.is_file():
                checks.append(
                    CodeCheck(action.path, [], False, "candidate file was not written", None)
                )
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            truncation = self._truncation_reason(path, text)
            if truncation:
                checks.append(CodeCheck(action.path, [], False, truncation, None))
                continue
            entrypoint = self._entrypoint_reason(path, text, prompt)
            if entrypoint:
                checks.append(CodeCheck(action.path, [], False, entrypoint, None))
                continue
            semantic = self._semantic_reason(path, text, prompt)
            if semantic:
                checks.append(CodeCheck(action.path, [], False, semantic, None))
                continue
            checks.append(self._compile(path))
        return checks

    def _compile(self, path: Path) -> CodeCheck:
        suffix = path.suffix.lower()
        if suffix == ".py":
            try:
                ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
                return CodeCheck(
                    str(path), ["python", "ast.parse"], True, "Python AST parse succeeded", 0
                )
            except SyntaxError as exc:
                return CodeCheck(str(path), ["python", "ast.parse"], False, str(exc), 1)
        if suffix == ".json":
            try:
                json.loads(path.read_text(encoding="utf-8"))
                return CodeCheck(str(path), ["json.loads"], True, "JSON parse succeeded", 0)
            except json.JSONDecodeError as exc:
                return CodeCheck(str(path), ["json.loads"], False, str(exc), 1)

        command: list[str] = []
        if suffix in {".js", ".mjs", ".cjs"} and shutil.which("node"):
            command = ["node", "--check", str(path)]
        elif suffix == ".go" and shutil.which("go"):
            command = ["go", "test", str(path)]
        elif suffix in {".cpp", ".cc", ".cxx"} and shutil.which("g++"):
            command = ["g++", "-std=c++17", "-fsyntax-only", str(path)]
        elif suffix == ".c" and shutil.which("cc"):
            command = ["cc", "-fsyntax-only", str(path)]
        elif suffix == ".rs" and shutil.which("rustc"):
            output_path = str(
                Path(tempfile.gettempdir()) / f"nexus-{os.getpid()}-{path.stem}.rmeta"
            )
            command = [
                "rustc",
                "--crate-type",
                "lib",
                "--emit=metadata",
                "-o",
                output_path,
                str(path),
            ]
        elif suffix in {".ts", ".tsx"}:
            # TypeScript needs project/module context; fail closed only on obvious truncation.
            return CodeCheck(
                str(path),
                [],
                True,
                "No standalone TypeScript compiler context; structural checks passed",
                None,
            )
        else:
            return CodeCheck(
                str(path),
                [],
                True,
                "No compiler required or available; structural checks passed",
                None,
            )

        from nexus.process_gateway import ProcessExecutionGateway, ProcessRequest
        # ``node --check`` parses the explicitly selected source file without
        # executing it or resolving imports. Treat that static parser as an
        # explicit trusted-host operation; native compilers may resolve include
        # paths or invoke helpers and therefore remain kernel-isolation-only.
        isolation_policy = "trusted_host" if suffix in {".js", ".mjs", ".cjs"} else "required"
        request = ProcessRequest.create(
            purpose="code_validation",
            command=command,
            workspace=self.workspace_dir,
            timeout_seconds=self.timeout,
            env_additions={"PYTHONDONTWRITEBYTECODE": "1", "CI": "true"},
            isolation_policy=isolation_policy,
        )
        result = ProcessExecutionGateway.run(request)
        if result.timed_out:
            return CodeCheck(
                str(path), command, False, f"compiler timed out after {self.timeout}s", None
            )
        if result.blocked_reason:
            return CodeCheck(str(path), command, False, f"compiler could not run: {result.blocked_reason}", None)
        if result.exit_code is None and not result.success:
            return CodeCheck(str(path), command, False, f"compiler could not run: {result.stderr}", None)
            
        output = (result.stdout + "\n" + result.stderr).strip() or "compiler produced no output"
        return CodeCheck(str(path), command, result.success, output, result.exit_code)

    @staticmethod
    def _entrypoint_reason(path: Path, text: str, prompt: str) -> str:
        needs_main = bool(
            re.search(
                r"\b(executable|standalone|entry\s*point|cli|server|program|main\s+function)\b",
                prompt,
                re.I,
            )
        )
        if not needs_main:
            return ""
        suffix = path.suffix.lower()
        if suffix == ".py" and needs_main:
            try:
                tree = ast.parse(text, filename=str(path))
            except SyntaxError:
                return ""  # The compiler check reports the precise syntax error.
            has_literal_main = any(
                isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main"
                for node in tree.body
            )
            guarded_calls: set[str] = set()
            for node in tree.body:
                if not (
                    isinstance(node, ast.If)
                    and "__name__" in ast.unparse(node.test)
                    and "__main__" in ast.unparse(node.test)
                ):
                    continue
                for statement in node.body:
                    for child in ast.walk(statement):
                        if not isinstance(child, ast.Call):
                            continue
                        if isinstance(child.func, ast.Name):
                            guarded_calls.add(child.func.id)
                        elif isinstance(child.func, ast.Attribute):
                            guarded_calls.add(child.func.attr)

            requires_literal_main = bool(
                re.search(r"\bmain\s+function\b|\bdef\s+main\b", prompt, re.I)
            )
            if requires_literal_main and not has_literal_main:
                return "required Python main function is missing"
            if not guarded_calls:
                return "required Python __name__ entrypoint guard is missing"
            if requires_literal_main and "main" not in guarded_calls:
                return "required Python __name__ entrypoint guard calling main() is missing"
        patterns = {
            ".go": r"\bfunc\s+main\s*\(",
            ".rs": r"\bfn\s+main\s*\(",
            ".cpp": r"\bint\s+main\s*\(",
            ".cc": r"\bint\s+main\s*\(",
            ".cxx": r"\bint\s+main\s*\(",
            ".c": r"\bint\s+main\s*\(",
        }
        pattern = patterns.get(suffix)
        if pattern and not re.search(pattern, text):
            return f"required executable entrypoint is missing for {suffix}"
        return ""

    @staticmethod
    def _truncation_reason(path: Path, text: str) -> str:
        if not text.strip():
            return "candidate is empty"
        if text.rstrip().endswith(("...", "…")):
            return "candidate ends with an ellipsis and appears truncated"
        suffix = path.suffix.lower()
        if suffix in {
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            ".go",
            ".rs",
            ".cpp",
            ".cc",
            ".cxx",
            ".c",
            ".java",
        }:
            scrubbed = re.sub(
                r"//.*?$|/\*.*?\*/|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"|"
                r"`(?:\\.|[^`\\])*`",
                "",
                text,
                flags=re.M | re.S,
            )
            pairs = (("{", "}"), ("(", ")"), ("[", "]"))
            for left, right in pairs:
                if scrubbed.count(left) != scrubbed.count(right):
                    return f"unbalanced {left}{right} delimiters; output appears truncated"
        return ""

    @staticmethod
    def _semantic_reason(path: Path, text: str, prompt: str) -> str:
        """Catch a few mechanically provable, high-frequency semantic misses."""
        suffix = path.suffix.lower()
        if suffix == ".py" and re.search(r"\bnon[- ]?negative\b", prompt, re.I):
            try:
                tree = ast.parse(text, filename=str(path))
            except SyntaxError:
                return ""
            for node in ast.walk(tree):
                if not isinstance(node, ast.If) or not isinstance(node.test, ast.Compare):
                    continue
                comparison = node.test
                rejects_zero = any(isinstance(op, (ast.LtE, ast.GtE)) for op in comparison.ops)
                mentions_zero = any(
                    isinstance(item, ast.Constant) and item.value == 0
                    for item in [comparison.left, *comparison.comparators]
                )
                returns_empty = any(
                    isinstance(child, ast.Return)
                    and isinstance(child.value, (ast.List, ast.Tuple, ast.Set, ast.Dict))
                    and not getattr(child.value, "elts", None)
                    and not getattr(child.value, "keys", None)
                    for statement in node.body
                    for child in ast.walk(statement)
                )
                if rejects_zero and mentions_zero and returns_empty:
                    return (
                        "boundary-condition check failed: prompt distinguishes negative from "
                        "nonnegative input, but the generated guard also rejects zero"
                    )

        if suffix in {".cpp", ".cc", ".cxx"} and re.search(r"\bprint\s+exactly\b", prompt, re.I):
            if re.search(r"(?:std::)?cout\s*<<\s*\w+\s*<<\s*(['\"])\s\1", text):
                return (
                    "exact-output check failed: traversal prints a separator after every item, "
                    "which produces a forbidden trailing space"
                )
            if re.search(r"\bnewline\b", prompt, re.I) and not re.search(
                r"(?:std::)?endl|(['\"])\\n\1", text
            ):
                return "exact-output check failed: requested trailing newline is not emitted"

        if suffix in {".js", ".mjs", ".cjs"} and re.search(
            r"\brecurs(?:ive|ively|ion)\b", prompt, re.I
        ):
            recursive_function: tuple[list[str], str] | None = None
            for match in re.finditer(r"function\s+([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{", text):
                name = match.group(1)
                signature = text[match.start() : match.end()]
                arg_text = signature.split("(", 1)[1].rsplit(")", 1)[0]
                args = [
                    item.split("=", 1)[0].strip() for item in arg_text.split(",") if item.strip()
                ]
                depth = 1
                index = match.end()
                while index < len(text) and depth:
                    if text[index] == "{":
                        depth += 1
                    elif text[index] == "}":
                        depth -= 1
                    index += 1
                body = text[match.end() : index - 1]
                if re.search(rf"\b{re.escape(name)}\s*\(", body):
                    recursive_function = (args, body)
                    break
            if recursive_function is None:
                return "recursive behavior was requested, but no named function calls itself"
            if re.search(r"\brelative\b", prompt, re.I):
                args, body = recursive_function
                relative_call = re.search(r"\bpath\.relative\(\s*([^,]+)\s*,", body)
                if relative_call:
                    base_expr = relative_call.group(1).strip()
                    if (args and base_expr == args[0]) or base_expr in {"'.'", '"."'}:
                        return (
                            "recursive relative-path check failed: path.relative does not use the "
                            "stable user-provided root/base directory"
                        )
        if suffix in {".js", ".mjs", ".cjs"}:
            if re.search(r"\b(?:cli|entry\s*point|executable)\b", prompt, re.I):
                main_defs = list(re.finditer(r"function\s+main\s*\([^)]*\)\s*\{", text))
                if main_defs and len(re.findall(r"\bmain\s*\(", text)) < 2:
                    return "required JavaScript main() entrypoint is defined but never invoked"
            if re.search(r"\bfs\.", text) and not re.search(
                r"require\(['\"](?:node:)?fs['\"]\)|from\s+['\"](?:node:)?fs['\"]", text
            ):
                return "JavaScript uses fs but does not import the built-in fs module"
            if re.search(r"(?<![.\w])join\s*\(", text) and not re.search(
                r"(?:\{[^}]*\bjoin\b[^}]*\}\s*=\s*require\(['\"](?:node:)?path['\"]\)|"
                r"import\s*\{[^}]*\bjoin\b[^}]*\}\s*from\s*['\"](?:node:)?path['\"])",
                text,
            ):
                return "JavaScript uses join() but does not import it from the built-in path module"
        return ""
