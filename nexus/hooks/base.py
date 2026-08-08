"""
Hook Base — types, events, and base class for lifecycle hooks.

Security model:
  - Hooks return argv vectors (list[str]), never shell command strings
  - All paths are validated against the workspace root
  - Each hook declares a failure policy: BLOCK, WARN, or ROLLBACK
  - Hook commands run through the same sandbox as normal tools
"""

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class HookEvent(str, Enum):
    """Lifecycle events that can trigger hooks."""

    BEFORE_FILE_EDIT = "before_file_edit"
    AFTER_FILE_EDIT = "after_file_edit"
    BEFORE_FILE_CREATE = "before_file_create"
    AFTER_FILE_CREATE = "after_file_create"
    BEFORE_COMMAND = "before_command"
    AFTER_COMMAND = "after_command"
    BEFORE_COMMIT = "before_commit"
    AFTER_COMMIT = "after_commit"
    BEFORE_PUSH = "before_push"
    AFTER_PUSH = "after_push"
    ON_ERROR = "on_error"
    ON_TEST_FAIL = "on_test_fail"
    ON_LINT_FAIL = "on_lint_fail"
    ON_PLAN_START = "on_plan_start"
    ON_PLAN_COMPLETE = "on_plan_complete"
    ON_SESSION_START = "on_session_start"
    ON_SESSION_END = "on_session_end"
    ON_MODEL_SWITCH = "on_model_switch"
    ON_SKILL_ACTIVATE = "on_skill_activate"
    ON_SUBAGENT_COMPLETE = "on_subagent_complete"


class HookType(str, Enum):
    """Types of hook actions."""

    SHELL = "shell"  # Run a shell command (as argv, NOT a shell string)
    PROMPT = "prompt"  # Inject a prompt into the agent
    TOOL = "tool"  # Call a specific tool
    NOTIFY = "notify"  # Show a notification
    BLOCK = "block"  # Block the operation


class HookFailurePolicy(str, Enum):
    """What happens when a hook fails."""

    WARN = "warn"  # Log warning, continue operation
    BLOCK = "block"  # Block the triggering operation
    ROLLBACK = "rollback"  # Roll back the triggering operation


@dataclass
class HookContext:
    """Context passed to hooks when they fire."""

    event: HookEvent
    file_path: str = ""
    file_content: str = ""
    command: str = ""
    command_output: str = ""
    error_message: str = ""
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_result: str = ""
    metadata: dict = field(default_factory=dict)
    workspace_root: str = ""  # Workspace root for path validation


@dataclass
class HookResult:
    """Result of a hook execution."""

    hook_name: str
    event: HookEvent
    success: bool
    output: str = ""
    blocked: bool = False  # If True, the triggering operation should be cancelled
    modified_content: str = ""  # If non-empty, use this instead of original content
    failure_policy: HookFailurePolicy = HookFailurePolicy.WARN
    network_access: bool = False
    require_native_isolation: bool = True


def validate_hook_path(path: str, workspace_root: str) -> bool:
    """Validate that a path used in a hook command is within the workspace.

    Returns True if the path is safe to use.
    """
    if not workspace_root:
        return True  # No workspace root to validate against

    try:
        resolved = Path(path).resolve()
        ws_root = Path(workspace_root).resolve()
        resolved.relative_to(ws_root)
        return True
    except (ValueError, OSError):
        return False


class BaseHook:
    """
    Base class for hooks. Subclass to create custom hooks.

    SECURITY: ``get_command()`` must return a list[str] (argv vector),
    never a single command string.  Paths must be validated against
    the workspace root.

    Example::

        class AutoFormatHook(BaseHook):
            name = "auto_format"
            events = [HookEvent.AFTER_FILE_EDIT]
            hook_type = HookType.SHELL

            def get_command(self, context: HookContext) -> list[str]:
                if context.file_path.endswith(".py"):
                    return ["ruff", "format", context.file_path]
                return []
    """

    name: str = "base_hook"
    description: str = "Base hook"
    events: list[HookEvent] = []
    hook_type: HookType = HookType.SHELL
    enabled: bool = True
    priority: int = 50  # 0 = lowest, 100 = highest
    file_pattern: str = ""  # Glob pattern to filter by file (e.g., "*.py")
    failure_policy: HookFailurePolicy = HookFailurePolicy.WARN
    network_access: bool = False
    require_native_isolation: bool = True

    def should_fire(self, context: HookContext) -> bool:
        """Determine if this hook should fire for the given context."""
        if not self.enabled:
            return False

        if context.event not in self.events:
            return False

        # File pattern matching
        if self.file_pattern and context.file_path:
            import fnmatch

            if not fnmatch.fnmatch(context.file_path, self.file_pattern):
                return False

        return True

    def execute(self, context: HookContext) -> HookResult:
        """
        Execute this hook. Override in subclasses.

        Returns a HookResult indicating success/failure and any modifications.
        """
        return HookResult(
            hook_name=self.name,
            event=context.event,
            success=True,
            failure_policy=self.failure_policy,
        )

    def get_command(self, context: HookContext) -> list[str]:
        """For SHELL hooks, return the command as an argv vector.

        SECURITY: Never return a shell command string. Always return
        a list of arguments. Never interpolate paths into shell strings.
        """
        return []

    def get_prompt(self, context: HookContext) -> str:
        """For PROMPT hooks, return the prompt to inject. Override in subclasses."""
        return ""

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "events": [e.value for e in self.events],
            "type": self.hook_type.value,
            "enabled": self.enabled,
            "priority": self.priority,
            "file_pattern": self.file_pattern,
            "failure_policy": self.failure_policy.value,
            "network_access": self.network_access,
            "require_native_isolation": self.require_native_isolation,
        }
