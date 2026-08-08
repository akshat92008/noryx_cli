"""Self-contained Nova V11 runtime used by Nexus.

The Nova model weights are served by Ollama, but the protocol parser, task
contract, guardrails, literal checks, and isolated disk replay are packaged
with Nexus.  A Nexus wheel therefore never depends on a sibling Nova source
checkout being present on the user's machine.
"""

from __future__ import annotations

import ast
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

_PROMPT_PATH_RE = re.compile(
    r"(?:^|\s|`|'|\")([a-zA-Z0-9_\-/]+\.[A-Za-z][A-Za-z0-9]{0,7})"
    r"(?:$|\s|`|'|\"|\?|\.|,|:|;|\))",
    re.IGNORECASE,
)
_EXPLICIT_TARGET_RE = re.compile(
    r"\b(?:create|modify|update|edit)(?:\s+exactly)?(?:\s+one)?(?:\s+file)?\s+"
    r"[`'\"]?([a-zA-Z0-9_\-/]+\.[A-Za-z][A-Za-z0-9]{0,7})",
    re.IGNORECASE,
)
_TECHNOLOGY_NAMES = {
    "angular.js",
    "chart.js",
    "deno.js",
    "ember.js",
    "express.js",
    "next.js",
    "node.js",
    "nuxt.js",
    "react.js",
    "three.js",
    "vue.js",
}


def extract_prompt_paths(text: str) -> list[str]:
    """Return explicit repository paths while excluding common prose matches."""
    explicit = _EXPLICIT_TARGET_RE.findall(text)
    candidates = _PROMPT_PATH_RE.findall(text)
    return list(
        dict.fromkeys(
            item for item in [*explicit, *candidates] if item.lower() not in _TECHNOLOGY_NAMES
        )
    )


@dataclass
class AtomicTask:
    """One bounded unit of work passed to Nova."""

    id: int
    description: str
    context: str = ""
    priority: int = 0
    depends_on: list[int] = field(default_factory=list)
    expected_files: int = 1
    scope_level: str = "atomic"


@dataclass
class FileAction:
    """A parsed Nova file operation."""

    path: str
    action: str
    content: str
    language: str = "python"


@dataclass
class ParsedResponse:
    """Structured Nova protocol response."""

    thinking: str = ""
    files: list[FileAction] = field(default_factory=list)
    test_command: str = ""
    response_text: str = ""
    clarification_text: str = ""
    parse_errors: list[str] = field(default_factory=list)
    raw_text: str = ""

    @property
    def is_valid(self) -> bool:
        has_body = bool(self.files or self.response_text or self.clarification_text)
        return bool(self.thinking.strip()) and has_body and not self.parse_errors

    @property
    def is_partial(self) -> bool:
        return bool(self.thinking or self.files or self.test_command) and not self.is_valid


@dataclass
class TaskResult:
    """Result of one local Nova generation."""

    task: AtomicTask
    response: ParsedResponse
    files_written: list[str] = field(default_factory=list)
    test_status: str = "UNTESTED"
    test_output: str = ""
    execution_time_ms: float = 0.0
    retries: int = 0


