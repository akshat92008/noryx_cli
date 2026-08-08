"""
Base Subagent — foundation class for isolated agent instances.

Each subagent runs in its own context with a specialized system prompt
and limited tool access. Results are reported back as summaries.
"""

from dataclasses import dataclass, field
from enum import Enum


class SubagentStatus(str, Enum):
    """Status of a subagent."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class SubagentResult:
    """Result from a subagent execution."""

    subagent_name: str
    task: str
    status: SubagentStatus
    summary: str = ""
    findings: list[str] = field(default_factory=list)
    tool_calls_made: int = 0
    files_touched: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0
    started_at: str = ""
    completed_at: str = ""

    @property
    def succeeded(self) -> bool:
        return self.status == SubagentStatus.COMPLETED

    def format_report(self) -> str:
        icon = "✅" if self.succeeded else "❌"
        lines = [
            f"{icon} Subagent: {self.subagent_name}",
            f"   Task: {self.task}",
            f"   Status: {self.status.value}",
        ]
        if self.summary:
            lines.append(f"   Summary: {self.summary}")
        if self.findings:
            lines.append(f"   Findings ({len(self.findings)}):")
            for f in self.findings[:10]:
                lines.append(f"     • {f}")
        if self.errors:
            lines.append(f"   Errors ({len(self.errors)}):")
            for e in self.errors[:5]:
                lines.append(f"     ✗ {e}")
        if self.files_touched:
            lines.append(f"   Files: {', '.join(self.files_touched[:10])}")
        lines.append(f"   Tool calls: {self.tool_calls_made} | Duration: {self.duration_ms}ms")
        return "\n".join(lines)


class BaseSubagent:
    """
    Base class for subagents. A subagent is a lightweight, isolated agent
    that executes a specific task and returns a summary.

    Subagents have:
    - Their own system prompt
    - Limited tool access
    - Their own message history (isolated from main agent)
    - A max iteration count (shorter than main agent)

    Usage:
        class SecurityAuditor(BaseSubagent):
            name = "security_auditor"
            system_prompt = "You are a security auditor..."
            allowed_tools = ["read_file", "search_code", "list_directory"]
            max_iterations = 15
    """

    name: str = "base_subagent"
    description: str = "Base subagent"
    system_prompt: str = "You are a helpful coding assistant."
    allowed_tools: list[str] = []  # Empty = all tools
    max_iterations: int = 20
    report_format: str = "summary"  # "summary", "detailed", "findings"

    def __init__(self, task: str, working_dir: str = ""):
        self.task = task
        self.working_dir = working_dir
        self.status = SubagentStatus.PENDING
        self._result: SubagentResult | None = None

    def get_initial_prompt(self) -> str:
        """
        Build the initial prompt for this subagent.
        Includes the task and any special instructions.
        """
        return f"""Your task: {self.task}

Instructions:
1. Execute this task thoroughly and systematically
2. Use the available tools to investigate and act
3. When done, provide a clear, structured summary of your findings/actions
4. Focus only on this specific task — do not go off-scope

Begin working on the task now."""

    def get_system_prompt_full(self) -> str:
        """Build the complete system prompt for this subagent."""
        base = self.system_prompt
        if self.allowed_tools:
            base += f"\n\nYou may only use these tools: {', '.join(self.allowed_tools)}"
        base += f"\n\nMax iterations: {self.max_iterations}"
        return base

    def process_result(self, content: str, tool_events: list[dict]) -> SubagentResult:
        """Process the raw agent output into a structured result."""
        errors = [e.get("result", "")[:200] for e in tool_events if not e.get("success", True)]
        failed = bool(errors) or (content or "").lstrip().upper().startswith(
            ("ERROR:", "BLOCKED:", "❌ EXECUTION FAILED")
        )
        self._result = SubagentResult(
            subagent_name=self.name,
            task=self.task,
            status=SubagentStatus.FAILED if failed else SubagentStatus.COMPLETED,
            summary=content[:2000] if content else "No output",
            tool_calls_made=len(tool_events),
            files_touched=list(
                set(
                    e.get("args", {}).get("path", "")
                    for e in tool_events
                    if e.get("args", {}).get("path")
                )
            ),
            errors=errors,
        )
        return self._result

    def to_dict(self) -> dict:
        """Serialize for API/UI."""
        return {
            "name": self.name,
            "description": self.description,
            "task": self.task,
            "status": self.status.value,
            "max_iterations": self.max_iterations,
            "allowed_tools": self.allowed_tools,
        }
