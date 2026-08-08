"""
Nova local backend adapter for NexusAI.

Runs the existing CeilingInternPipeline in a temporary verification workspace,
then converts guardrail-passed Nova file actions into Nexus tool calls. The
actual workspace mutation still goes through Agent._execute_tool_with_safety.
"""

from __future__ import annotations

import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from nexus.code_validation import GeneratedCodeValidator
from nexus.nova_runtime import (
    AtomicTask,
    CeilingNode,
    ConstraintExtractor,
    ConstraintVerifier,
    InternNode,
    TaskGuardrail,
    TestExecutor,
    extract_prompt_paths,
)

PATCH_BLOCK = re.compile(
    r"^<+\s*\r?\n(.*?)^=+\s*\r?\n(.*?)^>+\s*$",
    re.DOTALL | re.MULTILINE,
)
WRAPPED_BLOCK = re.compile(
    r"^<+\s*\r?\n(.*?)^>+\s*$",
    re.DOTALL | re.MULTILINE,
)
PROMPT_PATH = re.compile(
    r"(?:^|\s|`|'|\")([a-zA-Z0-9_\-/]+\.[A-Za-z][A-Za-z0-9]{0,7})(?:$|\s|`|'|\"|\?|\.|,|:|;|\))",
    re.IGNORECASE,
)


@dataclass
class NovaToolProposal:
    """A Nexus tool call proposed by Nova after Nova guardrails passed."""

    name: str
    args: dict[str, Any]
    source_path: str
    guardrail_summary: str


@dataclass
class NovaBackendResult:
    """Result of running Nova through the local pipeline."""

    raw_output: str
    assistant_text: str
    guardrail_output: str
    test_command: str = ""
    proposals: list[NovaToolProposal] = field(default_factory=list)


class NovaBackendError(RuntimeError):
    """Raised when the local Nova backend cannot produce guarded actions."""