class NovaOutputParser:
    """Parse the versioned JSON protocol with legacy V11 compatibility."""

    JSON_SCHEMA = "nova.patch.v1"

    THINKING_PATTERN = re.compile(
        r"<<THINKING>>(.*?)(?=<<FILES>>|<<TEST_COMMAND>>|<<CLARIFICATION>>|"
        r"<<RESPONSE>>|$)",
        re.DOTALL | re.IGNORECASE,
    )
    FILES_PATTERN = re.compile(
        r"<<FILES>>(.*?)(?=<<TEST_COMMAND>>|$)",
        re.DOTALL | re.IGNORECASE,
    )
    TEST_PATTERN = re.compile(
        r"<<TEST_COMMAND>>(.*?)$",
        re.DOTALL | re.IGNORECASE,
    )
    RESPONSE_PATTERN = re.compile(
        r"<<RESPONSE>>(.*?)(?=<<TEST_COMMAND>>|$)",
        re.DOTALL | re.IGNORECASE,
    )
    CLARIFICATION_PATTERN = re.compile(
        r"<<CLARIFICATION>>(.*?)(?=<<TEST_COMMAND>>|$)",
        re.DOTALL | re.IGNORECASE,
    )
    CODE_BLOCK_PATTERN = re.compile(
        r"(?:```|~~~)([A-Za-z0-9_+.-]*)\r?\n(.*?)(?:```|~~~)",
        re.DOTALL,
    )
    FILEPATH_PATTERN = re.compile(
        r"(?:#|//|<!--|/\*|^|\s)filepath:\s*([^\s>*`'\"]+)",
        re.IGNORECASE,
    )
    ACTION_PATTERN = re.compile(
        r"(?:#|//|<!--|/\*|^|\s)action:\s*(CREATE|MODIFY|DELETE)",
        re.IGNORECASE,
    )

    def parse(self, text: str) -> ParsedResponse:
        result = ParsedResponse(raw_text=text or "")
        if not text or not text.strip():
            result.parse_errors.append("Empty response")
            return result

        json_result = self._parse_json_protocol(text)
        if json_result is not None:
            return json_result

        thinking_match = self.THINKING_PATTERN.search(text)
        if thinking_match:
            result.thinking = thinking_match.group(1).strip()
        else:
            result.parse_errors.append("Missing <<THINKING>> block")

        response_match = self.RESPONSE_PATTERN.search(text)
        if response_match:
            result.response_text = response_match.group(1).strip()
            if not result.response_text:
                result.parse_errors.append("Empty <<RESPONSE>> block")
            return result

        clarification_match = self.CLARIFICATION_PATTERN.search(text)
        if clarification_match:
            result.clarification_text = clarification_match.group(1).strip()
            if not result.clarification_text:
                result.parse_errors.append("Empty <<CLARIFICATION>> block")
            return result

        files_match = self.FILES_PATTERN.search(text)
        if not files_match:
            result.parse_errors.append("Missing <<FILES>> block")
            return result

        result.files = self._parse_file_blocks(
            files_match.group(1).strip(),
            result.parse_errors,
        )
        test_match = self.TEST_PATTERN.search(text)
        if test_match:
            result.test_command = self._clean_test_command(test_match.group(1))
        return result

    def _parse_json_protocol(self, text: str) -> ParsedResponse | None:
        """Parse ``nova.patch.v1`` when the model emits a JSON object."""
        stripped = text.strip()
        if stripped.startswith("```json") and stripped.endswith("```"):
            stripped = stripped[len("```json") : -3].strip()
        if not stripped.startswith("{"):
            return None
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict) or payload.get("schema") != self.JSON_SCHEMA:
            return None

        result = ParsedResponse(raw_text=text)
        thinking = payload.get("thinking", "")
        if isinstance(thinking, str):
            result.thinking = thinking.strip()
        if not result.thinking:
            result.parse_errors.append("JSON protocol requires non-empty thinking")

        response_text = payload.get("response", "")
        clarification_text = payload.get("clarification", "")
        if response_text:
            if not isinstance(response_text, str):
                result.parse_errors.append("response must be a string")
            else:
                result.response_text = response_text.strip()
        if clarification_text:
            if not isinstance(clarification_text, str):
                result.parse_errors.append("clarification must be a string")
            else:
                result.clarification_text = clarification_text.strip()

        files = payload.get("files", [])
        if not isinstance(files, list):
            result.parse_errors.append("files must be an array")
            files = []
        for index, item in enumerate(files):
            if not isinstance(item, dict):
                result.parse_errors.append(f"files[{index}] must be an object")
                continue
            path = item.get("path")
            action = item.get("action")
            content = item.get("content", "")
            language = item.get("language", "")
            if not isinstance(path, str) or not path.strip():
                result.parse_errors.append(f"files[{index}].path must be a non-empty string")
                continue
            if not isinstance(action, str) or action.upper() not in {
                "CREATE",
                "MODIFY",
                "DELETE",
            }:
                result.parse_errors.append(
                    f"files[{index}].action must be CREATE, MODIFY, or DELETE"
                )
                continue
            if not isinstance(content, str):
                result.parse_errors.append(f"files[{index}].content must be a string")
                continue
            if action.upper() != "DELETE" and not content:
                result.parse_errors.append(f"files[{index}].content must not be empty")
                continue
            result.files.append(
                FileAction(
                    path=path.strip().lstrip("/\\"),
                    action=action.upper(),
                    content=content,
                    language=language if isinstance(language, str) and language else "text",
                )
            )

        test_command = payload.get("test_command", "")
        if test_command:
            if isinstance(test_command, str):
                result.test_command = self._clean_test_command(test_command)
            else:
                result.parse_errors.append("test_command must be a string")

        if not (result.files or result.response_text or result.clarification_text):
            result.parse_errors.append("JSON protocol requires files, response, or clarification")
        if result.response_text and result.files:
            result.parse_errors.append("JSON protocol cannot combine response with files")
        if result.clarification_text and result.files:
            result.parse_errors.append("JSON protocol cannot combine clarification with files")
        return result

    def _parse_file_blocks(
        self,
        files_raw: str,
        errors: list[str],
    ) -> list[FileAction]:
        files: list[FileAction] = []
        code_blocks = self.CODE_BLOCK_PATTERN.findall(files_raw)
        if not code_blocks:
            action = self._extract_file_metadata(files_raw, "python", errors)
            if action:
                files.append(action)
            else:
                errors.append("No code blocks found in <<FILES>>")
            return files

        for language, content in code_blocks:
            action = self._extract_file_metadata(content, language, errors)
            if action:
                files.append(action)
        return files

    def _extract_file_metadata(
        self,
        content: str,
        default_language: str,
        errors: list[str],
    ) -> FileAction | None:
        path_match = self.FILEPATH_PATTERN.search(content)
        action_match = self.ACTION_PATTERN.search(content)
        if not path_match:
            errors.append("Missing # filepath: in code block")
            return None
        if not action_match:
            errors.append("Missing # action: CREATE or MODIFY in code block")
            return None

        path = path_match.group(1).strip().removesuffix("-->").removesuffix("*/").strip("`'\"")
        for prefix in ("path/to/", "a/", "b/"):
            if path.startswith(prefix):
                path = path[len(prefix) :]

        code_lines = content.splitlines()
        code_start = 0
        for index, line in enumerate(code_lines):
            lowered = line.strip().lower()
            if "filepath:" in lowered or "action:" in lowered:
                code_start = index + 1
                continue
            if not lowered or lowered in {"<!--", "-->", "/*", "*/"}:
                if index <= code_start:
                    code_start = index + 1
                continue
            break
        code = "\n".join(code_lines[code_start:]).strip()
        if not code and action_match.group(1).upper() != "DELETE":
            errors.append(f"Empty file content for {path}")
            return None

        extensions = {
            ".py": "python",
            ".js": "javascript",
            ".mjs": "javascript",
            ".cjs": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".go": "go",
            ".rs": "rust",
            ".java": "java",
            ".cpp": "cpp",
            ".cc": "cpp",
            ".c": "c",
            ".rb": "ruby",
            ".sh": "bash",
            ".sql": "sql",
        }
        language = extensions.get(Path(path).suffix.lower(), default_language or "text")
        return FileAction(
            path=path,
            action=action_match.group(1).upper(),
            content=code,
            language=language,
        )

    @staticmethod
    def _clean_test_command(command: str) -> str:
        command = re.sub(r"^```\w*\r?\n?", "", command.strip())
        command = re.sub(r"\r?\n?```$", "", command)
        return command.splitlines()[0].strip() if command.strip() else ""

    @staticmethod
    def count_file_declarations(text: str) -> int:
        stripped = text.strip()
        if stripped.startswith("```json") and stripped.endswith("```"):
            stripped = stripped[len("```json") : -3].strip()
        if stripped.startswith("{"):
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                payload = {}
            if isinstance(payload, dict) and payload.get("schema") == "nova.patch.v1":
                files = payload.get("files")
                return len(files) if isinstance(files, list) else 0
        return len(
            re.findall(
                r"^(?:#|//|<!--|/\*)\s*filepath\s*:",
                text,
                re.MULTILINE | re.IGNORECASE,
            )
        )


