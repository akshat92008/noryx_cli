"""
Reflection Engine — self-verification loop that evaluates tool results,
decides if retries are needed, and ensures quality after each action.

Architecture:
    Tool Execution → Reflection → Decision (continue / retry / rollback / escalate)
"""

import re
from dataclasses import dataclass
from enum import Enum


class ReflectionVerdict(str, Enum):
    """Outcome of a reflection check."""

    SUCCESS = "success"  # Action succeeded, continue
    RETRY = "retry"  # Action failed, try again (possibly differently)
    ROLLBACK = "rollback"  # Action caused damage, undo it
    ESCALATE = "escalate"  # Can't fix automatically, ask user
    CONTINUE = "continue"  # Partial success, keep going
    SKIP = "skip"  # Non-critical failure, skip and move on


@dataclass
class ReflectionResult:
    """Result of a reflection analysis."""

    verdict: ReflectionVerdict
    reason: str
    suggestion: str = ""
    confidence: float = 1.0  # 0.0 to 1.0
    retry_count: int = 0
    max_retries: int = 3

    @property
    def should_retry(self) -> bool:
        return self.verdict == ReflectionVerdict.RETRY and self.retry_count < self.max_retries

    @property
    def should_rollback(self) -> bool:
        return self.verdict == ReflectionVerdict.ROLLBACK

    @property
    def should_escalate(self) -> bool:
        return self.verdict == ReflectionVerdict.ESCALATE


# ── Error Pattern Database ───────────────────────────────────────────────────

_ERROR_PATTERNS = {
    # File operation errors
    r"File not found|No such file": {
        "verdict": ReflectionVerdict.RETRY,
        "suggestion": "File doesn't exist. Try listing the directory first to find the correct path.",
    },
    r"Text not found in.*Make sure old_text matches": {
        "verdict": ReflectionVerdict.RETRY,
        "suggestion": "The edit_file old_text didn't match. Read the file first to get the exact text.",
    },
    r"Found \d+ occurrences.*provide more context": {
        "verdict": ReflectionVerdict.RETRY,
        "suggestion": "The old_text matched multiple places. Include more surrounding context to make it unique.",
    },
    r"Permission denied|EACCES": {
        "verdict": ReflectionVerdict.ESCALATE,
        "suggestion": "Permission denied. Ask the user to check file permissions.",
    },
    r"File too large": {
        "verdict": ReflectionVerdict.RETRY,
        "suggestion": "File is too large to read entirely. Use start_line/end_line parameters or search_code instead.",
    },
    # Command execution errors
    r"exit code [1-9]": {
        "verdict": ReflectionVerdict.RETRY,
        "suggestion": "Command failed. Check the error output and fix the issue.",
    },
    r"Command timed out": {
        "verdict": ReflectionVerdict.RETRY,
        "suggestion": "Command timed out. Try with a longer timeout or use process_run for background execution.",
    },
    r"command not found|not installed|not in PATH": {
        "verdict": ReflectionVerdict.ESCALATE,
        "suggestion": "Required tool is not installed. Ask the user to install it.",
    },
    # Git errors
    r"not a git repository": {
        "verdict": ReflectionVerdict.CONTINUE,
        "suggestion": "Not in a git repo. Initialize one with git_commit or skip git operations.",
    },
    r"merge conflict|CONFLICT": {
        "verdict": ReflectionVerdict.ESCALATE,
        "suggestion": "Merge conflict detected. Present the conflicts to the user for resolution.",
    },
    r"nothing to commit": {
        "verdict": ReflectionVerdict.CONTINUE,
        "suggestion": "No changes to commit. This is fine, continue with the next step.",
    },
    # Build/test errors
    r"SyntaxError|IndentationError|TabError": {
        "verdict": ReflectionVerdict.RETRY,
        "suggestion": "Syntax error in the code. Read the file and fix the syntax.",
    },
    r"ModuleNotFoundError|ImportError|Cannot find module": {
        "verdict": ReflectionVerdict.RETRY,
        "suggestion": "Missing module. Install the dependency or fix the import path.",
    },
    r"TypeError|AttributeError|NameError|ReferenceError": {
        "verdict": ReflectionVerdict.RETRY,
        "suggestion": "Runtime type/attribute error. Read the relevant code and fix the issue.",
    },
    r"FAILED|FAIL|AssertionError|assert.*failed": {
        "verdict": ReflectionVerdict.RETRY,
        "suggestion": "Test failure. Read the test output, understand what's expected, and fix the code.",
    },
    # Network errors
    r"ConnectionError|ConnectionRefused|ECONNREFUSED": {
        "verdict": ReflectionVerdict.RETRY,
        "suggestion": "Connection failed. Check if the server is running or try a different URL.",
    },
    r"HTTP [45]\d\d": {
        "verdict": ReflectionVerdict.RETRY,
        "suggestion": "HTTP error. Check the URL and request parameters.",
    },
}


