"""ContextManager — intelligent context windowing backed by RepositoryIntelligence (Sprint 5)."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from nexus.paths import nexus_home
from nexus.intelligence.repository.engine import RepositoryIntelligence
from nexus.intelligence.repository.model import FileContext, ArchitectureMap, RiskLevel


class ContextManager:
    """Manages active context using canonical RepositoryIntelligence engine with state persistence."""

    def __init__(self, working_dir: str, max_context_tokens: int = 30000):
        self.working_dir = str(Path(working_dir).expanduser().resolve())
        self.max_context_tokens = max_context_tokens
        self.engine = RepositoryIntelligence(self.working_dir)
        self._file_contexts: dict[str, FileContext] = {}
        self._architecture: ArchitectureMap | None = None
        self._dependency_graph: dict[str, set[str]] = defaultdict(set)
        self._initialized = False

        workspace_key = hashlib.sha256(self.working_dir.encode("utf-8")).hexdigest()[:20]
        self._cache_path = nexus_home() / "context" / f"{workspace_key}.json"
        self._load_cache()

    def initialize(self) -> str:
        if self._initialized:
            return ""
        self._initialized = True
        self.engine.build(force=False)

        summary = self.engine.summary()
        context_parts = [
            f"[PROJECT STRUCTURE] Files: {summary['files_count']} | Symbols: {summary['symbols_count']} | Tests: {summary['tests_count']}"
        ]

        changed = self.engine.git_changed_files()
        if changed:
            context_parts.append(f"[GIT WORKING TREE] Modified files: {', '.join(changed[:10])}")

        return "\n\n".join(context_parts) + "\n\n---\n\n"

    def track_file_access(self, filepath: str, was_edited: bool = False):
        abs_path = self._absolute_path(filepath)
        now = datetime.now().isoformat()

        if abs_path not in self._file_contexts:
            lang = Path(filepath).suffix.lower()
            self._file_contexts[abs_path] = FileContext(path=abs_path, language=lang)

        ctx = self._file_contexts[abs_path]
        ctx.last_accessed = now
        ctx.access_count += 1
        if was_edited:
            ctx.was_edited = True

        candidate = Path(abs_path)
        try:
            if candidate.is_file() and candidate.stat().st_size <= 2_000_000:
                content = candidate.read_text(encoding="utf-8", errors="replace")
                self.summarize_file(abs_path, content)
                self.track_file_imports(abs_path, content)
        except OSError:
            pass

        self.engine.update_paths([abs_path])
        self._save_cache()

    def track_file_imports(self, filepath: str, content: str):
        abs_path = self._absolute_path(filepath)
        if abs_path in self._file_contexts:
            imports = self._extract_imports(content, Path(filepath).suffix.lower())
            self._file_contexts[abs_path].imports = imports
            self._dependency_graph[abs_path].clear()
            for imp in imports:
                resolved = self._resolve_import(abs_path, imp)
                if resolved:
                    self._dependency_graph[abs_path].add(resolved)
        self._save_cache()

    def get_relevant_context(self, user_input: str = "") -> str:
        self._refresh_stale_contexts()
        bundle = self.engine.context_bundle(user_input, max_total_tokens=self.max_context_tokens // 2)
        
        # Combine engine bundle with active file summaries
        prompt = bundle.to_formatted_prompt()
        active_entries = []
        for abs_path, ctx in self._file_contexts.items():
            if ctx.summary and Path(abs_path).exists():
                active_entries.append(f"{Path(abs_path).name}: {ctx.summary}")
        
        if active_entries:
            prompt += "\n\n[ACTIVE FILE SUMMARIES]\n" + "\n".join(active_entries[:10])
            
        return prompt

    def get_architecture_context(self) -> str:
        summary = self.engine.summary()
        return f"Project: {Path(self.working_dir).name} | Files: {summary['files_count']} | Symbols: {summary['symbols_count']}"

    def get_dependency_context(self, filepath: str) -> list[str]:
        abs_path = self._absolute_path(filepath)
        related = set()
        related.update(self._dependency_graph.get(abs_path, set()))
        for file_path, deps in self._dependency_graph.items():
            if abs_path in deps:
                related.add(file_path)
        return list(related)

    def summarize_file(self, filepath: str, content: str) -> str:
        lines = content.split("\n")
        line_count = len(lines)
        lang = Path(filepath).suffix.lower()

        parts = [f"{line_count} lines"]
        classes = re.findall(r"^class\s+(\w+)", content, re.MULTILINE)
        functions = re.findall(r"^(?:def|function|const|let|var)\s+(\w+)", content, re.MULTILINE)
        if classes:
            parts.append(f"classes: {', '.join(classes[:5])}")
        if functions:
            parts.append(f"functions: {', '.join(functions[:8])}")

        summary = " | ".join(parts)
        abs_path = self._absolute_path(filepath)
        if abs_path in self._file_contexts:
            self._file_contexts[abs_path].summary = summary
            self._file_contexts[abs_path].line_count = line_count
            self._save_cache()

        return summary

    def get_change_impact_context(self, filepaths: list[str]) -> str:
        self._refresh_stale_contexts()
        impacted: set[str] = set()
        for filepath in filepaths:
            absolute = self._absolute_path(filepath)
            impacted.add(absolute)
            impacted.update(self.get_dependency_context(absolute))
        entries = []
        for path in sorted(impacted):
            context = self._file_contexts.get(path)
            if context and Path(path).exists():
                entries.append(f"{path}: {context.summary or 'tracked file'}")
        return "[CHANGE IMPACT]\n" + "\n".join(entries) if entries else ""

    def _absolute_path(self, filepath: str) -> str:
        candidate = Path(filepath).expanduser()
        if not candidate.is_absolute():
            candidate = Path(self.working_dir) / candidate
        return str(candidate.resolve())

    def _extract_imports(self, content: str, suffix: str) -> list[str]:
        patterns = (
            r"\b(?:import|from)\s+(?:[^'\"]*?\s+from\s+)?['\"]([^'\"]+)['\"]",
            r"\brequire\s*\(\s*['\"]([^'\"]+)['\"]\s*\)",
            r"^\s*from\s+([.\w]+)\s+import",
            r"^\s*import\s+([\w, ]+)",
        )
        imports = []
        for pattern in patterns:
            for match in re.finditer(pattern, content, re.MULTILINE):
                val = match.group(1).strip()
                if val:
                    for part in val.split(","):
                        imports.append(part.strip())
        return list(dict.fromkeys(imports))

    def _resolve_import(self, source_path: str, import_str: str) -> str | None:
        source_dir = Path(source_path).parent

        if import_str.startswith("."):
            m = re.match(r"^(\.+)(.*)$", import_str)
            if m:
                dots = len(m.group(1))
                rel_mod = m.group(2).lstrip("/")
                target_dir = source_dir
                for _ in range(dots - 1):
                    target_dir = target_dir.parent
                for ext in (".py", ".ts", ".js", ".tsx", ".jsx"):
                    cand = target_dir / f"{rel_mod}{ext}" if rel_mod else target_dir / f"index{ext}"
                    if cand.is_file():
                        return str(cand.resolve())

        for base in (source_dir, Path(self.working_dir)):
            for ext in (".py", ".ts", ".js", ".tsx", ".jsx"):
                cand = base / f"{import_str.replace('.', '/')}{ext}"
                if cand.is_file():
                    return str(cand.resolve())
        return None

    def _refresh_stale_contexts(self) -> None:
        to_delete = []
        index_refresh: list[str] = []
        for path in list(self._file_contexts.keys()):
            p = Path(path)
            if not p.exists():
                to_delete.append(path)
                index_refresh.append(path)
            else:
                try:
                    stat = p.stat()
                    ctx = self._file_contexts[path]
                    if ctx.modified_ns != stat.st_mtime_ns:
                        ctx.modified_ns = stat.st_mtime_ns
                        index_refresh.append(path)
                        content = p.read_text(encoding="utf-8", errors="replace")
                        self.summarize_file(path, content)
                        self.track_file_imports(path, content)
                except OSError:
                    pass

        for path in to_delete:
            del self._file_contexts[path]
            self._dependency_graph.pop(path, None)
            for deps in self._dependency_graph.values():
                deps.discard(path)

        if index_refresh:
            self.engine.update_paths(index_refresh)
            self._save_cache()

    def _save_cache(self) -> None:
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "workspace": self.working_dir,
                "files": {path: ctx.summary for path, ctx in self._file_contexts.items()},
                "dependencies": {path: list(deps) for path, deps in self._dependency_graph.items()},
            }
            self._cache_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except OSError:
            pass

    def _load_cache(self) -> None:
        if not self._cache_path.is_file():
            return
        try:
            payload = json.loads(self._cache_path.read_text(encoding="utf-8"))
            if payload.get("workspace") == self.working_dir:
                files_dict = payload.get("files", {})
                if isinstance(files_dict, dict):
                    for path, summary in files_dict.items():
                        if Path(path).exists():
                            self._file_contexts[path] = FileContext(path=path, summary=summary)
                deps_dict = payload.get("dependencies", {})
                if isinstance(deps_dict, dict):
                    for path, deps in deps_dict.items():
                        if Path(path).exists():
                            self._dependency_graph[path] = set(deps)
        except (OSError, json.JSONDecodeError):
            pass