class VerdictType(str, Enum):
    PASS = "PASS"
    REJECT_SCOPE = "REJECT_SCOPE"
    REJECT_SCHEMA = "REJECT_SCHEMA"
    REJECT_FILE_COUNT = "REJECT_FILE_COUNT"
    REJECT_FUNC_NAME = "REJECT_FUNC_NAME"
    REJECT_THINKING_MISMATCH = "REJECT_THINKING_MISMATCH"
    ESCALATE = "ESCALATE"


@dataclass
class GuardrailVerdict:
    type: VerdictType
    passed: bool
    task_id: int
    reason: str
    scope_level: str = ""
    expected_files: int = 0
    actual_files: int = 0
    reroute_count: int = 0

    def __str__(self) -> str:
        status = "PASS" if self.passed else self.type.value
        return (
            f"GUARDRAIL {status} task {self.task_id}: {self.reason} "
            f"(scope={self.scope_level or 'unset'}, "
            f"files={self.actual_files}/{self.expected_files})"
        )


class TaskGuardrail:
    """Deterministic scope, schema, count, and name checks for Nova output."""

    _FUNCTION_PATTERNS = (
        re.compile(r"\b(?:function|method)\s+(?:named|called)\s+`?([A-Za-z_]\w*)`?", re.I),
        re.compile(r"\bcall\s+it\s+`?([A-Za-z_]\w*)`?", re.I),
        re.compile(r"\bdef\s+([A-Za-z_]\w*)\s*\(", re.I),
    )

    def __init__(self, max_reroutes: int = 1):
        self.max_reroutes = max(0, max_reroutes)
        self._reroutes: dict[int, int] = {}

    def _failed(
        self,
        task: AtomicTask,
        kind: VerdictType,
        reason: str,
        actual_files: int = 0,
    ) -> GuardrailVerdict:
        count = self._reroutes.get(task.id, 0) + 1
        self._reroutes[task.id] = count
        verdict = VerdictType.ESCALATE if count > self.max_reroutes else kind
        return GuardrailVerdict(
            type=verdict,
            passed=False,
            task_id=task.id,
            reason=reason,
            scope_level=task.scope_level,
            expected_files=task.expected_files,
            actual_files=actual_files,
            reroute_count=count,
        )

    @staticmethod
    def _passed(
        task: AtomicTask,
        reason: str,
        actual_files: int = 0,
    ) -> GuardrailVerdict:
        return GuardrailVerdict(
            type=VerdictType.PASS,
            passed=True,
            task_id=task.id,
            reason=reason,
            scope_level=task.scope_level,
            expected_files=task.expected_files,
            actual_files=actual_files,
        )

    def pre_check(self, task: AtomicTask) -> GuardrailVerdict:
        if task.scope_level not in {"atomic", "multi_file"}:
            return self._failed(
                task,
                VerdictType.REJECT_SCOPE,
                "Task is vague or missing an enforceable scope tag.",
            )
        if task.expected_files < 1:
            return self._failed(
                task,
                VerdictType.REJECT_SCOPE,
                "Coding task has no positive expected file count.",
            )
        if task.scope_level == "atomic" and task.expected_files != 1:
            return self._failed(
                task,
                VerdictType.REJECT_SCOPE,
                "Atomic tasks must produce exactly one file.",
            )
        return self._passed(task, "Task scope is explicit and bounded.")

    def schema_check(
        self,
        task: AtomicTask,
        output: str,
    ) -> GuardrailVerdict:
        parser = NovaOutputParser()
        parsed = parser.parse(output)
        if parsed.is_valid:
            return self._passed(task, "Output matches the Nova protocol.", len(parsed.files))
        return self._failed(
            task,
            VerdictType.REJECT_SCHEMA,
            "Output schema failed: " + "; ".join(parsed.parse_errors),
            len(parsed.files),
        )

    def post_check(
        self,
        task: AtomicTask,
        output: str,
    ) -> GuardrailVerdict:
        parsed = NovaOutputParser().parse(output)
        if parsed.response_text or parsed.clarification_text:
            return self._passed(task, "Non-code response contains no file operations.")
        actual = len(parsed.files)
        if actual != task.expected_files:
            return self._failed(
                task,
                VerdictType.REJECT_FILE_COUNT,
                f"Expected {task.expected_files} file(s), received {actual}.",
                actual,
            )
        return self._passed(task, "File count matches the task contract.", actual)

    def function_name_check(
        self,
        task: AtomicTask,
        output: str,
        prompt: str = "",
    ) -> GuardrailVerdict:
        expected: set[str] = set()
        for pattern in self._FUNCTION_PATTERNS:
            expected.update(pattern.findall(prompt or task.description))
        if not expected:
            return self._passed(task, "No explicit function name to verify.")
        missing = [
            name for name in sorted(expected) if not re.search(rf"\b{re.escape(name)}\b", output)
        ]
        if missing:
            return self._failed(
                task,
                VerdictType.REJECT_FUNC_NAME,
                f"Expected function name(s) missing: {', '.join(missing)}.",
            )
        return self._passed(task, "All explicit function names are present.")

    def thinking_files_consistency_check(
        self,
        task: AtomicTask,
        output: str,
    ) -> GuardrailVerdict:
        parsed = NovaOutputParser().parse(output)
        mentions_file = bool(
            re.search(
                r"\b(?:create|write|modify|implement)(?:ing)?\b.*\bfile\b",
                parsed.thinking,
                re.I | re.S,
            )
        )
        if mentions_file and not parsed.files:
            return self._failed(
                task,
                VerdictType.REJECT_THINKING_MISMATCH,
                "Thinking promises a file operation but FILES is empty.",
            )
        return self._passed(
            task,
            "Thinking and emitted file operations are consistent.",
            len(parsed.files),
        )


