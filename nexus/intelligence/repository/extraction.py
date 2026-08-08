"""Language-aware symbol and dependency extraction module — Sprint 5."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from nexus.intelligence.repository.model import RepositorySymbol, SymbolReference


class LanguageExtractor:
    """Extracts symbols, references, imports, and framework structures."""

    def extract(self, relative_path: str, source: str) -> dict:
        suffix = Path(relative_path).suffix.lower()
        imports: list[str] = []
        symbols: list[RepositorySymbol] = []
        references: list[str] = []
        routes: list[str] = []
        database_models: list[str] = []
        parse_error: str = ""

        if suffix in {".py", ".pyi"}:
            try:
                tree = ast.parse(source, filename=relative_path)
                imports, symbols, references = self._extract_python(tree, relative_path)
                routes, database_models = self._extract_python_frameworks(tree)
            except SyntaxError as exc:
                parse_error = f"{exc.msg} at line {exc.lineno}"
        else:
            imports, symbols, references = self._extract_generic(source, relative_path)
            routes, database_models = self._extract_generic_frameworks(source, relative_path)
            if Path(relative_path).name == "package.json":
                try:
                    pkg = json.loads(source)
                    for sec in ("dependencies", "devDependencies", "peerDependencies"):
                        if isinstance(pkg.get(sec), dict):
                            imports.extend(pkg[sec].keys())
                except json.JSONDecodeError:
                    pass

        return {
            "imports": sorted(dict.fromkeys(imports)),
            "symbols": symbols,
            "references": sorted(dict.fromkeys(references)),
            "routes": sorted(dict.fromkeys(routes)),
            "database_models": sorted(dict.fromkeys(database_models)),
            "parse_error": parse_error,
        }

    @staticmethod
    def _extract_python(tree: ast.AST, relative_path: str) -> tuple[list[str], list[RepositorySymbol], list[str]]:
        imports: list[str] = []
        symbols: list[RepositorySymbol] = []
        references: list[str] = []
        parents: list[str] = []

        class Visitor(ast.NodeVisitor):
            def visit_Import(self, node: ast.Import) -> None:
                for alias in node.names:
                    imports.append(alias.name)

            def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
                base = "." * node.level + (node.module or "")
                imports.append(base)

            def visit_ClassDef(self, node: ast.ClassDef) -> None:
                qualified = ".".join([*parents, node.name])
                end_line = getattr(node, "end_lineno", node.lineno)
                docstring = ast.get_docstring(node) or ""
                symbols.append(
                    RepositorySymbol(
                        name=node.name,
                        kind="class",
                        file_path=relative_path,
                        line=node.lineno,
                        end_line=end_line,
                        qualified_name=qualified,
                        parent_symbol=parents[-1] if parents else None,
                        docstring=docstring,
                    )
                )
                parents.append(node.name)
                self.generic_visit(node)
                parents.pop()

            def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
                qualified = ".".join([*parents, node.name])
                kind = "method" if parents else "function"
                end_line = getattr(node, "end_lineno", node.lineno)
                docstring = ast.get_docstring(node) or ""
                
                # Signature summary
                args = [arg.arg for arg in node.args.args]
                sig = f"{node.name}({', '.join(args)})"

                symbols.append(
                    RepositorySymbol(
                        name=node.name,
                        kind=kind,
                        file_path=relative_path,
                        line=node.lineno,
                        end_line=end_line,
                        qualified_name=qualified,
                        signature=sig,
                        parent_symbol=parents[-1] if parents else None,
                        docstring=docstring,
                    )
                )
                parents.append(node.name)
                self.generic_visit(node)
                parents.pop()

            visit_AsyncFunctionDef = visit_FunctionDef

            def visit_Call(self, node: ast.Call) -> None:
                if isinstance(node.func, ast.Name):
                    references.append(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    references.append(node.func.attr)
                self.generic_visit(node)

        Visitor().visit(tree)
        return imports, symbols, references

    @staticmethod
    def _extract_python_frameworks(tree: ast.AST) -> tuple[list[str], list[str]]:
        routes: list[str] = []
        models: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                for decorator in node.decorator_list:
                    if not isinstance(decorator, ast.Call):
                        continue
                    func = decorator.func
                    method = func.attr.lower() if isinstance(func, ast.Attribute) else ""
                    if method in {"get", "post", "put", "patch", "delete", "route", "websocket"}:
                        if decorator.args and isinstance(decorator.args[0], ast.Constant):
                            val = decorator.args[0].value
                            if isinstance(val, str):
                                routes.append(f"{method.upper()} {val}")
            elif isinstance(node, ast.ClassDef):
                base_names = []
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        base_names.append(base.id)
                    elif isinstance(base, ast.Attribute):
                        base_names.append(base.attr)
                if any(name in {"Base", "Model", "Document", "DeclarativeBase"} for name in base_names):
                    models.append(node.name)
        return routes, models

    @staticmethod
    def _extract_generic(source: str, relative_path: str) -> tuple[list[str], list[RepositorySymbol], list[str]]:
        import_patterns = (
            r"\b(?:import|from)\s+(?:[^'\"]*?\s+from\s+)?['\"]([^'\"]+)['\"]",
            r"\brequire\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
            r"^\s*import\s+([A-Za-z0-9_./:-]+)",
            r"^\s*use\s+([A-Za-z0-9_:]+)",
        )
        imports: list[str] = []
        for pattern in import_patterns:
            imports.extend(re.findall(pattern, source, re.MULTILINE))

        declarations = (
            ("class", r"\bclass\s+([A-Za-z_$][\w$]*)"),
            ("interface", r"\binterface\s+([A-Za-z_$][\w$]*)"),
            ("type", r"\btype\s+([A-Za-z_$][\w$]*)\s*="),
            ("function", r"\b(?:function|func|fn)\s+([A-Za-z_$][\w$]*)"),
            ("function", r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\([^)]*\)\s*=>"),
        )
        symbols: list[RepositorySymbol] = []
        for kind, pattern in declarations:
            for match in re.finditer(pattern, source):
                line = source.count("\n", 0, match.start()) + 1
                name = match.group(1)
                symbols.append(
                    RepositorySymbol(
                        name=name,
                        kind=kind,
                        file_path=relative_path,
                        line=line,
                        qualified_name=name,
                    )
                )

        declared = {item.name for item in symbols}
        call_names = re.findall(r"\b([A-Za-z_$][\w$]*)\s*\(", source)
        references = [
            name for name in call_names
            if name not in declared and name not in {"if", "for", "while", "switch", "catch", "return"}
        ]
        return imports, symbols, references

    @staticmethod
    def _extract_generic_frameworks(source: str, relative_path: str) -> tuple[list[str], list[str]]:
        routes: list[str] = []
        route_patterns = (
            r"\b(?:app|router|server)\.(get|post|put|patch|delete|use)\s*\(\s*['\"]([^'\"]+)",
            r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+['\"]([^'\"]+)['\"]",
        )
        for match in re.finditer(route_patterns[0], source, re.I):
            routes.append(f"{match.group(1).upper()} {match.group(2)}")
        for match in re.finditer(route_patterns[1], source):
            routes.append(match.group(0).strip("'\""))

        models: list[str] = []
        suffix = Path(relative_path).suffix.lower()
        if suffix == ".prisma":
            models.extend(re.findall(r"^\s*model\s+([A-Za-z_]\w*)", source, re.MULTILINE))
        if suffix == ".sql":
            models.extend(re.findall(r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`]?([A-Za-z_]\w*)", source, re.I))

        return routes, models
