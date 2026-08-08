"""Context budgeting and excerpt extraction module — Sprint 5."""

from __future__ import annotations

from pathlib import Path
from nexus.intelligence.repository.model import ContextCandidate, ContextFile, RepositoryFile
from nexus.intelligence.repository.secrets import SecretProtector


class ContextBudgetManager:
    """Manages token budgets and builds surgical file excerpts."""

    def __init__(
        self,
        max_total_tokens: int = 24000,
        max_files: int = 12,
        max_tokens_per_file: int = 2500,
        lines_per_excerpt: int = 100,
    ):
        self.max_total_tokens = max_total_tokens
        self.max_files = max_files
        self.max_tokens_per_file = max_tokens_per_file
        self.lines_per_excerpt = lines_per_excerpt

    def assemble_context_files(
        self,
        candidates: list[ContextCandidate],
        repo_files: dict[str, RepositoryFile],
        root_dir: Path,
        search_terms: set[str],
    ) -> tuple[list[ContextFile], list[str], int]:
        selected_files: list[ContextFile] = []
        omitted_candidates: list[str] = []
        consumed_tokens = 0

        for candidate in candidates:
            if len(selected_files) >= self.max_files or consumed_tokens >= self.max_total_tokens:
                omitted_candidates.append(f"{candidate.path} (Budget reached)")
                continue

            repo_file = repo_files.get(candidate.path)
            full_path = root_dir / candidate.path

            if not repo_file or not full_path.is_file() or repo_file.size_bytes > 2_000_000:
                omitted_candidates.append(f"{candidate.path} (File unreadable/oversized)")
                continue

            try:
                raw_text = full_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                omitted_candidates.append(f"{candidate.path} (Read error)")
                continue

            sanitized_text, was_redacted = SecretProtector.sanitize(raw_text, candidate.path)
            lines = sanitized_text.splitlines()

            # Excerpt generation
            start_line, end_line, excerpt, is_full = self._create_excerpt(
                lines, repo_file, search_terms, max_lines=self.lines_per_excerpt
            )

            est_tokens = max(10, len(excerpt) // 4)
            remaining_budget = self.max_total_tokens - consumed_tokens

            if est_tokens > self.max_tokens_per_file:
                # Truncate excerpt to fit max_tokens_per_file
                char_limit = self.max_tokens_per_file * 4
                excerpt = excerpt[:char_limit] + "\n...[excerpt budget reached]"
                est_tokens = self.max_tokens_per_file

            if est_tokens > remaining_budget:
                omitted_candidates.append(f"{candidate.path} (Insufficient token budget remaining)")
                continue

            reason_str = "; ".join(candidate.reasons) if candidate.reasons else candidate.relationship
            if was_redacted:
                reason_str += " [SECRETS REDACTED]"

            ctx_file = ContextFile(
                path=candidate.path,
                language=repo_file.language,
                is_test=repo_file.is_test,
                is_config=repo_file.config_file,
                excerpt=excerpt,
                start_line=start_line,
                end_line=end_line,
                is_full_content=is_full,
                selection_reason=reason_str,
                estimated_tokens=est_tokens,
            )

            selected_files.append(ctx_file)
            consumed_tokens += est_tokens

        return selected_files, omitted_candidates, consumed_tokens

    @staticmethod
    def _create_excerpt(
        lines: list[str],
        repo_file: RepositoryFile,
        search_terms: set[str],
        max_lines: int,
    ) -> tuple[int, int, str, bool]:
        if not lines:
            return 1, 1, "(empty file)", True

        if len(lines) <= max_lines:
            rendered = [f"{i + 1:5}: {line}" for i, line in enumerate(lines)]
            return 1, len(lines), "\n".join(rendered), True

        # Find anchor line indices
        anchors: set[int] = {0}
        for symbol in repo_file.symbols:
            if not search_terms or any(term.lower() in symbol.name.lower() for term in search_terms):
                anchors.add(max(0, symbol.line - 1))

        if search_terms:
            for idx, line in enumerate(lines):
                lowered = line.lower()
                if any(term.lower() in lowered for term in search_terms):
                    anchors.add(idx)
                    if len(anchors) >= 20:
                        break

        chosen: set[int] = set()
        radius = 8
        for anchor in sorted(anchors):
            for idx in range(max(0, anchor - radius), min(len(lines), anchor + radius + 1)):
                chosen.add(idx)
                if len(chosen) >= max_lines:
                    break
            if len(chosen) >= max_lines:
                break

        if len(chosen) < min(max_lines, 30):
            for idx in range(min(len(lines), max_lines - len(chosen))):
                chosen.add(idx)

        sorted_indices = sorted(chosen)[:max_lines]
        start_line = sorted_indices[0] + 1
        end_line = sorted_indices[-1] + 1

        rendered: list[str] = []
        prev = -2
        for idx in sorted_indices:
            if idx > prev + 1:
                rendered.append("    ...")
            rendered.append(f"{idx + 1:5}: {lines[idx]}")
            prev = idx

        return start_line, end_line, "\n".join(rendered), False
