import logging
from pathlib import Path

from nexus.approvals import preview_mutation

# Phase 3: Hooks, MCP & Plugins
# Phase 1: Core Engine Imports
from nexus.safety import SafetyCheck

# Phase 2: Skills & Subagents

logger = logging.getLogger(__name__)



class WorkspaceManagerMixin:
    def _queue_edit(self, name: str, args: dict, diff: str) -> str:
        for edit_id, pending in self._pending_edits.items():
            if pending["name"] == name and pending["args"] == args:
                return edit_id
        edit_id = f"edit-{self._next_edit_id:04d}"
        self._next_edit_id += 1
        self._pending_edits[edit_id] = {"name": name, "args": dict(args), "diff": diff}
        return edit_id

    def apply_pending_edit(self, edit_id: str = "") -> tuple[str, bool]:
        edit_id = edit_id.strip()
        if not edit_id and len(self._pending_edits) == 1:
            edit_id = next(iter(self._pending_edits))
        pending = self._pending_edits.pop(edit_id, None)
        if not pending:
            return f"Unknown or expired edit id: {edit_id or '(none)'}", False
        result = self._tool_controller.execute(
            pending["name"], dict(pending["args"]), _edit_confirmed=True
        )
        self._refresh_final_report_after_approval()
        return result

    def reject_pending_edit(self, edit_id: str = "") -> tuple[str, bool]:
        edit_id = edit_id.strip()
        if not edit_id and len(self._pending_edits) == 1:
            edit_id = next(iter(self._pending_edits))
        pending = self._pending_edits.pop(edit_id, None)
        if not pending:
            return f"Unknown or expired edit id: {edit_id or '(none)'}", False
        result = f"Rejected {edit_id}; no file was changed.", True
        self._refresh_final_report_after_approval()
        return result

    def replace_pending_edit(self, edit_id: str, replacement_file: str) -> tuple[str, bool]:
        pending = self._pending_edits.get(edit_id.strip())
        if not pending:
            return f"Unknown or expired edit id: {edit_id}", False
        target = pending["args"].get("path")
        source = Path(replacement_file).expanduser().resolve()
        if not target or not source.is_file():
            return "Replacement file does not exist or pending edit has no single target.", False
        content = source.read_text(encoding="utf-8")
        args = {"path": target, "content": content}
        ok, diff = preview_mutation("write_file", args, self.working_dir)
        if not ok:
            return diff, False
        pending.update({"name": "write_file", "args": args, "diff": diff})
        return f"Updated {edit_id} preview:\n{diff}", True

    def pending_edits_summary(self) -> str:
        if not self._pending_edits:
            return "No file edits are pending."
        lines = [f"Pending edits ({len(self._pending_edits)}):"]
        for edit_id, pending in self._pending_edits.items():
            lines.append(
                f"  {edit_id}: {pending['name']} {pending['args'].get('path', '(multiple files)')}"
            )
        return "\n".join(lines)

    def _queue_confirmation(
        self, name: str, args: dict, safety_check: SafetyCheck, edit_confirmed: bool = False
    ) -> str:
        """Store an exact dangerous tool call until the user confirms or cancels it."""
        for confirmation_id, pending in self._pending_confirmations.items():
            if pending["name"] == name and pending["args"] == args:
                return confirmation_id

        confirmation_id = f"danger-{self._next_confirmation_id:04d}"
        self._next_confirmation_id += 1
        self._pending_confirmations[confirmation_id] = {
            "name": name,
            "args": dict(args),
            "reason": safety_check.reason,
            "details": safety_check.details,
            "edit_confirmed": edit_confirmed,
        }
        return confirmation_id

    def confirm_pending_operation(self, confirmation_id: str = "") -> tuple[str, bool]:
        """Execute one exact pending operation after explicit user confirmation."""
        confirmation_id = confirmation_id.strip()
        if not confirmation_id:
            if len(self._pending_confirmations) == 1:
                confirmation_id = next(iter(self._pending_confirmations))
            elif not self._pending_confirmations:
                return "No dangerous operation is pending confirmation.", False
            else:
                ids = ", ".join(self._pending_confirmations)
                return f"Multiple operations are pending; specify one: {ids}", False

        pending = self._pending_confirmations.pop(confirmation_id, None)
        if not pending:
            return f"Unknown or expired confirmation id: {confirmation_id}", False

        result = self._tool_controller.execute(
            pending["name"],
            dict(pending["args"]),
            _user_confirmed=True,
            _edit_confirmed=bool(pending.get("edit_confirmed")),
        )
        self._refresh_final_report_after_approval()
        return result

    def cancel_pending_operation(self, confirmation_id: str = "") -> tuple[str, bool]:
        """Cancel one pending dangerous operation without executing it."""
        confirmation_id = confirmation_id.strip()
        if not confirmation_id:
            if len(self._pending_confirmations) == 1:
                confirmation_id = next(iter(self._pending_confirmations))
            elif not self._pending_confirmations:
                return "No dangerous operation is pending confirmation.", False
            else:
                ids = ", ".join(self._pending_confirmations)
                return f"Multiple operations are pending; specify one: {ids}", False

        pending = self._pending_confirmations.pop(confirmation_id, None)
        if not pending:
            return f"Unknown or expired confirmation id: {confirmation_id}", False
        result = f"Cancelled {confirmation_id}; the operation was not executed.", True
        self._refresh_final_report_after_approval()
        return result

    def _apply_verified_workspace(self) -> tuple[bool, str]:
        """Apply a verified isolated workspace exactly once.

        Automatic application is restricted to modes whose policy explicitly
        grants ``may_apply``. Review/workspace modes continue to return a diff
        for human approval. A failed merge is treated as a failed run rather
        than reporting a false VERIFIED outcome.
        """
        if self._workspace_applied:
            return True, self._workspace_apply_detail or "Workspace already applied."
        if not self.mode_policy.may_apply:
            return True, "Execution mode requires manual workspace application."
        if self.worktree is None or self.worktree.info is None:
            return True, "No isolated workspace needs application."

        try:
            pending_diff = self.worktree.diff()
            if not pending_diff.strip():
                self._workspace_applied = True
                self._workspace_apply_detail = "Verified run produced no workspace diff."
                return True, self._workspace_apply_detail
            self.worktree.apply()
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            detail = f"Verified workspace could not be applied safely: {exc}"
            self._workspace_apply_detail = detail
            self.evidence.append(
                kind="workspace_apply",
                claim="apply verified isolated workspace to source repository",
                status="failed",
                raw_output=detail,
                metadata={
                    "source": self.source_working_dir,
                    "workspace": self.working_dir,
                },
            )
            return False, detail

        self._workspace_applied = True
        self._workspace_apply_detail = (
            "Verified isolated workspace was applied to the source repository."
        )
        self._permissions_used.add("workspace: apply verified changes")
        self.evidence.append(
            kind="workspace_apply",
            claim="apply verified isolated workspace to source repository",
            status="verified",
            raw_output=self._workspace_apply_detail,
            metadata={
                "source": self.source_working_dir,
                "workspace": self.working_dir,
                "backend": self.worktree.info.backend,
            },
        )
        return True, self._workspace_apply_detail

    def rollback_current_run(self) -> tuple[bool, str]:
        """Atomically roll back every file operation recorded by this run."""
        change_count = len(self.history.changes) - self._run_history_start
        if change_count <= 0:
            return False, "The current run has no applied file changes to roll back."
        success, detail = self.history.undo_changes(change_count)
        if success:
            self.run_ledger.mark_rolled_back(detail)
            try:
                self.repo_graph.build()
            except (OSError, ValueError) as exc:
                logger.debug("Repository graph refresh after rollback failed: %s", exc)
        return success, detail

