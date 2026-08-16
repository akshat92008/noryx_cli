"""Deterministic request-semantics compiler and safe fast-path primitives.

This module separates *what action the user requested* from *how the response
must be rendered*.  That distinction prevents exact/raw response requests and
read-only negation from being misclassified as repository mutations.
"""

from __future__ import annotations

import ast
import operator
import re
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from nexus.path_grammar import extract_repository_paths


class Action(str, Enum):
    CHAT = "chat"
    RESPOND = "respond"
    READ_FILE = "read_file"
    SEARCH_REPO = "search_repo"
    WRITE_FILE = "write_file"
    EDIT_FILE = "edit_file"
    RUN_COMMAND = "run_command"
    BROWSER_READ = "browser_read"
    BROWSER_INTERACT = "browser_interact"
    CALCULATE = "calculate"
    TRANSFORM = "transform"
    MEMORY_READ = "memory_read"
    MEMORY_WRITE = "memory_write"
    SCREENSHOT = "screenshot"
    ENGINEERING_TASK = "engineering_task"


class ResponseMode(str, Enum):
    NORMAL = "normal"
    EXACT_LITERAL = "exact_literal"
    RAW_VALUE = "raw_value"
    JSON_ONLY = "json_only"
    LIST_ONLY = "list_only"
    NUMBER_ONLY = "number_only"
    SILENT = "silent"
    SUMMARY = "summary"


class SideEffectPolicy(str, Enum):
    READ_ONLY = "read_only"
    NO_FILE_WRITE = "no_file_write"
    ALLOW_FILE_WRITE = "allow_file_write"
    REQUIRE_CONFIRMATION = "require_confirmation"
    ALLOW_OPERATION = "allow_operation"


@dataclass(frozen=True)
class RequestSemantics:
    action: Action
    response_mode: ResponseMode = ResponseMode.NORMAL
    side_effect_policy: SideEffectPolicy = SideEffectPolicy.READ_ONLY
    targets: tuple[str, ...] = ()
    payload: str = ""
    constraints: tuple[str, ...] = ()
    deterministic: bool = False

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["action"] = self.action.value
        data["response_mode"] = self.response_mode.value
        data["side_effect_policy"] = self.side_effect_policy.value
        data["targets"] = list(self.targets)
        data["constraints"] = list(self.constraints)
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RequestSemantics | None":
        if not data:
            return None
        return cls(
            action=Action(str(data.get("action", Action.CHAT.value))),
            response_mode=ResponseMode(str(data.get("response_mode", ResponseMode.NORMAL.value))),
            side_effect_policy=SideEffectPolicy(
                str(data.get("side_effect_policy", SideEffectPolicy.READ_ONLY.value))
            ),
            targets=tuple(str(item) for item in data.get("targets", ()) or ()),
            payload=str(data.get("payload", "") or ""),
            constraints=tuple(str(item) for item in data.get("constraints", ()) or ()),
            deterministic=bool(data.get("deterministic", False)),
        )


_NO_WRITE_RE = re.compile(
    r"\b(?:do\s+not|don't|dont|never|without|avoid)\s+"
    r"(?:writing|write|creating|create|editing|edit|modifying|modify|changing|change|"
    r"touching|touch|altering|alter|rewriting|rewrite|updating|update|deleting|delete)\b"
    r"(?:\s+anything\b|(?:[^.;\n]*)\b(?:any\s+)?files?\b)",
    re.IGNORECASE,
)

_EXACT_RE = re.compile(
    r"\b(?:reply|respond|return|output)\s+(?:with\s+)?exactly\s*:?[ \t]*(?P<payload>[^\n]+)",
    re.IGNORECASE,
)

_RAW_RE = re.compile(
    r"\b(?:return|reply|output|show|give)(?:\s+me)?\s+(?:only\s+)?(?:the\s+)?raw\s+(?:value|text|content)\b|"
    r"\b(?:return|reply|output)\s+(?:only\s+)?(?:the\s+)?value\b",
    re.IGNORECASE,
)

_NUMBER_ONLY_RE = re.compile(
    r"\b(?:return|reply|output)\s+(?:only\s+)?(?:the\s+)?number\b",
    re.IGNORECASE,
)

_JSON_ONLY_RE = re.compile(r"\b(?:return|output|reply)\s+(?:only\s+)?json\b|\bjson\s+only\b", re.I)
_LIST_ONLY_RE = re.compile(r"\b(?:return|output|reply)\s+(?:only\s+)?(?:the\s+)?list\b", re.I)
_SILENT_RE = re.compile(r"\b(?:do not|don't|dont)\s+(?:reply|respond)|\bsilent(?:ly)?\b", re.I)

_ARITHMETIC_EXPR = re.compile(r"(?P<expr>[0-9][0-9\s+\-*/%().^]{0,120})")