@dataclass
class LiteralConstraint:
    type: str
    value: str
    original_text: str
    prompt_text: str = ""


class ConstraintExtractor:
    """Extract explicit literals that can be checked mechanically."""

    def __init__(self, ceiling_node: Any):
        self.ceiling = ceiling_node

    def extract(self, prompt: str) -> list[LiteralConstraint]:
        found: list[LiteralConstraint] = []
        for match in re.finditer(r"(?:return|status)(?:\s+code)?\s+(\d{3})", prompt, re.I):
            found.append(LiteralConstraint("status_code", match.group(1), match.group(0), prompt))
        quoted = re.compile(
            r"(?:print|output|status:|return|body|emit|write)"
            r"[^\n,;.!?]{0,80}?(['\"])([^'\"\n]+)\1",
            re.I,
        )
        for match in quoted.finditer(prompt):
            found.append(LiteralConstraint("string_output", match.group(2), match.group(0), prompt))
        unquoted = re.compile(
            r"(?:prints?|outputs?|emits?|writes?)\s+exactly\s+"
            r"([A-Za-z0-9_./:+-]+(?: [A-Za-z0-9_./:+-]+)*)",
            re.I,
        )
        for match in unquoted.finditer(prompt):
            value = re.split(
                r"\s+(?:and|then|with|under)\b",
                match.group(1),
                maxsplit=1,
                flags=re.I,
            )[0]
            found.append(LiteralConstraint("string_output", value.strip(), match.group(0), prompt))
        assignment = re.compile(
            r"set\s+(?:it|[\w_]+)\s+to\s+(?:an?\s+)?"
            r"(empty string|null|true|false|[\w_]+|['\"][^'\"]+['\"])",
            re.I,
        )
        for match in assignment.finditer(prompt):
            value = match.group(1)
            if value.lower() == "empty string":
                value = '""'
            elif value.startswith(("'", '"')):
                value = value[1:-1]
            found.append(LiteralConstraint("assignment", value, match.group(0), prompt))

        unique: dict[tuple[str, str], LiteralConstraint] = {}
        for item in found:
            if (
                item.type == "string_output"
                and any(char.isspace() for char in item.value)
                and re.search(
                    r"\b(travers|algorithm|sort|calculat|recurs|graph|search)\w*\b",
                    prompt,
                    re.I,
                )
            ):
                continue
            unique[(item.type, item.value)] = item
        return list(unique.values())


