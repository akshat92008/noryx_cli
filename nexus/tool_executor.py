"""
ToolExecutionController — extracted service for tool safety dispatch.

This module provides a typed interface that formalises the tool-execution
pipeline.  The Agent class delegates its ``_execute_tool_with_safety`` method
to this controller so the ~425-line implementation lives in one responsible
class rather than buried inside the monolithic Agent.

Usage::

    from nexus.tool_executor import ToolExecutionController
    # The Agent passes ``self`` as the host; the controller accesses agent
    # attributes through the declared protocol interface.
    ctrl = ToolExecutionController(agent)
    result, ok = ctrl.execute("write_file", {"path": "a.py", "content": "..."})

Architecture::

    ToolExecutionController
    ├── _check_capability()          reject unknown / blocked tools early
    ├── _apply_network_tag()         mark network-touching commands
    ├── _run_before_hooks()          pre-execution hook chain
    ├── _dispatch()                  route to execute_tool()
    ├── _record_evidence()           append to EvidenceTrail
    ├── _run_after_hooks()           post-execution hook chain
    └── _maybe_reflect()            optional ReflectionEngine call
"""

from __future__ import annotations

import json
import logging
import re
import shlex
import time
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Protocol

from nexus.approvals import preview_mutation
from nexus.capabilities import ToolCapability
from nexus.code_validation import GeneratedCodeValidator
from nexus.evidence import command_exit_code, verify_mutation
from nexus.extensions import ToolContext
from nexus.hooks.base import HookContext, HookEvent
from nexus.planner import TaskStatus
from nexus.policy import PermissionDecision
from nexus.recovery.controller import RecoveryController
from nexus.safety import SafetyCheck, SafetyLevel
from nexus.security.command_policy import CommandPolicy, CommandRisk
from nexus.security.policy_engine import PolicyEngine
from nexus.tools import ToolResult, ToolStatus, execute_tool
from nexus.verification_evidence import analyse_test_command, validate_test_execution
from nexus.workspace_journal import (
    ContentAddressedWorkspaceJournal,
    WorkspaceMutation,
    WorkspaceSnapshot,
    WorkspaceSnapshotError,
)

if TYPE_CHECKING:
    from nexus.agent import Agent

logger = logging.getLogger(__name__)


class AgentProtocol(Protocol):
    """Minimal interface the controller needs from the Agent."""

    working_dir: str
    mode_policy: Any
    evidence: Any
    hooks: Any
    reflection: Any
    _tool_capabilities: dict[str, Any]
    _pending_confirmations: dict[str, Any]

    def _queue_confirmation(
        self,
        *,
        name: str,
        args: dict,
        safety_check: Any,
        edit_confirmed: bool,
    ) -> str: ...

    def execute_tool_raw(self, name: str, args: dict) -> tuple[str, bool]: ...