def _strip_literal(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'", "`"}:
        return value[1:-1]
    return value


def _extract_bare_read_target(text: str) -> str:
    patterns = (
        r"\b(?:read|open|show)\s+(?:the\s+)?(?P<target>[A-Za-z0-9_.-]{1,80})(?=\s|$|[,:;.])",
        r"\b(?:value|content)\s+(?:of|from)\s+(?P<target>[A-Za-z0-9_.-]{1,80})(?=\s|$|[,:;.])",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            candidate = match.group("target").strip("`'\"")
            if candidate.lower() not in {"the", "a", "an", "file", "page"}:
                return candidate
    return ""


def compile_request_semantics(user_input: str) -> RequestSemantics:
    """Compile explicit request semantics without consulting an LLM."""

    text = (user_input or "").strip()
    lowered = text.lower()
    paths = extract_repository_paths(text)
    constraints: list[str] = []
    no_write = bool(_NO_WRITE_RE.search(text))
    if no_write:
        constraints.append("FORBID_FILE_WRITE:*")

    response_mode = ResponseMode.NORMAL
    exact = _EXACT_RE.search(text)
    payload = ""
    if exact:
        response_mode = ResponseMode.EXACT_LITERAL
        payload = _strip_literal(exact.group("payload").rstrip(" ."))
    elif _NUMBER_ONLY_RE.search(text):
        response_mode = ResponseMode.NUMBER_ONLY
    elif _RAW_RE.search(text):
        response_mode = ResponseMode.RAW_VALUE
    elif _JSON_ONLY_RE.search(text):
        response_mode = ResponseMode.JSON_ONLY
    elif _LIST_ONLY_RE.search(text):
        response_mode = ResponseMode.LIST_ONLY
    elif _SILENT_RE.search(text):
        response_mode = ResponseMode.SILENT

    # Exact literal is a response instruction, not an engineering mutation.
    if exact:
        return RequestSemantics(
            action=Action.RESPOND,
            response_mode=response_mode,
            side_effect_policy=SideEffectPolicy.NO_FILE_WRITE if no_write else SideEffectPolicy.READ_ONLY,
            payload=payload,
            constraints=tuple(constraints),
            deterministic=True,
        )

    arithmetic_signal = bool(re.search(r"\b(?:calculate|compute|evaluate|what\s+is)\b", lowered))
    arithmetic_match = _ARITHMETIC_EXPR.search(text)
    if arithmetic_signal and arithmetic_match:
        return RequestSemantics(
            action=Action.CALCULATE,
            response_mode=(
                ResponseMode.NUMBER_ONLY
                if response_mode == ResponseMode.NORMAL
                else response_mode
            ),
            side_effect_policy=SideEffectPolicy.READ_ONLY,
            payload=arithmetic_match.group("expr").strip(),
            constraints=tuple(constraints),
            deterministic=True,
        )

    transform = re.search(
        r"\b(?P<op>uppercase|lowercase|capitalize|reverse)\s+[\"'`](?P<value>.*?)[\"'`]",
        text,
        re.I,
    )
    if transform:
        return RequestSemantics(
            action=Action.TRANSFORM,
            response_mode=response_mode,
            side_effect_policy=SideEffectPolicy.READ_ONLY,
            payload=f"{transform.group('op').lower()}\n{transform.group('value')}",
            deterministic=True,
        )

    if re.search(r"\b(?:what did i tell you|do you remember|remember what i|my favorite|my favourite)\b", lowered):
        return RequestSemantics(
            action=Action.MEMORY_READ,
            response_mode=response_mode,
            side_effect_policy=SideEffectPolicy.READ_ONLY,
            constraints=tuple(constraints),
        )

    if re.search(r"\b(?:take|capture)\s+(?:a\s+)?screenshot\b", lowered):
        return RequestSemantics(
            action=Action.SCREENSHOT,
            response_mode=response_mode,
            side_effect_policy=SideEffectPolicy.NO_FILE_WRITE if no_write else SideEffectPolicy.READ_ONLY,
            constraints=tuple(constraints),
        )

    if re.search(r"\b(?:browser|webpage|page|selector|locator)\b", lowered) and re.search(
        r"\b(?:get|read|return|show|extract|raw value|text|attribute)\b", lowered
    ):
        return RequestSemantics(
            action=Action.BROWSER_READ,
            response_mode=response_mode,
            side_effect_policy=SideEffectPolicy.READ_ONLY,
            targets=tuple(paths),
            constraints=tuple(constraints),
        )

    # A read/explain/search clause dominates negated mutation words.
    read_signal = bool(
        re.search(
            r"\b(?:read|explain|describe|inspect|analy[sz]e|tell me whether|tell me if|"
            r"does\s+.+\s+import|what does|show me|find|search|locate)\b",
            lowered,
        )
    )
    if read_signal or no_write:
        target_list = list(paths)
        bare = _extract_bare_read_target(text)
        if not target_list and bare:
            target_list.append(bare)
        action = Action.READ_FILE if target_list else Action.SEARCH_REPO
        return RequestSemantics(
            action=action,
            response_mode=response_mode,
            side_effect_policy=SideEffectPolicy.NO_FILE_WRITE if no_write else SideEffectPolicy.READ_ONLY,
            targets=tuple(target_list),
            constraints=tuple(constraints),
            deterministic=(action == Action.READ_FILE and response_mode == ResponseMode.RAW_VALUE),
        )

    # Explicit mutation verbs only count when they are not part of a negated
    # clause handled above.
    if re.search(r"\b(?:write|create|generate)\b", lowered) and paths:
        return RequestSemantics(
            action=Action.WRITE_FILE,
            response_mode=response_mode,
            side_effect_policy=SideEffectPolicy.ALLOW_FILE_WRITE,
            targets=tuple(paths),
            constraints=tuple(constraints),
        )
    if re.search(r"\b(?:edit|modify|change|update|patch|refactor|rename)\b", lowered):
        return RequestSemantics(
            action=Action.EDIT_FILE,
            response_mode=response_mode,
            side_effect_policy=SideEffectPolicy.ALLOW_FILE_WRITE,
            targets=tuple(paths),
            constraints=tuple(constraints),
        )
    if re.search(r"\b(?:run|execute)\s+(?:the\s+)?(?:command|shell|script|process)\b", lowered):
        return RequestSemantics(
            action=Action.RUN_COMMAND,
            response_mode=response_mode,
            side_effect_policy=SideEffectPolicy.REQUIRE_CONFIRMATION,
            constraints=tuple(constraints),
        )
    if re.search(
        r"\b(?:build|implement|develop|fix|debug|repair|migrate|optimi[sz]e|deploy|test|secure|harden)\b",
        lowered,
    ):
        return RequestSemantics(
            action=Action.ENGINEERING_TASK,
            response_mode=response_mode,
            side_effect_policy=SideEffectPolicy.ALLOW_FILE_WRITE,
            targets=tuple(paths),
            constraints=tuple(constraints),
        )

    return RequestSemantics(
        action=Action.CHAT,
        response_mode=response_mode,
        side_effect_policy=SideEffectPolicy.READ_ONLY,
        targets=tuple(paths),
        constraints=tuple(constraints),
    )


_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _safe_number(node: ast.AST) -> int | float:
    if isinstance(node, ast.Expression):
        return _safe_number(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(
        node.value, bool
    ):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _ALLOWED_BINOPS:
        left = _safe_number(node.left)
        right = _safe_number(node.right)
        if isinstance(node.op, ast.Pow) and abs(float(right)) > 12:
            raise ValueError("exponent is too large for deterministic fast path")
        result = _ALLOWED_BINOPS[type(node.op)](left, right)
        if abs(float(result)) > 1e100:
            raise ValueError("numeric result is too large")
        return result
    if isinstance(node, ast.UnaryOp) and type(node.op) in _ALLOWED_UNARY:
        return _ALLOWED_UNARY[type(node.op)](_safe_number(node.operand))
    raise ValueError("unsupported arithmetic expression")


def _render_number(value: int | float) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def execute_deterministic(
    semantics: RequestSemantics,
    *,
    working_dir: str | Path,
) -> str | None:
    """Execute a deterministic, side-effect-free request when possible."""

    if not semantics.deterministic:
        return None
    if semantics.side_effect_policy not in {
        SideEffectPolicy.READ_ONLY,
        SideEffectPolicy.NO_FILE_WRITE,
    }:
        return None

    if semantics.action == Action.RESPOND and semantics.response_mode == ResponseMode.EXACT_LITERAL:
        return semantics.payload

    if semantics.action == Action.CALCULATE:
        expression = semantics.payload.replace("^", "**")
        tree = ast.parse(expression, mode="eval")
        return _render_number(_safe_number(tree))

    if semantics.action == Action.TRANSFORM:
        operation, _, value = semantics.payload.partition("\n")
        if operation == "uppercase":
            return value.upper()
        if operation == "lowercase":
            return value.lower()
        if operation == "capitalize":
            return value.capitalize()
        if operation == "reverse":
            return value[::-1]
        return None

    if semantics.action == Action.READ_FILE and semantics.response_mode == ResponseMode.RAW_VALUE:
        if len(semantics.targets) != 1:
            return None
        root = Path(working_dir).expanduser().resolve()
        target = (root / semantics.targets[0]).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            return None
        if not target.is_file() or target.stat().st_size > 2 * 1024 * 1024:
            return None
        return target.read_text(encoding="utf-8", errors="replace").strip()

    return None