class ConstraintVerifier:
    """Verify literal requirements against generated source."""

    OUTPUT_MARKERS = (
        "print",
        "return",
        "status",
        "body",
        "send",
        "json",
        "console.log",
        "cout",
        "printf",
        "println",
        "fmt.",
        "write",
        "emit",
    )

    def __init__(self, ceiling_node: Any):
        self.ceiling = ceiling_node

    def verify_single(
        self,
        constraint: LiteralConstraint,
        code: str,
    ) -> tuple[bool, str]:
        if constraint.value not in code:
            return False, f"Constraint FAILED: '{constraint.value}' not found in code."
        relevant = [line.lower() for line in code.splitlines() if constraint.value in line]
        if constraint.type == "status_code" and not any(
            "status" in line or "return" in line for line in relevant
        ):
            return False, (
                f"Constraint FAILED: '{constraint.value}' is not in a return/status branch."
            )
        if constraint.type == "string_output" and not any(
            any(marker in line for marker in self.OUTPUT_MARKERS) for line in relevant
        ):
            return False, (f"Constraint FAILED: '{constraint.value}' is not in an output branch.")
        return True, f"Constraint PASSED: found '{constraint.value}' in a valid branch."

    def verify(
        self,
        constraints: list[LiteralConstraint],
        files: list[FileAction],
    ) -> tuple[bool, str]:
        code = "\n".join(item.content for item in files)
        reasons: list[str] = []
        for constraint in constraints:
            passed, reason = self.verify_single(constraint, code)
            reasons.append(reason)
            if not passed:
                return False, " | ".join(reasons)
        return True, " | ".join(reasons)