class ToolExecutionController:
    """
    Typed service that owns the tool safety → dispatch → evidence pipeline.

    This is a *delegation target* for ``Agent._execute_tool_with_safety``.
    It does not replace the agent but reduces the agent's responsibility
    surface by centralising tool-lifecycle logic here.

    Thread safety: each agent call is single-threaded; no locking needed here.
    """

    # Commands that require network access — auto-tagged by the controller.
    _NETWORK_COMMAND_PATTERN = re.compile(
        r"\b(?:curl|wget|ssh|scp|sftp|ftp|rsync|gh)\b"
        r"|\bgit\s+(?:clone|fetch|pull|push)\b"
        r"|\b(?:pip|pip3|uv)\s+(?:pip\s+)?install\b"
        r"|\b(?:npm|pnpm|yarn)\s+(?:add|install|publish)\b"
        r"|\b(?:docker|podman)\s+(?:pull|push)\b"
        r"|\bcargo\s+(?:add|install)\b|\bgo\s+get\b",
        re.IGNORECASE,
    )

    # Tools that read files or the repo index (no mutation).
    _READ_TOOLS: frozenset[str] = frozenset(
        {
            "read_file",
            "file_info",
            "diff_files",
            "search_code",
            "list_directory",
            "find_files",
            "get_project_structure",
            "repo_index",
            "repo_symbols",
            "repo_impact",
            "repo_context",
            "repo_routes",
            "repo_models",
            "repo_navigate",
            "database_check",
            "security_scan",
        }
    )

    # Tools that mutate files on disk.
    _MUTATION_TOOLS: tuple[str, ...] = ("write_file", "edit_file", "patch_file", "multi_edit")

    def __init__(self, agent: "Agent") -> None:
        self._agent = agent
        self._policy_engine: PolicyEngine | None = PolicyEngine()
        shared_recovery = getattr(agent, "recovery_controller", None)
        self._recovery_controller: RecoveryController = shared_recovery or RecoveryController(
            run_id=getattr(agent, "conversation_id", None),
            working_dir=getattr(agent, "working_dir", "."),
            budget=getattr(agent, "budget", None),
        )
        self._command_workspace_journal: ContentAddressedWorkspaceJournal | None = None

    def _history_count(self) -> int:
        """Return a compatible history length for real and protocol-test agents."""
        history = getattr(self._agent, "history", None)
        changes = getattr(history, "changes", history if isinstance(history, list) else ())
        try:
            return len(changes)
        except TypeError:
            return 0

    @staticmethod
    def _estimated_mutation_lines(name: str, args: dict[str, Any]) -> int:
        """Conservative changed-line estimate used before disk mutation."""
        if name == "write_file":
            return max(1, len(str(args.get("content", "")).splitlines()))
        if name == "edit_file":
            return max(
                1,
                len(str(args.get("old_text", "")).splitlines()),
                len(str(args.get("new_text", "")).splitlines()),
            )
        if name == "patch_file":
            old_span = max(
                0, int(args.get("end_line", 0) or 0) - int(args.get("start_line", 0) or 0) + 1
            )
            return max(1, old_span, len(str(args.get("new_content", "")).splitlines()))
        if name == "multi_edit":
            return sum(
                max(
                    1,
                    len(str(item.get("old_text", "")).splitlines()),
                    len(str(item.get("new_text", "")).splitlines()),
                )
                for item in args.get("edits", [])
                if isinstance(item, dict)
            )
        return 0

    # ──────────────────────────────────────────────────────────────────────────
    # Public interface
    # ──────────────────────────────────────────────────────────────────────────

    def _pending_confirmation_sentinel(
        self,
        confirmation_id: str,
        action_type: str,
        display_text: str,
    ) -> str:
        """Return a structured sentinel string that kernel.py detects to halt the loop.

        Format::

            __HUMAN_ACTION_REQUIRED__:<action_type>:<confirmation_id>\n<display_text>

        The kernel detects the ``__HUMAN_ACTION_REQUIRED__:`` prefix and emits a
        ``RunAwaitingConfirmation`` event, halting the model loop immediately
        without feeding the message back to the LLM as a tool failure.
        """
        return (
            f"__HUMAN_ACTION_REQUIRED__:{action_type}:{confirmation_id}\n"
            f"{display_text}"
        )

    def execute(
        self,
        name: str,
        args: dict,
        *,
        _user_initiated: bool = False,
        _user_confirmed: bool = False,
        _edit_confirmed: bool = False,
    ) -> tuple[str, bool]:
        """Execute a guarded tool and mirror its outcome into the run ledger."""
        started = time.monotonic()
        history_start = self._history_count()
        from nexus.run_context import run_context_scope
        from nexus.tools import tool_context

        with (
            run_context_scope(self._agent.run_context),
            tool_context(self._agent.working_dir, self._agent.history, self._agent.conversation_id),
        ):
            result, success = self._execute_impl(
                name,
                args,
                _user_initiated=_user_initiated,
                _user_confirmed=_user_confirmed,
                _edit_confirmed=_edit_confirmed,
            )
        if _user_confirmed:
            self._agent._permissions_used.add(f"{name}: explicit approval")
        if success and name in {"web_fetch", "web_search", "api_check", "browser_check"}:
            target = str(args.get("url") or args.get("query") or "")
            self._agent._network_calls.append(f"{name}: {target}")
        elif success and args.get("network"):
            target = str(args.get("command") or args.get("argv") or name)
            self._agent._network_calls.append(f"{name}: {target}")
        run_ledger = getattr(self._agent, "run_ledger", None)
        if run_ledger and run_ledger.turn_dir:
            from nexus.agent import _redact_runtime_text

            safe_args = {
                key: _redact_runtime_text(value) if isinstance(value, str) else value
                for key, value in args.items()
                if key
                not in {
                    "content",
                    "old_text",
                    "new_text",
                    "new_content",
                    "_nova_guardrail",
                }
            }
            self._agent.run_ledger.append_event(
                "tool_call",
                status="verified" if success else "failed",
                detail=(
                    f"{name} completed successfully."
                    if success
                    else _redact_runtime_text((result or "")[:1000])
                ),
                metadata={
                    "tool": name,
                    "arguments": safe_args,
                    "duration_ms": int((time.monotonic() - started) * 1000),
                },
            )
            evidence_records = self._agent.evidence.records(limit=1)
            self._agent.run_ledger.append_tool_call(
                tool=name,
                status="verified" if success else "failed",
                arguments=safe_args,
                evidence_id=(evidence_records[-1].get("id", "") if evidence_records else ""),
                duration_ms=int((time.monotonic() - started) * 1000),
            )
            self._agent.run_ledger.record_costs(self._agent.budget.snapshot())
            mutation_tools = {"write_file", "edit_file", "patch_file", "multi_edit"}
            if success and name in mutation_tools:
                raw_paths = (
                    [item.get("path", "") for item in args.get("edits", [])]
                    if name == "multi_edit"
                    else [args.get("path", "")]
                )
                try:
                    self._agent.repo_graph.update_paths(path for path in raw_paths if path)
                except (OSError, ValueError) as exc:
                    logger.debug("Repository graph incremental refresh failed: %s", exc)
                brain = getattr(self._agent, "engineering_brain", None)
                if brain is not None:
                    mutation_paths = [path for path in raw_paths if path]
                    estimated_lines = self._estimated_mutation_lines(name, args)
                    try:
                        brain.record_changes(
                            [
                                (path, f"verified mutation via {name}", estimated_lines)
                                for path in mutation_paths
                            ]
                        )
                    except Exception as exc:
                        self._agent.evidence.append(
                            kind="engineering_state_integrity",
                            claim="Persist verified mutation in HMAC-authenticated task memory",
                            status="failed",
                            raw_output=str(exc),
                            metadata={
                                "paths": [str(path) for path in mutation_paths],
                                "tool": name,
                                "error_type": type(exc).__name__,
                            },
                        )
                        return self._rollback_post_mutation_failure(
                            history_start=history_start,
                            reason=f"Engineering task state could not be persisted: {exc}",
                            tool=name,
                        )
                    guard = getattr(brain, "scope_guard", None)
                    if guard is not None:
                        guard.register_change(mutation_paths, lines_changed=estimated_lines)
                self._agent.run_ledger.checkpoint(
                    f"verified-{name}",
                    plan=self._agent._active_plan,
                    evidence_count=len(self._agent.evidence.records()),
                    history_count=len(self._agent.history.changes),
                    metadata={"paths": [path for path in raw_paths if path]},
                )
            elif success and name in ("run_command", "run_process"):
                self._agent.run_ledger.checkpoint(
                    "command-completed",
                    plan=self._agent._active_plan,
                    evidence_count=len(self._agent.evidence.records()),
                    history_count=len(self._agent.history.changes),
                    metadata={"command": _redact_runtime_text(str(args.get("command", ""))[:500])},
                )
        return result, success

    def _enforce_tool_policy(
        self,
        name: str,
        args: dict,
        command: str,
        scope_paths: list[str],
        pending_args: dict,
        _user_initiated: bool,
        _user_confirmed: bool,
        _edit_confirmed: bool,
        mutation_tools: tuple,
        read_tools: set,
        command_risk: CommandRisk = CommandRisk.UNKNOWN,
    ) -> tuple[bool, str, tuple[str, bool]]:
        if name in self._agent.disallowed_tools:
            return (
                False,
                "",
                (f"❌ BLOCKED: {name} is denied by the active permission rules.", False),
            )
        if self._agent.allowed_tools and name not in self._agent.allowed_tools:
            return False, "", (f"❌ BLOCKED: {name} is not in the active tool allowlist.", False)
        if self._agent._active_plan is not None and self._agent._enforce_plan_tool_contract:
            current = next(
                (
                    step
                    for step in self._agent._active_plan.steps
                    if step.status == TaskStatus.IN_PROGRESS
                ),
                None,
            )
            if current is not None:
                step_tools = set(current.tools_needed) | {
                    "read_file",
                    "search_code",
                    "list_directory",
                    "find_files",
                    "get_project_structure",
                    "repo_context",
                    "repo_symbols",
                    "repo_impact",
                }
                if name not in step_tools:
                    return (
                        False,
                        "",
                        (
                            f"❌ BLOCKED: {name} is outside the active plan step's tool contract.",
                            False,
                        ),
                    )
        if (
            not self._agent.mode_policy.may_edit
            and not _user_initiated
            and (
                name in mutation_tools
                or name in ("run_command", "run_process", "process_run")
                or name.startswith("git_")
            )
        ):
            return (
                False,
                "",
                (
                    "❌ BLOCKED: Current mode is read-only. Switch mode before executing changes.",
                    False,
                ),
            )

        policy_capability = ""
        policy_targets: list[str] = []
        normalized_command = command.lower()
        if name in mutation_tools:
            policy_capability = "write"
            policy_targets = scope_paths
        elif name in ("run_command", "run_process", "process_run"):
            if command_risk == CommandRisk.NETWORK_REQUEST and re.search(
                r"(?:^|[/\\])git\s+push\b", normalized_command
            ):
                policy_capability = "git_push"
            elif command_risk == CommandRisk.PACKAGE_INSTALL:
                policy_capability = "package_install"
            elif re.search(
                r"\b(?:kubectl\s+(?:apply|delete)|helm\s+(?:install|upgrade)|"
                r"terraform\s+apply|vercel\s+deploy)\b",
                normalized_command,
            ):
                policy_capability = "deployment"
            else:
                policy_capability = "command"
            policy_targets = [command]
        elif name.startswith("git_"):
            policy_capability = "command"
            policy_targets = [name]
        elif name in read_tools:
            policy_capability = "read"
            policy_targets = scope_paths or [name]
        elif name in ("web_fetch", "web_search", "api_check", "browser_check"):
            policy_capability = "network_access"
            policy_targets = [str(args.get("url") or args.get("query") or "")]

        if policy_capability:
            approval_targets = []
            for policy_target in policy_targets or [name]:
                policy_decision = self._agent.policy.decide(
                    policy_capability,
                    policy_target,
                )
                extension_asked = False
                for provider in self._agent.extensions.loaded("policies"):
                    external = str(
                        provider.decide(
                            policy_capability,
                            policy_target,
                            ToolContext(
                                working_dir=self._agent.working_dir,
                                session_id=self._agent.conversation_id,
                                permission_mode=self._agent.permission_mode,
                            ),
                        )
                    ).lower()
                    if external == PermissionDecision.DENY.value:
                        policy_decision = PermissionDecision.DENY
                        break
                    if external == PermissionDecision.ASK.value:
                        policy_decision = PermissionDecision.ASK
                        extension_asked = True
                if policy_decision == PermissionDecision.DENY:
                    return (
                        False,
                        "",
                        (
                            f"❌ BLOCKED: repository policy denies {policy_capability} "
                            f"for {policy_target or name}.",
                            False,
                        ),
                    )
                if policy_decision == PermissionDecision.ASK and (
                    self._agent.policy.source
                    or extension_asked
                    or (
                        self._agent.mode_policy.require_review
                        and policy_capability
                        in {"command", "package_install", "deployment", "git_push"}
                    )
                ):
                    approval_targets.append(policy_target or name)
            policy_requires_approval = approval_targets and not (_user_confirmed or _user_initiated)
            if policy_requires_approval:
                policy_target = ", ".join(approval_targets)
                policy_check = SafetyCheck(
                    level=SafetyLevel.DANGEROUS,
                    operation=f"{policy_capability}: {policy_target or name}",
                    reason=f"Repository policy requires approval for {policy_capability}",
                    details=policy_target or name,
                    requires_confirmation=True,
                )
                confirmation_id = self._agent._queue_confirmation(
                    name=name,
                    args=pending_args,
                    safety_check=policy_check,
                    edit_confirmed=_edit_confirmed,
                )
                display_text = (
                    f"⏸️ PENDING_CONFIRMATION [{confirmation_id}]: {policy_check.reason}.\n"
                    "This operation was not executed.\n"
                    f"Enter /confirm {confirmation_id} or /cancel {confirmation_id}."
                )
                return (
                    False,
                    "",
                    (
                        self._pending_confirmation_sentinel(
                            confirmation_id, "COMMAND_CONFIRMATION", display_text
                        ),
                        False,
                    ),
                )
        return True, policy_capability, ("", False)

    def _enforce_network_safety(
        self,
        name: str,
        args: dict,
        command: str,
        pending_args: dict,
        _user_initiated: bool,
        _user_confirmed: bool,
        _edit_confirmed: bool,
    ) -> tuple[bool, tuple[str, bool]]:
        requests_network = bool(args.get("network")) or bool(args.get("allow_external"))
        if requests_network and not (_user_confirmed or _user_initiated):
            network_check = SafetyCheck(
                level=SafetyLevel.DANGEROUS,
                operation=f"{name} network access",
                reason="Network access is disabled by default",
                details=command or str(args.get("url", "")),
                requires_confirmation=True,
            )
            confirmation_id = self._agent._queue_confirmation(
                name=name,
                args=pending_args,
                safety_check=network_check,
                edit_confirmed=_edit_confirmed,
            )
            display_text = (
                f"⏸️ PENDING_CONFIRMATION [{confirmation_id}]: {network_check.reason}.\n"
                f"Enter /confirm {confirmation_id} or /cancel {confirmation_id}."
            )
            return False, (
                self._pending_confirmation_sentinel(
                    confirmation_id, "NETWORK_CONFIRMATION", display_text
                ),
                False,
            )
        return True, ("", False)

    def _enforce_package_safety(
        self,
        name: str,
        args: dict,
        command: str,
        pending_args: dict,
        _user_confirmed: bool,
        _edit_confirmed: bool,
        mutation_tools: tuple,
    ) -> tuple[bool, str, tuple[str, bool]]:
        package_checks = []
        package_warning_text = ""
        if name in mutation_tools:
            for package_path, proposed_content in self._agent._dependency_candidates(name, args):
                try:
                    current_content = Path(package_path).read_text(encoding="utf-8")
                except OSError:
                    current_content = ""
                package_checks.extend(
                    self._agent.package_guard.check_file_change(
                        package_path,
                        proposed_content,
                        current_content=current_content,
                    )
                )
        elif name in ("run_command", "run_process", "process_run") and command:
            package_checks = self._agent.package_guard.check_command(command)
        if package_checks:
            for check in package_checks:
                self._agent.evidence.append(
                    kind="package_registry",
                    claim=f"registry check for {check.registry}:{check.name}",
                    status=check.status,
                    tool=name,
                    raw_output=check.reason,
                    metadata={
                        "registry": check.registry,
                        "name": check.name,
                        "registry_url": check.url,
                    },
                )
            blocked = [check for check in package_checks if check.blocked]
            if blocked:
                details = "\n".join(
                    f"  {check.registry}:{check.name} — {check.reason}" for check in blocked
                )
                return False, "", (f"❌ BLOCKED by anti-slopsquatting guard:\n{details}", False)
            unverified = [check for check in package_checks if check.requires_confirmation]
            if unverified and not _user_confirmed:
                details = "\n".join(
                    f"  {check.registry}:{check.name} — {check.reason}" for check in unverified
                )
                uncertainty_check = SafetyCheck(
                    level=SafetyLevel.DANGEROUS,
                    operation=f"{name} with unverified package metadata",
                    reason=(
                        "The package registry could not be verified. This is not "
                        "treated as proof of a malicious package, but continuing "
                        "requires explicit approval"
                    ),
                    details=details,
                    requires_confirmation=True,
                )
                confirmation_id = self._agent._queue_confirmation(
                    name=name,
                    args=pending_args,
                    safety_check=uncertainty_check,
                    edit_confirmed=_edit_confirmed,
                )
                display_text = (
                    f"⏸️ PENDING_CONFIRMATION [{confirmation_id}]: {uncertainty_check.reason}.\n"
                    "This operation was not executed. Review the exact operation, then\n"
                    f"enter /confirm {confirmation_id} or /cancel {confirmation_id}.\n"
                    f"{details}"
                )
                return (
                    False,
                    "",
                    (
                        self._pending_confirmation_sentinel(
                            confirmation_id, "PACKAGE_CONFIRMATION", display_text
                        ),
                        False,
                    ),
                )
            warnings = [check for check in package_checks if check.status == "warn"]
            if warnings:
                package_warning_text = "⚠️ PACKAGE RISK WARNING:\n" + "\n".join(
                    f"  {check.registry}:{check.name} — {check.reason}" for check in warnings
                )
        return True, package_warning_text, ("", False)

    def _prepare_mutation_diff(
        self,
        name: str,
        args: dict,
        pending_args: dict,
        _user_confirmed: bool,
        _edit_confirmed: bool,
        mutation_tools: tuple,
    ) -> tuple[bool, str, tuple[str, bool]]:
        mutation_diff = ""
        if name in mutation_tools:
            ok, mutation_diff = preview_mutation(name, args, self._agent.working_dir)
            if not ok:
                return False, "", (f"❌ Cannot create a safe diff preview: {mutation_diff}", False)
            if self._agent.mode_policy.require_review and not _edit_confirmed:
                confirmation_id = self._agent._queue_edit(name, pending_args, mutation_diff)
                # Fix #2: Return a structured sentinel that signals AWAITING_APPROVAL.
                # The kernel detects the "__AWAITING_APPROVAL__:" prefix and emits a
                # RunAwaitingApproval event instead of treating this as a failure.
                approval_sentinel = (
                    f"__AWAITING_APPROVAL__:{confirmation_id}\n"
                    f"⏸️ The file edit has been queued for review.\n"
                    f"Enter `/apply {confirmation_id}` or `/reject {confirmation_id}`.\n"
                    f"Diff preview:\n```diff\n{mutation_diff}\n```"
                )
                return (
                    False,
                    "",
                    (approval_sentinel, False),
                )
        return True, mutation_diff, ("", False)

    @staticmethod
    def _external_result(result: Any, *, source: str) -> ToolResult:
        """Normalize extension results into the authoritative structured contract."""
        if isinstance(result, ToolResult):
            return result
        if isinstance(result, dict):
            supplied_status = result.get("status")
            raw_status = str(supplied_status or "").strip().lower()
            raw_status = raw_status.removeprefix("toolstatus.")
            is_error = bool(result.get("isError") or result.get("is_error"))
            aliases = {
                "ok": ToolStatus.SUCCESS,
                "passed": ToolStatus.SUCCESS,
                "failed": ToolStatus.FAILURE,
                "error": ToolStatus.FAILURE,
                "denied": ToolStatus.PERMISSION_DENIED,
            }
            if isinstance(supplied_status, ToolStatus):
                status = supplied_status
            elif raw_status in {item.value for item in ToolStatus}:
                status = ToolStatus(raw_status)
            elif raw_status in aliases:
                status = aliases[raw_status]
            elif supplied_status not in (None, ""):
                status = ToolStatus.FAILURE
                is_error = True
            else:
                status = ToolStatus.FAILURE if is_error else ToolStatus.SUCCESS
            output_value = result.get("output", result.get("content", result))
            if isinstance(output_value, (dict, list)):
                output = json.dumps(output_value, ensure_ascii=False, default=str)
            else:
                output = str(output_value)
            default_error = (
                f"Invalid {source} status: {supplied_status}"
                if supplied_status not in (None, "")
                and status == ToolStatus.FAILURE
                and raw_status not in {item.value for item in ToolStatus}
                and raw_status not in aliases
                else (f"{source} error" if is_error else "")
            )
            return ToolResult(
                status=status,
                output=output,
                error=str(result.get("error") or default_error),
            )
        return ToolResult(status=ToolStatus.SUCCESS, output=str(result))

    @staticmethod
    def _mcp_output(response: dict[str, Any]) -> str:
        content = response.get("content", [])
        if not isinstance(content, list):
            return str(content)
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict):
                if "text" in item:
                    chunks.append(str(item["text"]))
                else:
                    chunks.append(json.dumps(item, ensure_ascii=False, default=str))
            else:
                chunks.append(str(item))
        return "\n".join(chunks)

    def _dispatch_tool_execution(
        self,
        name: str,
        args: dict,
    ) -> ToolResult:
        """Dispatch one tool and always return a structured, truthful result."""
        for plugin in self._agent.plugin_loader.plugins.values():
            dispatch = plugin.get_tool_dispatch()
            if name not in dispatch:
                continue
            try:
                return self._external_result(dispatch[name](**args), source="plugin")
            except Exception as exc:
                logger.exception("Plugin tool %s failed", name)
                return ToolResult(
                    status=ToolStatus.FAILURE,
                    output=f"Plugin tool failed: {exc}",
                    error=str(exc),
                )

        for extension_tool in self._agent.extensions.loaded("tools"):
            if extension_tool.name != name:
                continue
            try:
                extension_result = extension_tool.invoke(
                    args,
                    ToolContext(
                        working_dir=self._agent.working_dir,
                        session_id=self._agent.conversation_id,
                        task_id=(
                            str(self._agent._active_plan.current_step)
                            if self._agent._active_plan is not None
                            else ""
                        ),
                        permission_mode=self._agent.permission_mode,
                    ),
                )
                return self._external_result(extension_result, source="extension")
            except Exception as exc:
                logger.exception("Extension tool %s failed", name)
                return ToolResult(
                    status=ToolStatus.FAILURE,
                    output=f"Extension tool failed: {exc}",
                    error=str(exc),
                )

        if self._agent.mcp.is_mcp_tool(name):
            try:
                response = self._agent.mcp.call_tool(name, args)
            except Exception as exc:
                logger.exception("MCP tool %s failed", name)
                return ToolResult(
                    status=ToolStatus.FAILURE,
                    output=f"MCP tool failed: {exc}",
                    error=str(exc),
                )
            if isinstance(response, dict) and "content" in response:
                is_error = bool(response.get("isError") or response.get("is_error"))
                return ToolResult(
                    status=ToolStatus.FAILURE if is_error else ToolStatus.SUCCESS,
                    output=self._mcp_output(response),
                    error=str(response.get("error") or ("MCP error" if is_error else "")),
                )
            return self._external_result(response, source="mcp")

        tool_result = execute_tool(name, args, policy_engine=self._policy_engine)
        if tool_result.status in {ToolStatus.FAILURE, ToolStatus.BLOCKED} and tool_result.error:
            raw_failure = {
                "tool": name,
                "error": tool_result.error,
                "output": tool_result.output,
                "args": args,
            }
            strategy, _diag, terminal = self._recovery_controller.handle_failure(
                raw_failure,
                source_component="tool_executor",
                phase="tool_dispatch",
            )
            logger.debug(
                "RecoveryController outcome for %s: strategy=%s terminal=%s",
                name,
                getattr(strategy, "name", strategy),
                terminal,
            )
        return tool_result

    def _enforce_engineering_scope(
        self,
        name: str,
        args: dict[str, Any],
        scope_paths: list[str],
        mutation_tools: tuple[str, ...],
    ) -> tuple[bool, tuple[str, bool] | None]:
        """Enforce the repository-aware mutation boundary below the model."""
        brain = getattr(self._agent, "engineering_brain", None)
        if name not in mutation_tools or brain is None:
            return True, None
        scope_reason = str(args.get("scope_reason") or args.get("reason") or "")
        raw_scope_evidence = args.get("scope_evidence") or []
        if isinstance(raw_scope_evidence, dict):
            raw_scope_evidence = [raw_scope_evidence]
        if not isinstance(raw_scope_evidence, list):
            raw_scope_evidence = []
        decision = brain.authorize_mutation(
            scope_paths,
            expansion_evidence=raw_scope_evidence,
            reason=scope_reason,
        )
        if not decision.allowed:
            return False, (
                f"❌ BLOCKED: Engineering scope contract: {decision.reason}",
                False,
            )
        guard = getattr(brain, "scope_guard", None)
        estimated_lines = self._estimated_mutation_lines(name, args)
        projected_lines = (guard.changed_lines + estimated_lines) if guard is not None else 0
        if guard is not None and projected_lines > guard.contract.max_changed_lines:
            return False, (
                f"❌ BLOCKED: Engineering scope contract: projected edit volume "
                f"{projected_lines} exceeds the {guard.contract.max_changed_lines}-line budget.",
                False,
            )
        if decision.requires_scope_expansion:
            self._agent.evidence.append(
                kind="scope_expansion",
                claim="Engineering Brain approved a bounded scope expansion",
                status="verified",
                raw_output=decision.reason,
                metadata=decision.to_dict(),
            )
        return True, None

    def _current_workspace_revision(self) -> str:
        """Return the content-addressed source revision, excluding Noryx state."""
        try:
            from nexus.intelligence.repository.snapshot import workspace_revision

            return workspace_revision(self._agent.working_dir)
        except (OSError, ValueError):
            return ""

    @staticmethod
    def _evidence_metadata(check_type: str, revision: str, **extra: Any) -> dict[str, Any]:
        """Build provenance metadata shared by deterministic evidence records."""
        return {
            "check_type": check_type,
            "workspace_revision": revision,
            "producer_type": "deterministic_tool",
            "independently_validated": True,
            **extra,
        }

    def _workspace_journal(self) -> ContentAddressedWorkspaceJournal:
        history = getattr(self._agent, "history", None)
        session_dir = getattr(history, "session_dir", None)
        if session_dir is None:
            from nexus.paths import nexus_home

            session_dir = nexus_home() / "history" / str(self._agent.conversation_id)
        if self._command_workspace_journal is None:
            from nexus.paths import nexus_home

            self._command_workspace_journal = ContentAddressedWorkspaceJournal(
                self._agent.working_dir,
                preimage_dir=Path(session_dir) / "command-preimages",
                excluded_roots=(nexus_home(),),
            )
        return self._command_workspace_journal

    def _snapshot_workspace(self, *, store_preimages: bool = False) -> WorkspaceSnapshot:
        """Capture a complete content-addressed workspace snapshot."""
        return self._workspace_journal().capture(store_preimages=store_preimages)

    @staticmethod
    def _reconcile_workspace(
        before_snapshot: WorkspaceSnapshot,
        after_snapshot: WorkspaceSnapshot,
    ) -> list[WorkspaceMutation]:
        """Detect creations, modifications, deletions and mode changes by digest."""
        return ContentAddressedWorkspaceJournal.diff(before_snapshot, after_snapshot)

    def _record_command_mutations(
        self,
        *,
        name: str,
        before_snapshot: WorkspaceSnapshot,
        after_snapshot: WorkspaceSnapshot,
        transaction_id: str,
        append_evidence: bool = True,
    ) -> list[WorkspaceMutation]:
        """Persist one command transaction without ambiguous partial evidence."""
        mutations = self._reconcile_workspace(before_snapshot, after_snapshot)
        root = Path(before_snapshot.root)
        history_batch: list[dict[str, Any]] = []
        for mutation in mutations:
            old = mutation.before
            new = mutation.after
            absolute = root / mutation.relative_path
            history_batch.append(
                {
                    "filepath": str(absolute),
                    "tool_name": name,
                    "snapshot_path": (old.preimage_path if old and old.kind == "file" else None),
                    "description": f"{mutation.change_type} implicitly by {name}",
                    "is_new_file": old is None,
                    "change_type": mutation.change_type,
                    "before_sha256": old.sha256 if old else "",
                    "after_sha256": new.sha256 if new else "",
                    "before_mode": old.mode if old else None,
                    "after_mode": new.mode if new else None,
                    "before_kind": old.kind if old else "file",
                    "before_link_target": old.link_target if old else "",
                    "transaction_id": transaction_id,
                }
            )
        batch_recorder = getattr(self._agent.history, "record_changes_batch", None)
        if callable(batch_recorder):
            batch_recorder(history_batch)
        else:
            for change in history_batch:
                self._agent.history.record_change(**change)

        if append_evidence and mutations:
            self._agent.evidence.append(
                kind="file_mutation",
                claim=(
                    f"content-addressed command journal detected {len(mutations)} "
                    f"workspace mutation(s)"
                ),
                status="verified",
                tool=name,
                artifacts=[
                    {
                        "path": str(root / item.relative_path),
                        "exists": item.after is not None,
                        "sha256": item.after.sha256 if item.after else None,
                        "size": item.after.size if item.after else 0,
                        "kind": (
                            item.after.kind
                            if item.after is not None
                            else (item.before.kind if item.before else "file")
                        ),
                        "link_target": item.after.link_target if item.after else "",
                    }
                    for item in mutations
                ],
                raw_output="",
                metadata={
                    "transaction_id": transaction_id,
                    "independently_validated": True,
                    "check_type": "content_addressed_mutation_transaction",
                    "mutations": [
                        {
                            "path": item.relative_path,
                            "change_type": item.change_type,
                            "before_sha256": item.before.sha256 if item.before else "",
                            "after_sha256": item.after.sha256 if item.after else "",
                        }
                        for item in mutations
                    ],
                },
            )
        return mutations

    def _prepare_multi_edit_change_set(self, args: dict[str, Any]):
        """Create and persist the multi-file transaction before disk mutation."""
        from nexus.multifile.contracts import (
            ChangeType,
            EngineeringChangeSet,
            PlannedFileChange,
            TaskType,
        )
        from nexus.multifile.persistence import ChangeSetPersistence

        unique_paths = list(
            dict.fromkeys(
                str(edit.get("path", ""))
                for edit in args.get("edits", [])
                if isinstance(edit, dict) and edit.get("path")
            )
        )
        file_changes = [
            PlannedFileChange(
                path=path,
                change_type=ChangeType.MODIFY,
                reason="Atomic multi_edit transaction via canonical tool boundary",
            )
            for path in unique_paths
        ]
        active_plan = getattr(self._agent, "_active_plan", None)
        objective = str(
            getattr(active_plan, "goal", "")
            or getattr(active_plan, "objective", "")
            or f"multi_edit: {len(unique_paths)} file(s)"
        )
        change_set = EngineeringChangeSet(
            run_id=str(getattr(getattr(self._agent, "run_context", None), "run_id", "") or ""),
            plan_id=str(getattr(active_plan, "id", "") or ""),
            plan_version=int(getattr(active_plan, "version", 1) or 1),
            repository_snapshot_id=self._current_workspace_revision(),
            objective=objective,
            task_type=TaskType.FEATURE,
            file_changes=file_changes,
        )
        persistence = None
        turn_dir = getattr(getattr(self._agent, "run_ledger", None), "turn_dir", None)
        if turn_dir is not None:
            persistence = ChangeSetPersistence(Path(turn_dir) / "change-set")
            persistence.save_change_set(change_set)
        return change_set, persistence

    def _begin_command_transaction(
        self, name: str
    ) -> tuple[WorkspaceSnapshot | None, str, tuple[str, bool] | None]:
        """Create the fail-closed pre-command journal for synchronous processes."""
        if name not in {"run_command", "run_process"}:
            return None, "", None
        transaction_id = f"cmd-{uuid.uuid4().hex}"
        try:
            snapshot = self._snapshot_workspace(store_preimages=True)
        except WorkspaceSnapshotError as exc:
            return (
                None,
                "",
                (
                    f"❌ BLOCKED: Cannot establish command mutation journal: {exc}",
                    False,
                ),
            )
        return snapshot, transaction_id, None

    def _finish_command_transaction(
        self,
        *,
        name: str,
        before_snapshot: WorkspaceSnapshot | None,
        transaction_id: str,
        success: bool,
        result: ToolResult,
    ) -> tuple[list[WorkspaceMutation], tuple[str, bool] | None]:
        """Reconcile and, on command failure, rollback all partial mutations."""
        if name not in {"run_command", "run_process"} or before_snapshot is None:
            return [], None
        try:
            after_snapshot = self._snapshot_workspace(store_preimages=False)
            mutations = self._record_command_mutations(
                name=name,
                before_snapshot=before_snapshot,
                after_snapshot=after_snapshot,
                transaction_id=transaction_id,
                append_evidence=success,
            )
        except Exception as exc:
            try:
                self._workspace_journal().restore(before_snapshot)
                removed = self._agent.history.discard_transaction(transaction_id)
                rollback_note = (
                    "workspace restored to pre-command snapshot; "
                    f"cleared {removed} partial history record(s)"
                )
            except Exception as rollback_exc:
                rollback_note = f"ROLLBACK FAILED: {rollback_exc}"
            try:
                self._agent.evidence.append(
                    kind="post_mutation_integrity",
                    claim="Recover from command reconciliation or evidence failure",
                    status=(
                        "verified" if not rollback_note.startswith("ROLLBACK FAILED") else "failed"
                    ),
                    tool=name,
                    raw_output=f"{type(exc).__name__}: {exc}; {rollback_note}",
                    metadata={"transaction_id": transaction_id},
                )
            except Exception:
                logger.exception("Unable to persist command integrity failure evidence")
            return [], (
                f"❌ POST-COMMAND INTEGRITY FAILURE: {exc}; {rollback_note}",
                False,
            )

        if not mutations or success:
            return mutations, None
        try:
            self._workspace_journal().restore(before_snapshot)
            removed = self._agent.history.discard_transaction(transaction_id)
            rollback_ok = removed == len(mutations)
            rollback_output = (
                f"restored and digest-verified the pre-command workspace; "
                f"cleared {removed}/{len(mutations)} transaction record(s)"
            )
        except WorkspaceSnapshotError as exc:
            rollback_ok = False
            rollback_output = f"content-addressed transaction restore failed: {exc}"
        rollback_artifacts = []
        if rollback_ok:
            root = Path(before_snapshot.root)
            for mutation in mutations:
                previous = mutation.before
                rollback_artifacts.append(
                    {
                        "path": str(root / mutation.relative_path),
                        "exists": previous is not None,
                        "sha256": previous.sha256 if previous else None,
                        "size": previous.size if previous else 0,
                        "kind": (
                            previous.kind
                            if previous is not None
                            else (mutation.after.kind if mutation.after else "file")
                        ),
                        "link_target": previous.link_target if previous else "",
                    }
                )
        self._agent.evidence.append(
            kind="post_mutation_integrity",
            claim="Rollback partial mutations from failed command",
            status="verified" if rollback_ok else "failed",
            tool=name,
            artifacts=rollback_artifacts,
            raw_output=rollback_output,
            metadata={
                "transaction_id": transaction_id,
                "rolled_back_changes": len(mutations) if rollback_ok else 0,
                "mutations": [
                    {
                        "path": item.relative_path,
                        "change_type": item.change_type,
                        "before_sha256": item.before.sha256 if item.before else "",
                        "after_sha256": item.after.sha256 if item.after else "",
                    }
                    for item in mutations
                ],
            },
        )
        result.output += (
            "\nRollback " + ("succeeded: " if rollback_ok else "FAILED: ") + rollback_output
        )
        if not rollback_ok:
            result.status = ToolStatus.FAILURE
            result.error = rollback_output
        return mutations, None

    def _rollback_post_mutation_failure(
        self,
        *,
        history_start: int,
        reason: str,
        tool: str,
    ) -> tuple[str, bool]:
        """Restore every file changed by the current tool call before failing."""
        count = max(0, self._history_count() - history_start)
        rollback_ok = count == 0
        rollback_output = "No persisted file changes required rollback."
        if count:
            undo = getattr(self._agent.history, "undo_changes", None)
            if callable(undo):
                rollback_ok, rollback_output = undo(count)
            else:
                rollback_ok = False
                rollback_output = "History backend does not support rollback."
        try:
            self._agent.evidence.append(
                kind="post_mutation_integrity",
                claim="Rollback mutations after post-write integrity failure",
                status="verified" if rollback_ok else "failed",
                tool=tool,
                raw_output=f"{reason} | {rollback_output}",
                metadata={"rolled_back_changes": count},
            )
        except Exception:
            logger.exception("Failed to append post-mutation rollback evidence")
        status = "succeeded" if rollback_ok else "FAILED"
        return (
            f"❌ POST-MUTATION INTEGRITY FAILURE: {reason} Rollback {status}: {rollback_output}",
            False,
        )

    def _track_engineering_context(
        self,
        *,
        name: str,
        args: dict[str, Any],
        file_path: str,
        result: ToolResult,
        success: bool,
    ) -> None:
        """Record inspection and verification obligations outside the main dispatcher."""
        brain = getattr(self._agent, "engineering_brain", None)
        if not success or brain is None:
            return
        if file_path and name in {"read_file", "view_file"}:
            try:
                brain.record_inspection([file_path])
            except (OSError, TypeError, ValueError):
                logger.debug("Engineering inspection tracking failed for %s", file_path)
        if name not in {"run_command", "run_process"}:
            return
        contract = getattr(brain, "contract", None)
        if contract is None:
            return
        command_value = args.get("argv") if name == "run_process" else args.get("command")
        profile = analyse_test_command(command_value or "", root=self._agent.working_dir)
        valid, _detail, _count = validate_test_execution(
            profile,
            output=result.output,
            exit_code=command_exit_code(result.output),
            root=self._agent.working_dir,
        )
        if not valid:
            return
        related = [str(path).replace("\\", "/") for path in contract.related_tests]
        if profile.scope == "full_suite":
            matched = related
        else:
            targets = {target.split("::", 1)[0] for target in profile.targets}
            matched = [
                path
                for path in related
                if path in targets
                or any(path == target or path.endswith("/" + target) for target in targets)
            ]
        if matched:
            try:
                brain.record_verified_files(sorted(set(matched)))
            except (OSError, TypeError, ValueError):
                logger.debug("Engineering verification tracking failed")

    def _apply_execution_isolation(
        self,
        name: str,
        args: dict,
        *,
        user_confirmed: bool = False,
    ) -> None:
        """Bind command execution to the explicit isolation capability.

        Strong modes require kernel isolation by default.  Trusted-host
        execution is available only when the Agent was constructed with the
        explicit ``allow_unisolated_host_process=True`` capability.  If the
        active mode itself requires OS isolation, the exact operation must also
        have been explicitly confirmed by the user before that requirement may
        be overridden.  Modes that already opt out of mandatory isolation do
        not need a second confirmation gate.
        """
        if name not in {"run_command", "run_process", "process_run"}:
            return

        capability = bool(getattr(self._agent, "allow_unisolated_host_process", False))
        mode_requires_isolation = bool(self._agent.mode_policy.require_os_isolation)
        trusted_host = capability and (bool(user_confirmed) or not mode_requires_isolation)
        require_isolation = mode_requires_isolation and not trusted_host
        args["require_os_isolation"] = require_isolation
        args["allow_unisolated_host_process"] = trusted_host

    def _assess_command_policy(
        self, name: str, args: dict, command: str
    ) -> tuple[CommandRisk, str]:
        """Validate one command through the runtime's authoritative policy."""
        if name not in {"run_command", "run_process", "process_run"}:
            return CommandRisk.UNKNOWN, ""
        try:
            command_argv = (
                [str(item) for item in args.get("argv", [])]
                if name == "run_process"
                else shlex.split(str(command))
            )
            raw_cwd = Path(str(args.get("cwd") or ".")).expanduser()
            workspace = Path(self._agent.working_dir).expanduser().resolve()
            command_cwd = (
                raw_cwd.resolve() if raw_cwd.is_absolute() else (workspace / raw_cwd).resolve()
            )
            command_policy = CommandPolicy(workspace)
            command_policy.validate_command(command_argv, command_cwd)
            return command_policy.classify(command_argv), ""
        except (TypeError, ValueError) as exc:
            return CommandRisk.UNKNOWN, str(exc)

    @staticmethod
    def _apply_network_risk(
        name: str,
        args: dict,
        pending_args: dict,
        command_risk: CommandRisk,
    ) -> None:
        needs_network = (
            name in {"run_command", "run_process", "process_run"}
            and command_risk in {CommandRisk.NETWORK_REQUEST, CommandRisk.PACKAGE_INSTALL}
        ) or name == "github_create_pr"
        if needs_network:
            args["network"] = True
            pending_args["network"] = True

    def _collect_scope_paths(self, name: str, args: dict, file_path: str) -> list[str]:
        scope_paths = []
        if name == "multi_edit":
            scope_paths.extend(str(item.get("path", "")) for item in args.get("edits", []))
        elif file_path:
            scope_paths.append(str(file_path))
        if name == "diff_files":
            scope_paths.extend(str(args.get(key, "")) for key in ("file_a", "file_b"))
        elif name in {"search_code", "find_files"}:
            scope_paths.append(str(args.get("directory", "")))
        elif name in {"run_command", "run_process", "process_run"}:
            scope_paths.append(str(args.get("cwd", "")))
        elif name == "repo_impact":
            scope_paths.extend(str(item) for item in args.get("paths", []))
        elif name == "security_scan":
            scope_paths.extend(str(item) for item in args.get("paths", []) or [])
        elif name == "browser_check":
            scope_paths.append(str(args.get("screenshot_path", "")))
        for argument_name in self._agent._external_tool_path_arguments.get(name, ()):
            value = args.get(argument_name)
            if isinstance(value, (list, tuple, set)):
                scope_paths.extend(str(item) for item in value)
            elif value not in (None, ""):
                scope_paths.append(str(value))
        return list(dict.fromkeys(item for item in scope_paths if item))

    def _execute_impl(
        self,
        name: str,
        args: dict,
        *,
        _user_initiated: bool = False,
        _user_confirmed: bool = False,
        _edit_confirmed: bool = False,
    ) -> tuple[str, bool]:
        """
        Execute a tool with full safety checks, hooks, and context tracking.

        Pipeline: Before Hooks → Safety Check → Execute → Context Track → After Hooks → Reflection
        """
        from nexus.agent import _is_relative_to
        from nexus.tools import normalize_tool_arguments

        args = normalize_tool_arguments(name, args)
        declaration = self._agent._tool_capabilities.get(name)
        if declaration is None:
            return f"❌ BLOCKED: Tool '{name}' has no capability declaration.", False
        if declaration.requires(ToolCapability.CONFIRMATION_REQUIRED) and not _user_confirmed:
            capability_check = SafetyCheck(
                level=SafetyLevel.DANGEROUS,
                operation=f"external tool: {name}",
                reason="This tool declares external side effects and requires approval",
                details=json.dumps(args, ensure_ascii=False, default=str)[:2000],
                requires_confirmation=True,
            )
            confirmation_id = self._agent._queue_confirmation(
                name=name,
                args=args,
                safety_check=capability_check,
                edit_confirmed=_edit_confirmed,
            )
            display_text = (
                f"⏸️ PENDING_CONFIRMATION [{confirmation_id}]: {capability_check.reason}.\n"
                f"Enter /confirm {confirmation_id} or /cancel {confirmation_id}."
            )
            return (
                self._pending_confirmation_sentinel(
                    confirmation_id, "CAPABILITY_CONFIRMATION", display_text
                ),
                False,
            )
        if name == "run_command" and not self._agent.mode_policy.allow_shell_command:
            return (
                "❌ BLOCKED: shell-string execution is disabled in this mode; use "
                "run_process with an explicit argv array.",
                False,
            )
        pending_args = dict(args)
        nova_guardrail = args.pop("_nova_guardrail", None)
        file_path = args.get("path", "") or args.get("file_path", "")
        command = args.get("command", "")
        if name == "run_process":
            raw_argv = args.get("argv", [])
            command = shlex.join(str(item) for item in raw_argv) if raw_argv else ""
        command_risk, command_error = self._assess_command_policy(name, args, command)
        if command_error:
            return (
                f"❌ BLOCKED: authoritative command policy rejected the request: {command_error}",
                False,
            )
        self._apply_network_risk(name, args, pending_args, command_risk)
        mutation_tools = ("write_file", "edit_file", "patch_file", "multi_edit")
        read_tools = {
            "read_file",
            "file_info",
            "diff_files",
            "search_code",
            "list_directory",
            "find_files",
            "get_project_structure",
            "repo_index",
            "repo_symbols",
            "repo_impact",
            "repo_context",
            "repo_routes",
            "repo_models",
            "repo_navigate",
            "database_check",
            "security_scan",
        }

        scope_paths = self._collect_scope_paths(name, args, file_path)

        # ── 1. Enforce Tool Policy
        ok, policy_capability, err_res = self._enforce_tool_policy(
            name,
            args,
            command,
            scope_paths,
            pending_args,
            _user_initiated,
            _user_confirmed,
            _edit_confirmed,
            mutation_tools,
            read_tools,
            command_risk,
        )
        if not ok:
            return err_res

        # ── 2. Enforce Network Safety
        ok, err_res = self._enforce_network_safety(
            name, args, command, pending_args, _user_initiated, _user_confirmed, _edit_confirmed
        )
        if not ok:
            return err_res

        # Engineering Brain surgical scope gate.  The model cannot silently
        # expand the approved mutation surface.
        scope_ok, scope_error = self._enforce_engineering_scope(
            name, args, scope_paths, mutation_tools
        )
        if not scope_ok and scope_error is not None:
            return scope_error

        # Nova Guardrail checks for mutations
        if name in mutation_tools:
            if nova_guardrail is not None and not nova_guardrail.get("passed"):
                return "❌ BLOCKED: Nova guardrail metadata was present but did not pass.", False
            if self._agent._is_nova_model() and (
                not nova_guardrail or not nova_guardrail.get("passed")
            ):
                return (
                    "❌ BLOCKED: Nova file edit reached Noryx without a passing Nova "
                    "guardrail verdict (path validation, constraint verification, and disk gate).",
                    False,
                )
            early_edits = args.get("edits", []) if name == "multi_edit" else [args]
            for early_edit in early_edits:
                early_path = early_edit.get("path", "")
                early_content = (
                    early_edit.get("content", "")
                    or early_edit.get("new_text", "")
                    or early_edit.get("new_content", "")
                )
                early_check = self._agent.safety.check_file_write(early_path, early_content)
                if early_check.level == SafetyLevel.BLOCKED:
                    return f"❌ BLOCKED: {early_check.reason}", False

        # Resolve scope outside workspace
        for scoped_path in (item for item in scope_paths if item):
            resolved_file = Path(scoped_path).expanduser()
            if not resolved_file.is_absolute():
                resolved_file = Path(self._agent.working_dir) / resolved_file
            resolved_file = resolved_file.resolve()
            roots = [
                Path(self._agent.working_dir),
                *(Path(item) for item in self._agent.additional_dirs),
            ]
            if any(_is_relative_to(resolved_file, root) for root in roots):
                continue
            if not _user_confirmed:
                scope_check = SafetyCheck(
                    level=SafetyLevel.DANGEROUS,
                    operation=f"{name} outside workspace",
                    reason="File access is outside the current workspace",
                    details=str(resolved_file),
                    requires_confirmation=True,
                )
                confirmation_id = self._agent._queue_confirmation(
                    name=name,
                    args=pending_args,
                    safety_check=scope_check,
                    edit_confirmed=_edit_confirmed,
                )
                display_text = (
                    f"⏸️ PENDING_CONFIRMATION [{confirmation_id}]: {scope_check.reason}.\n"
                    "This operation was not executed. Review the exact operation, then\n"
                    f"enter /confirm {confirmation_id} or /cancel {confirmation_id}.\n"
                    f"{scope_check.details}"
                )
                return (
                    self._pending_confirmation_sentinel(
                        confirmation_id, "SCOPE_CONFIRMATION", display_text
                    ),
                    False,
                )

        # ── 3. Enforce Package Safety
        ok, package_warning_text, err_res = self._enforce_package_safety(
            name, args, command, pending_args, _user_confirmed, _edit_confirmed, mutation_tools
        )
        if not ok:
            return err_res

        # ── 4. File diff approval gate
        ok, mutation_diff, err_res = self._prepare_mutation_diff(
            name, args, pending_args, _user_confirmed, _edit_confirmed, mutation_tools
        )
        if not ok:
            return err_res

        # ── Fire BEFORE hooks
        event_before = None
        event_after = None

        if name in ("write_file",):
            event_before = HookEvent.BEFORE_FILE_CREATE
            event_after = HookEvent.AFTER_FILE_CREATE
        elif name in ("edit_file", "patch_file", "multi_edit"):
            event_before = HookEvent.BEFORE_FILE_EDIT
            event_after = HookEvent.AFTER_FILE_EDIT
        elif name in ("run_command", "run_process", "process_run"):
            event_before = HookEvent.BEFORE_COMMAND
            event_after = HookEvent.AFTER_COMMAND
        elif name == "git_commit":
            event_before = HookEvent.BEFORE_COMMIT
            event_after = HookEvent.AFTER_COMMIT

        hook_ctx = HookContext(
            event=event_before or HookEvent.BEFORE_COMMAND,
            file_path=file_path,
            command=command,
            tool_name=name,
            tool_args=args,
        )

        if event_before:
            hook_ctx.event = event_before
            hook_results = self._agent.hooks.fire(event_before, hook_ctx)
            if any(r.blocked for r in hook_results):
                return "❌ Operation blocked by hook policy.", False

        # ── Safety check ──
        safety_check = None
        if name in ("run_command", "run_process", "process_run") and command:
            safety_check = self._agent.safety.check_command(command)
        elif name == "multi_edit":
            for edit in args.get("edits", []):
                check = self._agent.safety.check_file_write(
                    edit.get("path", ""), edit.get("new_text", "")
                )
                if check.level in (SafetyLevel.BLOCKED, SafetyLevel.DANGEROUS):
                    safety_check = check
                    break
        elif name in mutation_tools and file_path:
            content_val = (
                args.get("content", "") or args.get("new_text", "") or args.get("new_content", "")
            )
            safety_check = self._agent.safety.check_file_write(file_path, content_val)

        if safety_check and safety_check.level == SafetyLevel.BLOCKED:
            return f"❌ BLOCKED: {safety_check.reason}", False
        elif safety_check and safety_check.level == SafetyLevel.DANGEROUS and not _user_confirmed:
            confirmation_id = self._agent._queue_confirmation(
                name=name,
                args=pending_args,
                safety_check=safety_check,
                edit_confirmed=_edit_confirmed,
            )
            display_text = (
                f"⏸️ PENDING_CONFIRMATION [{confirmation_id}]: {safety_check.reason}.\n"
                "This operation was not executed. Review the exact operation, then\n"
                f"enter /confirm {confirmation_id} or /cancel {confirmation_id}.\n"
                f"{safety_check.details}"
            )
            return (
                self._pending_confirmation_sentinel(
                    confirmation_id, "COMMAND_CONFIRMATION", display_text
                ),
                False,
            )
        # A safe-looking command is not a containment boundary. Production
        # presets require kernel-backed isolation; trusted local qualification
        # must opt into host execution as a separate capability.
        self._apply_execution_isolation(name, args, user_confirmed=_user_confirmed)

        # ── 5. Execute
        before_snapshot, command_transaction_id, journal_error = self._begin_command_transaction(
            name
        )
        if journal_error is not None:
            return journal_error

        history_start = self._history_count()
        change_set = None
        change_set_persistence = None
        if name == "multi_edit":
            try:
                change_set, change_set_persistence = self._prepare_multi_edit_change_set(args)
            except Exception as exc:
                return (
                    f"❌ BLOCKED: Unable to register multi-file transaction before mutation: {exc}",
                    False,
                )

        result = self._dispatch_tool_execution(name, args)

        if name == "multi_edit" and change_set is not None and result.status == ToolStatus.SUCCESS:
            try:
                change_set.applied_file_paths = change_set.file_paths()
                if change_set_persistence is not None:
                    change_set_persistence.save_change_set(change_set)
                logger.debug(
                    "EngineeringChangeSet registered: %s with %d files",
                    change_set.change_set_id,
                    len(change_set.file_changes),
                )
            except Exception as exc:
                return self._rollback_post_mutation_failure(
                    history_start=history_start,
                    reason=f"Multi-file change-set finalization failed: {exc}",
                    tool=name,
                )

        success = result.status == ToolStatus.SUCCESS
        if name in ("api_check", "database_check", "browser_check", "security_scan") and success:
            try:
                if json.loads(result.output).get("status") != "passed":
                    result.status = ToolStatus.FAILURE
                    if not result.error:
                        result.error = f"{name} did not report passed status"
            except (AttributeError, TypeError, json.JSONDecodeError):
                result.status = ToolStatus.FAILURE
                if not result.error:
                    result.error = f"{name} returned an invalid structured response"
            success = result.status == ToolStatus.SUCCESS
        current_workspace_revision = self._current_workspace_revision() if success else ""

        if package_warning_text:
            result.output = package_warning_text + "\n" + result.output

        # Reconcile synchronous command mutations regardless of exit status.
        _command_mutations, command_integrity_error = self._finish_command_transaction(
            name=name,
            before_snapshot=before_snapshot,
            transaction_id=command_transaction_id,
            success=success,
            result=result,
        )
        if command_integrity_error is not None:
            return command_integrity_error

        # ── Verified-completion evidence
        if success and name in mutation_tools:
            verified, detail, artifacts = verify_mutation(name, args, self._agent.working_dir)
            code_checks = []
            code_failures = []
            if verified:
                candidate_actions = []
                raw_paths = (
                    [edit.get("path", "") for edit in args.get("edits", [])]
                    if name == "multi_edit"
                    else [args.get("path", "")]
                )
                for raw_path in raw_paths:
                    try:
                        from nexus.agent import _is_relative_to

                        path_obj = Path(raw_path).expanduser()
                        if not path_obj.is_absolute():
                            path_obj = Path(self._agent.working_dir) / path_obj
                        if path_obj.is_absolute() and not _is_relative_to(
                            path_obj, Path(self._agent.working_dir)
                        ):
                            continue
                        relative = path_obj.resolve().relative_to(Path(self._agent.working_dir))
                        candidate_actions.append(SimpleNamespace(path=str(relative)))
                    except ValueError:
                        continue
                code_checks = GeneratedCodeValidator(self._agent.working_dir).validate(
                    candidate_actions
                )
                code_failures = [check for check in code_checks if not check.passed]
                if code_failures:
                    verified = False
                    detail = "compiler validation failed: " + " | ".join(
                        check.format() for check in code_failures
                    )
                    undo_count = len(args.get("edits", [])) if name == "multi_edit" else 1
                    rollback_ok, rollback_output = self._agent.history.undo_changes(
                        max(1, undo_count)
                    )
                    detail += (
                        f" | rollback={'succeeded' if rollback_ok else 'failed'}: {rollback_output}"
                    )
            if code_checks:
                self._agent.evidence.append(
                    kind="verification_check",
                    claim="generated code compiler and syntax validation",
                    status="verified" if not code_failures else "failed",
                    tool="generated_code_validator",
                    command="generated-code-validator",
                    exit_code=0 if not code_failures else 1,
                    raw_output="\n".join(check.format() for check in code_checks),
                    metadata=self._evidence_metadata("syntax", current_workspace_revision),
                )
            self._agent.evidence.append(
                kind="file_mutation",
                claim=f"{name} persisted the requested change",
                status="verified" if verified else "failed",
                tool=name,
                artifacts=artifacts,
                raw_output=result.output,
                metadata=self._evidence_metadata(
                    "mutation", current_workspace_revision, verification=detail
                ),
            )
            if not verified:
                return (
                    f"❌ WRITE VERIFICATION FAILED: {detail}\nRaw tool output:\n{result.output}",
                    False,
                )
            if self._agent.run_ledger.turn_dir and mutation_diff:
                self._agent.run_ledger.store_artifact(
                    "patches",
                    f"{name}-{len(self._agent.history.changes):04d}.diff",
                    mutation_diff,
                )
            result.output += f"\n🔎 VERIFIED: {detail}\nEvidence: {self._agent.evidence.path}"
        elif name in ("run_command", "run_process", "process_run"):
            exit_code = (
                command_exit_code(result.output) if name in ("run_command", "run_process") else None
            )
            status = (
                "verified" if success and (exit_code == 0 or name == "process_run") else "failed"
            )
            self._agent.evidence.append(
                kind="command",
                claim=f"executed command: {command}",
                status=status,
                tool=name,
                command=command,
                exit_code=exit_code,
                raw_output=result.output,
            )
        elif name in ("api_check", "database_check", "browser_check", "security_scan"):
            probe_status = ""
            try:
                probe_status = str(json.loads(result.output).get("status", ""))
            except (TypeError, json.JSONDecodeError):
                pass
            self._agent.evidence.append(
                kind="behavioral_verification",
                claim=f"executed {name}",
                status="verified" if success and probe_status == "passed" else "failed",
                tool=name,
                raw_output=result.output,
                metadata=self._evidence_metadata(
                    name, current_workspace_revision, probe_status=probe_status
                ),
            )
        elif name.startswith("git_"):
            self._agent.evidence.append(
                kind="git_operation",
                claim=f"executed {name}",
                status="verified" if success else "failed",
                tool=name,
                raw_output=result.output,
                metadata={"arguments": args},
            )

        # ── 6. Track file access in context manager
        if file_path:
            was_edited = name in ("write_file", "edit_file", "patch_file", "multi_edit")
            self._agent.context_mgr.track_file_access(file_path, was_edited=was_edited)
            if success and name == "read_file" and result:
                self._agent.context_mgr.track_file_imports(file_path, result.output)
                self._agent.context_mgr.summarize_file(file_path, result.output)
        self._track_engineering_context(
            name=name, args=args, file_path=file_path, result=result, success=success
        )

        # ── 7. Fire AFTER hooks
        if event_after:
            hook_ctx.event = event_after
            hook_ctx.tool_result = result.output
            self._agent.hooks.fire(event_after, hook_ctx)

        # ── 8. Fire error hook on failure
        if not success:
            self._agent.hooks.fire(
                HookEvent.ON_ERROR,
                HookContext(
                    event=HookEvent.ON_ERROR,
                    error_message=result.output[:500],
                    tool_name=name,
                    tool_args=args,
                ),
            )

        return result.output, success

    # ──────────────────────────────────────────────────────────────────────────
    # Introspection helpers (used by tests and the dashboard)
    # ──────────────────────────────────────────────────────────────────────────

    def is_mutation_tool(self, name: str) -> bool:
        """Return True if *name* modifies files on disk."""
        return name in self._MUTATION_TOOLS

    def is_read_tool(self, name: str) -> bool:
        """Return True if *name* is a read-only operation."""
        return name in self._READ_TOOLS

    def needs_network_tag(self, command: str) -> bool:
        """Return True if *command* string implies external network access."""
        return bool(self._NETWORK_COMMAND_PATTERN.search(command))

    def describe_pipeline(self) -> dict[str, list[str]]:
        """Return a machine-readable description of the execution pipeline stages."""
        return {
            "stages": [
                "capability_check",
                "confirmation_gate",
                "shell_command_policy",
                "network_tag",
                "scope_path_resolution",
                "before_hooks",
                "safety_layer",
                "execute_tool",
                "evidence_record",
                "after_hooks",
                "reflection",
            ],
            "mutation_tools": list(self._MUTATION_TOOLS),
            "read_tools": sorted(self._READ_TOOLS),
        }


# ─── Convenience factory ─────────────────────────────────────────────────────


def make_controller(agent: "Agent") -> ToolExecutionController:
    """Create and attach a ``ToolExecutionController`` to *agent*."""
    ctrl = ToolExecutionController(agent)
    agent._tool_controller = ctrl  # type: ignore[attr-defined]
    return ctrl