class NovaPipelineBackend:
    """Thin adapter from Nova pipeline results to Nexus tool proposals."""

    def __init__(self, model: str = "nova_codex", working_dir: str | None = None):
        self.model = model
        self.working_dir = Path(working_dir or os.getcwd()).resolve()

    def run(self, prompt: str) -> NovaBackendResult:
        """Run local Nova and return only independently verified tool proposals."""
        with tempfile.TemporaryDirectory(prefix="nexus_nova_verify_") as tmp:
            verification_dir = Path(tmp)
            prompt_paths = extract_prompt_paths(prompt)
            task = AtomicTask(
                id=1,
                description=prompt,
                expected_files=max(1, len(prompt_paths)),
                scope_level="atomic" if len(prompt_paths) <= 1 else "multi_file",
            )
            raw_parts: list[str] = []
            response_parts: list[str] = []
            proposals: list[NovaToolProposal] = []
            test_command = ""
            logs: list[str] = []
            intern = InternNode(model=self.model)
            guardrail = TaskGuardrail(max_reroutes=1)
            manual = CeilingNode(provider="manual")
            constraints = ConstraintExtractor(manual).extract(prompt)
            constraint_verifier = ConstraintVerifier(manual)
            precheck = guardrail.pre_check(task)
            logs.append(str(precheck))
            if not precheck.passed:
                return NovaBackendResult(
                    raw_output="",
                    assistant_text=f"Nova guardrails rejected the task: {precheck.reason}",
                    guardrail_output="\n".join(logs),
                )

            override_prompt = ""
            for attempt in range(2):
                task_result = intern.execute(
                    task,
                    context=self._prompt_file_context(prompt),
                    override_prompt=override_prompt,
                )
                response = task_result.response
                if response.raw_text:
                    raw_parts.append(response.raw_text)

                if response.response_text:
                    response_parts.append(response.response_text)
                    break
                if response.clarification_text:
                    response_parts.append(response.clarification_text)
                    break

                failure = self._validate_response(
                    task=task,
                    response=response,
                    prompt_paths=prompt_paths,
                    constraints=constraints,
                    constraint_verifier=constraint_verifier,
                    guardrail=guardrail,
                    verification_dir=verification_dir,
                )
                if failure:
                    logs.append(f"GUARDRAIL FAILED attempt={attempt + 1}: {failure}")
                    if attempt == 0:
                        override_prompt = self._retry_prompt(prompt, prompt_paths, failure)
                        continue
                    response_parts.append(
                        "Nova guardrails rejected the output before Nexus tools ran:\n" + failure
                    )
                    break

                summary = "\n".join(
                    logs
                    + [
                        f"VALIDATED attempt={attempt + 1}: schema, paths, constraints, "
                        "disk replay, and compiler checks passed"
                    ]
                )
                test_command = response.test_command
                try:
                    for file_action in response.files:
                        proposals.extend(self._file_action_to_tool_calls(file_action, summary))
                except NovaBackendError as exc:
                    logs.append(f"GUARDRAIL FAILED attempt={attempt + 1}: canonicalization: {exc}")
                    if attempt == 0:
                        override_prompt = self._retry_prompt(prompt, prompt_paths, str(exc))
                        continue
                    response_parts.append(f"Nova canonicalization failed: {exc}")
                break

            assistant_text = "\n\n".join(response_parts).strip()
            if proposals:
                assistant_text = assistant_text or (
                    f"Nova guardrails passed; prepared {len(proposals)} Nexus tool call(s)."
                )
            elif not assistant_text:
                assistant_text = "Nova did not produce any guardrail-approved tool calls."

            return NovaBackendResult(
                raw_output="\n\n".join(raw_parts).strip(),
                assistant_text=assistant_text,
                guardrail_output="\n".join(logs).strip(),
                test_command=test_command,
                proposals=proposals,
            )

    def _validate_response(
        self,
        *,
        task: AtomicTask,
        response,
        prompt_paths: list[str],
        constraints,
        constraint_verifier: ConstraintVerifier,
        guardrail: TaskGuardrail,
        verification_dir: Path,
    ) -> str:
        """Apply every deterministic gate to one Nova generation."""
        if not response.is_valid:
            return "Format errors: " + "; ".join(response.parse_errors)
        schema = guardrail.schema_check(task, response.raw_text)
        if not schema.passed:
            return schema.reason
        post = guardrail.post_check(task, response.raw_text)
        if not post.passed:
            return post.reason

        if prompt_paths:
            expected = sorted(os.path.normpath(item.lstrip("/\\")) for item in prompt_paths)
            actual = sorted(os.path.normpath(item.path.lstrip("/\\")) for item in response.files)
            if actual != expected:
                return f"Path validator expected {expected}, got {actual}"

        passed, reason = constraint_verifier.verify(constraints, response.files)
        if not passed:
            return reason
        function_names = guardrail.function_name_check(
            task,
            response.raw_text,
            task.description,
        )
        if not function_names.passed:
            return function_names.reason
        consistency = guardrail.thinking_files_consistency_check(
            task,
            response.raw_text,
        )
        if not consistency.passed:
            return consistency.reason

        with tempfile.TemporaryDirectory(
            prefix="candidate_",
            dir=verification_dir,
        ) as candidate:
            candidate_dir = Path(candidate)
            self._seed_verification_workspace(task.description, candidate_dir)
            try:
                TestExecutor(str(candidate_dir)).write_files(
                    response.files,
                    strict_verify=True,
                )
            except ValueError as exc:
                return f"Disk verification failed: {exc}"

            summary = "candidate"
            try:
                cleaned: list[NovaToolProposal] = []
                for action in response.files:
                    cleaned.extend(self._file_action_to_tool_calls(action, summary))
            except NovaBackendError as exc:
                return f"Canonicalization failed: {exc}"
            clean_dir = candidate_dir / "_canonical"
            clean_dir.mkdir(parents=True, exist_ok=True)
            self._seed_verification_workspace(task.description, clean_dir)
            materialized, error = self._materialize_proposals(cleaned, clean_dir)
            if error:
                return f"Canonicalization failed: {error}"
            checks = GeneratedCodeValidator(str(clean_dir)).validate(
                [SimpleNamespace(path=item) for item in materialized],
                task.description,
            )
            failed = [check for check in checks if not check.passed]
            if failed:
                return "Compiler/validator failed: " + " | ".join(
                    check.format() for check in failed
                )
        return ""

    def _prompt_file_context(self, prompt: str) -> str:
        """Return exact contents of prompt-mentioned files for MODIFY tasks."""
        chunks: list[str] = []
        for rel_path in extract_prompt_paths(prompt):
            source = (self.working_dir / rel_path).resolve()
            try:
                source.relative_to(self.working_dir)
            except ValueError:
                continue
            if source.is_file():
                try:
                    chunks.append(
                        f"# Existing File: {rel_path}\n```\n"
                        f"{source.read_text(encoding='utf-8')}\n```"
                    )
                except OSError:
                    continue
        return "\n\n".join(chunks)

    @staticmethod
    def _retry_prompt(
        prompt: str,
        prompt_paths: list[str],
        failure: str,
    ) -> str:
        paths = ", ".join(prompt_paths) or "the exact repository-relative path"
        return (
            "Regenerate the complete answer from the beginning. Do not quote the "
            "previous answer.\n\n"
            f"Original task:\n{prompt}\n\n"
            f"Guardrail failure:\n{failure}\n\n"
            f"Required paths: {paths}\n"
            "Return only the canonical Nova protocol. For MODIFY use a short exact "
            "SEARCH block from the authoritative file followed by ======= and the "
            "replacement. Preserve every original requirement."
        )

    def _seed_verification_workspace(self, prompt: str, verification_dir: Path):
        """Copy prompt-mentioned existing files so Nova's disk gate can verify MODIFY patches."""
        for rel_path in set(PROMPT_PATH.findall(prompt)):
            source = (self.working_dir / rel_path).resolve()
            try:
                source.relative_to(self.working_dir)
            except ValueError:
                continue
            if not source.is_file():
                continue

            target = verification_dir / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    @staticmethod
    def _materialize_proposals(
        proposals: list[NovaToolProposal], candidate_dir: Path
    ) -> tuple[list[str], str]:
        """Replay cleaned proposals in an isolated tree for compiler checks."""
        materialized: list[str] = []
        for proposal in proposals:
            target = (candidate_dir / proposal.source_path.lstrip("/\\")).resolve()
            try:
                target.relative_to(candidate_dir.resolve())
            except ValueError:
                return materialized, f"path escapes candidate workspace: {proposal.source_path}"
            target.parent.mkdir(parents=True, exist_ok=True)
            if proposal.name == "write_file":
                target.write_text(str(proposal.args.get("content", "")), encoding="utf-8")
            elif proposal.name == "edit_file":
                if not target.is_file():
                    return materialized, f"edit target does not exist: {proposal.source_path}"
                original = target.read_text(encoding="utf-8")
                old = str(proposal.args.get("old_text", ""))
                if not old or original.count(old) != 1:
                    return materialized, (
                        f"edit search text occurs {original.count(old)} times in "
                        f"{proposal.source_path}; expected exactly once"
                    )
                target.write_text(
                    original.replace(old, str(proposal.args.get("new_text", "")), 1),
                    encoding="utf-8",
                )
            else:
                return materialized, f"unsupported proposal type: {proposal.name}"
            if proposal.source_path not in materialized:
                materialized.append(proposal.source_path)
        return materialized, ""

    def _file_action_to_tool_calls(
        self,
        file_action,
        guardrail_summary: str,
    ) -> list[NovaToolProposal]:
        """Convert a parsed Nova FileAction into one or more Nexus tool calls."""
        action = file_action.action.upper()
        base_meta = {
            "passed": True,
            "summary": guardrail_summary,
        }

        if action == "MODIFY":
            matches = PATCH_BLOCK.findall(file_action.content)
            if matches:
                proposals = []
                for old_text, new_text in matches:
                    proposals.append(
                        NovaToolProposal(
                            name="edit_file",
                            args={
                                "path": file_action.path,
                                "old_text": old_text,
                                "new_text": new_text,
                                "_nova_guardrail": base_meta,
                            },
                            source_path=file_action.path,
                            guardrail_summary=guardrail_summary,
                        )
                    )
                return proposals
            # If action == "MODIFY" but no patch markers were present (e.g. direct full file emission from Ceiling model),
            # fall through to treat content as a full write_file proposal.

        if action == "DELETE":
            raise NovaBackendError(
                f"Nova emitted DELETE for {file_action.path}; Nexus does not apply Nova deletions automatically."
            )

        content = file_action.content

        # Strip markdown fences if content was wrapped in ``` code blocks
        if content.startswith("```"):
            content = re.sub(r"^```[a-zA-Z0-9_-]*\r?\n", "", content)
            content = re.sub(r"\r?\n```$", "", content)

        # Handle trailing filename label artifacts (e.g. >>>>>># File: task_queue.js \n task_queue.js)
        if ">>>>" in content and ("# File:" in content or "# filepath:" in content):
            content = re.split(r">>>+#\s*File:|>>>+#\s*filepath:|>>>+\s*#", content)[0].strip("\n")

        # Handle repetitive block loops (e.g. repeated "# File:" or "# filepath:")
        if "# File:" in content or "# filepath:" in content:
            parts = re.split(r"(?:#\s*filepath:|#\s*File:)", content)
            if len(parts) > 1:
                block = parts[1]
                if "=======" in block:
                    block = block.split("=======", 1)[1]
                extracted = re.split(r">>>+|#\s*File:|#\s*filepath:", block)[0].strip("\n")
                clean_extracted = extracted.strip()
                target_filename = os.path.basename(file_action.path).strip()
                if (
                    clean_extracted
                    and clean_extracted != target_filename
                    and clean_extracted != file_action.path.strip()
                ):
                    content = extracted
                elif "=======" in parts[0]:
                    pre_content = parts[0].split("=======", 1)[1]
                    content = re.sub(r">>>+.*$", "", pre_content, flags=re.MULTILINE).strip("\n")

        patch_matches = PATCH_BLOCK.findall(content)
        if patch_matches:
            # Nova's CREATE template sometimes puts the new code before the
            # separator. Prefer the post-separator side, but keep a non-empty
            # pre-separator side instead of writing marker scaffolding.
            chunks = []
            for old_text, new_text in patch_matches:
                chunk = new_text if new_text.strip() else old_text
                chunk = re.split(r"^=+\s*$", chunk, maxsplit=1, flags=re.MULTILINE)[0]
                chunk = re.split(r">>>+|#\s*File:|#\s*filepath:", chunk)[0]
                chunks.append(chunk.strip("\n"))
            content = "\n".join(chunks).strip("\n")
        elif "=======" in content and (
            "<<<<<<<" in content or content.lstrip().startswith("<<<<<<<")
        ):
            pre_sep, post_sep = content.split("=======", 1)
            post_sep = re.split(
                r">>>+#\s*File:|>>>+#\s*filepath:|#\s*File:|#\s*filepath:", post_sep
            )[0]
            clean_post = re.sub(r"^>+\s*$", "", post_sep, flags=re.MULTILINE).strip("\n")
            if clean_post:
                content = clean_post
            else:
                pre_sep = pre_sep.split("<<<<<<<", 1)[-1]
                content = re.sub(r"^<+\s*$", "", pre_sep, flags=re.MULTILINE).strip("\n")
        elif "@@" in content and ("---" in content or "+++" in content):
            diff_proposals = _unified_diff_to_proposals(
                file_action.path, content, base_meta, guardrail_summary
            )
            if diff_proposals:
                return diff_proposals
        else:
            wrapped_match = WRAPPED_BLOCK.search(content)
            if wrapped_match:
                content = wrapped_match.group(1).strip("\n")

        if not content.strip():
            raise NovaBackendError(f"Nova emitted 0-byte empty content for {file_action.path}")

        return [
            NovaToolProposal(
                name="write_file",
                args={
                    "path": file_action.path,
                    "content": content,
                    "_nova_guardrail": base_meta,
                },
                source_path=file_action.path,
                guardrail_summary=guardrail_summary,
            )
        ]