@dataclass
class InferenceMetrics:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_duration_ms: float = 0.0


@dataclass
class GenerationResult:
    text: str
    done: bool
    metrics: InferenceMetrics = field(default_factory=InferenceMetrics)


class OllamaClient:
    """Minimal dependency-free Ollama client for Nova V11."""

    def __init__(self, base_url: str | None = None, timeout: int | None = None):
        resolved_url = (
            base_url
            or os.environ.get("NEXUS_OLLAMA_URL")
            or os.environ.get("OLLAMA_HOST")
            or "http://127.0.0.1:11434"
        )
        if not resolved_url.startswith(("http://", "https://")):
            resolved_url = f"http://{resolved_url}"

        parsed_host = resolved_url.split("://")[-1].split(":")[0]
        if parsed_host not in ("127.0.0.1", "localhost", "::1") and not os.environ.get(
            "NEXUS_ALLOW_REMOTE_OLLAMA"
        ):
            raise ValueError(
                f"Remote Ollama host '{parsed_host}' is blocked. Set NEXUS_ALLOW_REMOTE_OLLAMA=1 to override."
            )

        self.base_url = resolved_url.rstrip("/")
        self.timeout = timeout or int(os.environ.get("NEXUS_OLLAMA_TIMEOUT", "180"))

    def nova_generate(
        self,
        prompt: str,
        model: str,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        num_ctx: int = 8192,
    ) -> GenerationResult:
        payload = json.dumps(
            {
                "model": model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": temperature,
                    "num_predict": max_tokens,
                    "num_ctx": num_ctx,
                },
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                "Ollama is unavailable or returned an invalid response. Start Ollama, "
                f"install the '{model}' model, and verify {self.base_url}/api/tags. "
                f"Underlying error: {exc}"
            ) from exc
        return GenerationResult(
            text=str(data.get("response", "")),
            done=bool(data.get("done", False)),
            metrics=InferenceMetrics(
                prompt_tokens=int(data.get("prompt_eval_count", 0) or 0),
                completion_tokens=int(data.get("eval_count", 0) or 0),
                total_duration_ms=(time.monotonic() - started) * 1000,
            ),
        )