class ReflectionEngine:
    """
    Self-verification engine that analyzes tool results after each execution.

    It evaluates:
    1. Did the tool succeed?
    2. Was the output what we expected?
    3. Do we need to try again or try differently?
    4. Should we rollback the change?
    5. Should we ask the user for help?

    Usage:
        reflector = ReflectionEngine()
        result = reflector.reflect(tool_name, tool_args, tool_output, context)

        if result.should_retry:
            # Try again with modified approach
        elif result.should_rollback:
            # Undo the last action
        elif result.should_escalate:
            # Ask the user
    """

    def __init__(self):
        self._retry_counters: dict[str, int] = {}  # tool_call_key -> retry count
        self._action_history: list[dict] = []  # Recent actions for pattern detection

    def reflect(
        self,
        tool_name: str,
        tool_args: dict,
        tool_output: str,
        plan_context: str = "",
    ) -> ReflectionResult:
        """
        Analyze a tool execution result and decide what to do next.

        Args:
            tool_name: Name of the tool that was called
            tool_args: Arguments passed to the tool
            tool_output: The output/result from the tool
            plan_context: Current plan context (if any)

        Returns:
            ReflectionResult with verdict and suggestions
        """
        # Track action history
        self._action_history.append(
            {
                "tool": tool_name,
                "args": tool_args,
                "output_preview": tool_output[:200],
                "success": not tool_output.startswith("❌"),
            }
        )

        # Keep history manageable
        if len(self._action_history) > 50:
            self._action_history = self._action_history[-30:]

        # Quick success check
        if not tool_output.startswith("❌") and not tool_output.startswith("⚠️"):
            return ReflectionResult(
                verdict=ReflectionVerdict.SUCCESS,
                reason="Tool executed successfully.",
                confidence=0.95,
            )

        # Check for known error patterns
        for pattern, action in _ERROR_PATTERNS.items():
            if re.search(pattern, tool_output, re.IGNORECASE):
                retry_key = f"{tool_name}:{pattern}"
                retry_count = self._retry_counters.get(retry_key, 0)

                if action["verdict"] == ReflectionVerdict.RETRY:
                    self._retry_counters[retry_key] = retry_count + 1

                return ReflectionResult(
                    verdict=action["verdict"],
                    reason=f"Matched error pattern: {pattern}",
                    suggestion=action["suggestion"],
                    retry_count=retry_count,
                    confidence=0.8,
                )

        # Check for repeated failures (same tool, same args)
        recent_failures = [
            a for a in self._action_history[-5:] if a["tool"] == tool_name and not a["success"]
        ]
        if len(recent_failures) >= 3:
            return ReflectionResult(
                verdict=ReflectionVerdict.ESCALATE,
                reason=f"Tool '{tool_name}' has failed {len(recent_failures)} times recently.",
                suggestion="The same operation keeps failing. Try a completely different approach or ask the user for help.",
                confidence=0.9,
            )

        # Generic error handling
        if tool_output.startswith("❌"):
            return ReflectionResult(
                verdict=ReflectionVerdict.RETRY,
                reason=f"Tool '{tool_name}' returned an error.",
                suggestion="Read the error message carefully and try a different approach.",
                confidence=0.6,
            )

        # Warning — continue but note it
        if tool_output.startswith("⚠️"):
            return ReflectionResult(
                verdict=ReflectionVerdict.CONTINUE,
                reason=f"Tool '{tool_name}' returned a warning.",
                suggestion="The operation partially succeeded. Check if the result is acceptable.",
                confidence=0.7,
            )

        return ReflectionResult(
            verdict=ReflectionVerdict.SUCCESS,
            reason="No issues detected.",
            confidence=0.9,
        )

    def reflect_on_plan_step(
        self,
        step_title: str,
        tool_results: list[dict],
    ) -> ReflectionResult:
        """
        Reflect on an entire plan step (which may involve multiple tool calls).

        Args:
            step_title: The title of the plan step
            tool_results: List of {tool, output, success} dicts

        Returns:
            ReflectionResult for the overall step
        """
        if not tool_results:
            return ReflectionResult(
                verdict=ReflectionVerdict.CONTINUE,
                reason="No tools were called for this step.",
                confidence=0.5,
            )

        failures = [r for r in tool_results if not r.get("success", True)]
        successes = [r for r in tool_results if r.get("success", True)]

        if not failures:
            return ReflectionResult(
                verdict=ReflectionVerdict.SUCCESS,
                reason=f"All {len(successes)} tool calls succeeded for '{step_title}'.",
                confidence=0.95,
            )

        if len(failures) == len(tool_results):
            return ReflectionResult(
                verdict=ReflectionVerdict.RETRY,
                reason=f"All {len(failures)} tool calls failed for '{step_title}'.",
                suggestion="Every tool call failed. Reassess the approach for this step.",
                confidence=0.9,
            )

        # Mixed results
        failure_rate = len(failures) / len(tool_results)
        if failure_rate > 0.5:
            return ReflectionResult(
                verdict=ReflectionVerdict.RETRY,
                reason=f"Most tool calls failed ({len(failures)}/{len(tool_results)}) for '{step_title}'.",
                suggestion="More failures than successes. Fix the failures before proceeding.",
                confidence=0.7,
            )

        return ReflectionResult(
            verdict=ReflectionVerdict.CONTINUE,
            reason=f"Some tool calls failed ({len(failures)}/{len(tool_results)}) for '{step_title}'.",
            suggestion="Minor failures. Review and fix if critical, otherwise continue.",
            confidence=0.6,
        )

    def get_reflection_context(self) -> str:
        """Generate context about recent actions for the agent."""
        if not self._action_history:
            return ""

        recent = self._action_history[-5:]
        failures = [a for a in recent if not a["success"]]

        if not failures:
            return ""

        context = "\n[REFLECTION — Recent Issues]\n"
        for f in failures:
            context += f"  ⚠ {f['tool']} failed: {f['output_preview'][:100]}\n"

        # Check for patterns
        repeated_tools = {}
        for a in self._action_history[-10:]:
            if not a["success"]:
                repeated_tools[a["tool"]] = repeated_tools.get(a["tool"], 0) + 1

        for tool, count in repeated_tools.items():
            if count >= 2:
                context += f"  🔄 {tool} has failed {count} times — consider a different approach\n"

        return context

    def reset(self):
        """Reset retry counters and action history."""
        self._retry_counters.clear()
        self._action_history.clear()