def _unified_diff_to_proposals(
    file_path: str, diff_text: str, base_meta: dict, guardrail_summary: str
) -> list[NovaToolProposal]:
    """Parse unified diff hunks into surgical edit_file proposals."""
    hunks = re.split(r"^@@\s*-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s*@@", diff_text, flags=re.MULTILINE)
    if len(hunks) <= 1:
        return []
    proposals = []
    for hunk in hunks[1:]:
        old_lines = []
        new_lines = []
        for line in hunk.splitlines():
            if line.startswith("-"):
                old_lines.append(line[1:])
            elif line.startswith("+"):
                new_lines.append(line[1:])
            elif line.startswith(" ") or line.startswith("\t"):
                old_lines.append(line[1:] if line.startswith(" ") else line)
                new_lines.append(line[1:] if line.startswith(" ") else line)
        old_text = "\n".join(old_lines).strip("\n")
        new_text = "\n".join(new_lines).strip("\n")
        if old_text and new_text:
            proposals.append(
                NovaToolProposal(
                    name="edit_file",
                    args={
                        "path": file_path,
                        "old_text": old_text,
                        "new_text": new_text,
                        "_nova_guardrail": base_meta,
                    },
                    source_path=file_path,
                    guardrail_summary=guardrail_summary,
                )
            )
    return proposals