@dataclass
class SanitizeResult:
    original_prompt: str
    sanitized_prompt: str
    is_rejected: bool = False
    is_modified: bool = False
    injection_patterns_found: list[str] = field(default_factory=list)
    dangerous_payloads_found: list[str] = field(default_factory=list)


class InputSanitizer:
    """Block protocol-embedded destructive commands and label prompt injection."""

    _DANGEROUS = (
        re.compile(
            r"<<TEST_COMMAND>>\s*(?:sudo\s+)?(?:rm|del|format|mkfs|dd|shutdown|reboot)\b",
            re.I,
        ),
        re.compile(r"\brm\s+-[^\n]*r[^\n]*f\s+(?:/|~|\$HOME)\b", re.I),
    )
    _INJECTION = (
        re.compile(r"ignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?", re.I),
        re.compile(r"system\s+(?:override|prompt|message)\s*:", re.I),
        re.compile(r"you\s+are\s+now\b", re.I),
    )

    def sanitize(self, prompt: str) -> SanitizeResult:
        dangerous = [pattern.pattern for pattern in self._DANGEROUS if pattern.search(prompt)]
        if dangerous:
            return SanitizeResult(
                original_prompt=prompt,
                sanitized_prompt="",
                is_rejected=True,
                dangerous_payloads_found=dangerous,
            )
        injections = [pattern.pattern for pattern in self._INJECTION if pattern.search(prompt)]
        sanitized = prompt
        for pattern in self._INJECTION:
            sanitized = pattern.sub("[UNTRUSTED_INSTRUCTION]", sanitized)
        return SanitizeResult(
            original_prompt=prompt,
            sanitized_prompt=sanitized,
            is_modified=bool(injections),
            injection_patterns_found=injections,
        )


class InternNode:
    """Execute one atomic task through the local Nova model."""

    def __init__(
        self,
        model: str = "nova_codex",
        max_retries: int = 1,
        temperature: float = 0.2,
        client: OllamaClient | None = None,
    ):
        self.model = model
        self.max_retries = max_retries
        self.temperature = temperature
        self.client = client or OllamaClient()
        self.parser = NovaOutputParser()
        self.sanitizer = InputSanitizer()

    def execute(
        self,
        task: AtomicTask,
        context: str = "",
        override_prompt: str = "",
    ) -> TaskResult:
        started = time.monotonic()
        prompt = override_prompt or task.description
        if context:
            heading = (
                "Authoritative current file context"
                if override_prompt
                else "Context from previous tasks"
            )
            prompt = f"{heading}:\n{context}\n\nTask:\n{prompt}"
        sanitized = self.sanitizer.sanitize(prompt)
        if sanitized.is_rejected:
            return TaskResult(
                task=task,
                response=ParsedResponse(
                    raw_text="[BLOCKED BY SANITIZER]",
                    parse_errors=["Prompt contains a destructive protocol payload."],
                ),
                execution_time_ms=(time.monotonic() - started) * 1000,
            )

        prompt = sanitized.sanitized_prompt
        budget = 6144 if len(prompt) > 2500 else 4096
        generated = self.client.nova_generate(
            prompt=prompt,
            model=self.model,
            temperature=self.temperature,
            max_tokens=budget,
            num_ctx=max(8192, budget + 4096),
        )
        parsed = self.parser.parse(generated.text)
        if not generated.done:
            parsed.parse_errors.append("Ollama generation did not finish.")
        if self._has_unbalanced_generated_code(parsed):
            parsed.parse_errors.append("Generated code has unbalanced delimiters.")
        return TaskResult(
            task=task,
            response=parsed,
            execution_time_ms=(time.monotonic() - started) * 1000,
        )

    @staticmethod
    def _has_unbalanced_generated_code(parsed: ParsedResponse) -> bool:
        c_family = {".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".cpp", ".cc", ".c", ".java"}
        for action in parsed.files:
            if Path(action.path).suffix.lower() not in c_family:
                continue
            text = re.sub(
                r"//.*?$|/\*.*?\*/|'(?:\\.|[^'\\])*'|\"(?:\\.|[^\"\\])*\"",
                "",
                action.content,
                flags=re.MULTILINE | re.DOTALL,
            )
            if any(
                text.count(left) != text.count(right)
                for left, right in (("{", "}"), ("(", ")"), ("[", "]"))
            ):
                return True
        return False


class TestExecutor:
    """Replay Nova file operations inside a caller-owned isolated directory."""

    _PATCH = re.compile(
        r"^<+\s*\r?\n(.*?)^=+\s*\r?\n(.*?)^>+\s*$",
        re.DOTALL | re.MULTILINE,
    )

    def __init__(self, workspace_dir: str, timeout: int = 30):
        self.workspace_dir = str(Path(workspace_dir).resolve())
        self.timeout = timeout

    def write_files(
        self,
        files: list[FileAction],
        strict_verify: bool = False,
    ) -> list[str]:
        root = Path(self.workspace_dir).resolve()
        written: list[str] = []
        for action in files:
            relative = Path(os.path.normpath(action.path.lstrip("/\\")))
            target = (root / relative).resolve()
            try:
                target.relative_to(root)
            except ValueError as exc:
                raise ValueError(f"Path escapes workspace: {action.path}") from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            kind = action.action.upper()
            if kind == "DELETE":
                raise ValueError("Nova DELETE operations require explicit Nexus approval.")
            if kind == "CREATE" and target.exists() and strict_verify:
                raise ValueError(f"CREATE target already exists: {action.path}")

            original = target.read_text(encoding="utf-8") if target.exists() else ""
            matches = self._PATCH.findall(action.content)
            if kind == "MODIFY" and matches:
                updated = original
                for old, new in matches:
                    if not old or updated.count(old) != 1:
                        raise ValueError(
                            f"Patch search text occurs {updated.count(old)} times in "
                            f"{action.path}; expected exactly once"
                        )
                    updated = updated.replace(old, new, 1)
            else:
                updated = action.content

            if not updated.strip():
                raise ValueError(f"Refusing empty content for {action.path}")
            target.write_text(updated, encoding="utf-8")
            if strict_verify and target.read_text(encoding="utf-8") != updated:
                raise ValueError(f"Disk readback mismatch for {action.path}")
            written.append(str(relative))
        return written


CEILING_SYSTEM_PROMPT = """You are the Ceiling planner in Nexus.
Decompose the request into bounded, dependency-ordered coding tasks.
Return strict JSON with a tasks array. Every task must contain id, description,
expected_files, scope_level (atomic, multi_file, or vague), and depends_on.
Descriptions must include exact repository-relative paths and requirements.
Do not split one standalone executable file into multiple tasks."""


class CeilingNode:
    """Parser-compatible local Ceiling helper used by Nexus guardrails."""

    def __init__(self, provider: str = "manual", api_key: str = ""):
        self.provider = provider
        self.api_key = api_key
        self.client: Any = "manual"
        self.model_name = ""
        self.tokens_used = 0

    def _parse_tasks(self, text: str) -> list[AtomicTask]:
        candidate = text
        if "```json" in candidate:
            candidate = candidate.split("```json", 1)[1].split("```", 1)[0]
        elif "```" in candidate:
            candidate = candidate.split("```", 1)[1].split("```", 1)[0]
        try:
            data = json.loads(candidate.strip())
            tasks: list[AtomicTask] = []
            for item in data.get("tasks", []):
                tasks.append(
                    AtomicTask(
                        id=int(item.get("id", len(tasks) + 1)),
                        description=str(item["description"]),
                        depends_on=[int(value) for value in item.get("depends_on", [])],
                        expected_files=int(item.get("expected_files", 1)),
                        scope_level=str(item.get("scope_level", "atomic")).lower().strip(),
                    )
                )
            return tasks
        except (TypeError, ValueError, KeyError, json.JSONDecodeError):
            return [
                AtomicTask(
                    id=1,
                    description=text,
                    expected_files=-1,
                    scope_level="",
                )
            ]


def validate_python_source(source: str) -> tuple[bool, str]:
    """Small helper used by protocol tests without executing generated code."""
    try:
        ast.parse(source)
    except SyntaxError as exc:
        return False, str(exc)
    return True, ""
