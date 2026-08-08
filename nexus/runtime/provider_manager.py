import json
import logging
import time
from typing import Any

from nexus import ui
from nexus.agent import _redact_runtime_text
from nexus.budget import BudgetedClient, BudgetExceeded
from nexus.hooks.base import HookContext, HookEvent

# Phase 3: Hooks, MCP & Plugins
from nexus.models import MODELS, resolve_model, resolve_model_key

# Phase 1: Core Engine Imports
from nexus.planner import IntentType, TaskStatus
from nexus.providers.hosted import HostedProvider
from nexus.providers.nova import NovaProvider
from nexus.runtime.events import EventType
from nexus.runtime.session import ExecutionSession

# Phase 2: Skills & Subagents
from nexus.verification import CheckStatus

logger = logging.getLogger(__name__)



class ProviderManagerMixin:
    def set_model(self, model_key: str) -> bool:
        """Switch to a different model."""
        resolved_key = resolve_model_key(model_key)
        if not resolved_key:
            return False
        cfg = resolve_model(resolved_key) or dict(MODELS[resolved_key])
        if cfg.get("backend") == "nova":
            self.client = NovaProvider(
                model_name=cfg.get("ollama_model", "nova_codex"), working_dir=self.working_dir
            )
        else:
            try:
                primary = HostedProvider(
                    api_key=self._api_key,
                    attempt_controller=self.budget,
                    attempt_observer=self._record_provider_attempt,
                )
                self.client = BudgetedClient(primary, self.budget)
            except ValueError:
                return False
        self.model_key = resolved_key
        self.model_cfg = cfg
        self.hooks.fire(
            HookEvent.ON_MODEL_SWITCH,
            HookContext(
                event=HookEvent.ON_MODEL_SWITCH,
                metadata={"model": resolved_key},
            ),
        )
        return True

    def _is_nova_model(self) -> bool:
        """Return True when the active model uses the local Nova backend."""
        return self.model_cfg.get("backend") == "nova"

    def _should_use_two_node(self, analysis: dict) -> bool:
        """Use Ceiling+Intern for coding/workspace tasks handled by hosted models."""
        if (
            self._is_nova_model()
            or not self.model_cfg.get("supports_tools")
            or not self.local_intern_enabled
        ):
            return False
        intent = analysis.get("intent")
        return intent not in (IntentType.CHAT, IntentType.EXPLAIN, IntentType.SEARCH)

    def _record_provider_attempt(self, attempt: dict[str, Any]) -> None:
        """Persist one physical provider request emitted by the hosted router."""

        if not getattr(self, "run_ledger", None) or not self.run_ledger.turn_dir:
            return
        error = str(attempt.get("error", ""))
        error_category = ""
        if error:
            from nexus.runtime.kernel import classify_failure

            error_category = classify_failure(error).value
        usage = attempt.get("usage") if isinstance(attempt.get("usage"), dict) else {}
        limits = self.budget.snapshot().get("limits", {})
        input_price = limits.get("input_price_per_million")
        output_price = limits.get("output_price_per_million")
        estimated_cost = 0.0
        if input_price is not None and output_price is not None:
            estimated_cost = (
                int(usage.get("prompt_tokens", 0) or 0) * float(input_price)
                + int(usage.get("completion_tokens", 0) or 0) * float(output_price)
            ) / 1_000_000
        self.run_ledger.append_model_call(
            role="provider_attempt",
            model=str(attempt.get("model", self.model_cfg.get("id", ""))),
            provider=str(attempt.get("provider", "")),
            status=str(attempt.get("status", "failed")),
            usage=usage,
            request_id=str(attempt.get("request_id", "")),
            started_at=str(attempt.get("started_at", "")),
            completed_at=str(attempt.get("completed_at", "")),
            duration_ms=int(attempt.get("duration_ms", 0) or 0),
            attempt=int(attempt.get("attempt", 1) or 1),
            physical_attempt=int(attempt.get("physical_attempt", 1) or 1),
            retry_number=max(0, int(attempt.get("attempt", 1) or 1) - 1),
            fallback_from=str(attempt.get("fallback_from", "")),
            estimated_cost_usd=estimated_cost,
            error_category=error_category,
            detail=_redact_runtime_text(error[:1000]),
        )

    def _run_hosted_turn(
        self,
        user_input: str,
        analysis: dict,
        plan: Any,
        interactive: bool = False,
        emit_ui: bool = False,
        max_turns_override: int | None = None,
    ) -> tuple[str, list[dict]]:
        """Run a standard hosted-model execution loop (single-node)."""
        _run_id = (
            self.run_ledger.session_id if hasattr(self, "run_ledger") and self.run_ledger else None
        )
        session = ExecutionSession(
            provider=self.client,
            max_turns=max_turns_override or self.max_turns,
            model_id=self.model_cfg["id"],
            run_id=_run_id,
            ledger=self.run_ledger,
        )
        engine = session.interactive

        def handle_tool(name, args):
            tc = [{"id": f"call_{time.time()}", "name": name, "arguments": json.dumps(args)}]
            res, successes = self._handle_tool_calls_interactive(tc, emit_ui=emit_ui)
            return successes[0], res[0]["content"]

        engine.tool_executor = handle_tool

        # Auto-activate skills
        try:
            self.skills.auto_activate(
                user_input,
                intent=analysis["intent"].value
                if hasattr(analysis["intent"], "value")
                else str(analysis.get("intent", "unknown")),
            )
            self._update_system_prompt()
        except (LookupError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug("Automatic skill activation failed: %s", exc)

        self.messages.append({"role": "user", "content": user_input})
        events = engine.run_interactive(self._build_messages(), tools=self._get_tools())

        live = ui.LiveStatus() if emit_ui else None
        content = ""
        accumulated_events = []

        try:
            for event in events:
                if event.type == EventType.MODEL_REQUEST_STARTED:
                    if live:
                        live.start(f"Connecting to {self.model_cfg['name']}...")
                elif event.type == EventType.MODEL_REQUEST_COMPLETED:
                    if live:
                        live.stop()
                    accumulated_events.append(
                        {
                            "type": "model_turn",
                            "model": event.model,
                            "usage": event.usage,
                            "node": "hosted",
                        }
                    )
                elif event.type == EventType.MODEL_STREAM_CHUNK:
                    if live and live._is_active:
                        live.stop()
                    if emit_ui:
                        ui.console.print(event.text, end="", style=ui.WHITE, highlight=False)
                elif event.type == EventType.TOOL_CALL_STARTED:
                    if live:
                        live.update(f"Running tool {event.tool_name}...")
                elif event.type == EventType.TOOL_CALL_COMPLETED:
                    accumulated_events.append(
                        {
                            "type": "tool_call",
                            "name": event.tool_name,
                            "args": event.arguments,
                            "result": event.result,
                            "success": event.success,
                            "node": "interactive",
                        }
                    )
                elif event.type == EventType.RUN_FAILED:
                    raise RuntimeError(event.error)
                elif event.type == EventType.RUN_COMPLETED:
                    content = event.content
        except LookupError as e:
            if live:
                live.stop()
            error_msg = str(e)
            if isinstance(e, BudgetExceeded):
                content = f"BLOCKED: {error_msg}"
                if hasattr(self, "run_ledger") and self.run_ledger:
                    self.run_ledger.append_event("budget", status="blocked", detail=error_msg)
                if emit_ui:
                    ui.print_error(content)
                return content, accumulated_events

            is_rate_limit = (
                "429" in error_msg.lower()
                or "rate" in error_msg.lower()
                or "resourceexhausted" in error_msg.lower()
                or "too many requests" in error_msg.lower()
            )
            if (
                (is_rate_limit or "Nexus AI Provider Failover Error" in error_msg)
                and self.enable_nova_fallback
                and self.local_intern_enabled
            ):
                if emit_ui:
                    ui.print_warning(
                        "Hosted providers are unavailable — using the explicitly enabled local Nova fallback."
                    )
                if self.messages and self.messages[-1]["role"] == "user":
                    self.messages.pop()
                return self._run_nova_turn(user_input, emit_ui=emit_ui)

            if emit_ui:
                ui.print_error(f"API error: {error_msg}")
            return f"Error: {error_msg}", accumulated_events

        if content:
            content = self._guard_completion_claims(content)
            self.messages.append({"role": "assistant", "content": content})
            if emit_ui:
                ui.console.print()

        if emit_ui:
            ui.print_response_complete()
        self._auto_save()

        # ── Post-plan verification ────────────────────────────────
        if plan and hasattr(plan, "steps"):
            current_step = next((s for s in plan.steps if s.status == TaskStatus.IN_PROGRESS), None)
            if current_step:
                tool_events = [
                    event
                    for event in accumulated_events
                    if isinstance(event, dict) and event.get("type") == "tool_call"
                ]
                successful_tools = {
                    str(event.get("name", ""))
                    for event in tool_events
                    if event.get("success", False)
                }
                mutation_tools = {"write_file", "edit_file", "patch_file", "multi_edit"}
                expected_tools = set(current_step.tools_needed)
                expected_mutation = bool(expected_tools & mutation_tools)
                expected_command = bool(expected_tools & {"run_command", "run_process"})
                mutated = bool(successful_tools & mutation_tools)
                contract_missing = (
                    (expected_mutation and not mutated)
                    or (expected_command and not successful_tools & {"run_command", "run_process"})
                    or (expected_tools and not successful_tools)
                )
                response_failed = (
                    (content or "").lstrip().upper().startswith(("ERROR:", "BLOCKED:"))
                )
                if response_failed or contract_missing:
                    self.planner.advance_step(
                        current_step.id,
                        TaskStatus.FAILED,
                        (
                            "The model stopped with an execution error."
                            if response_failed
                            else "The step ended without satisfying its required tool contract."
                        ),
                    )
                elif mutated:
                    # Read-only diagnostic steps may inspect a broken tree. A
                    # mutating step must leave syntax and imports coherent.
                    syntax_check = self.verifier.verify_syntax()
                    import_check = self.verifier.verify_imports()
                    syntax_ok = syntax_check.status in {
                        CheckStatus.PASSED,
                        CheckStatus.NOT_APPLICABLE,
                    }
                    imports_ok = import_check.status in {
                        CheckStatus.PASSED,
                        CheckStatus.NOT_APPLICABLE,
                    }
                    if syntax_ok and imports_ok:
                        self.planner.advance_step(
                            current_step.id,
                            TaskStatus.COMPLETED,
                            "Step executed successfully",
                        )
                    else:
                        err_msg = ""
                        if not syntax_ok:
                            err_msg += f"Syntax error: {syntax_check.output}\n"
                        if not imports_ok:
                            err_msg += f"Import error: {import_check.output}\n"
                        self.planner.advance_step(current_step.id, TaskStatus.FAILED, err_msg)
                else:
                    self.planner.advance_step(
                        current_step.id, TaskStatus.COMPLETED, "Step executed successfully"
                    )

        if plan and plan.is_complete:
            if emit_ui:
                ui.print_info("📋 Plan complete. Running verification...")
            try:
                report = self._record_verification_report(self._run_verification_suite())
                if emit_ui:
                    ui.console.print(report.format_report())
                if report.all_passed:
                    self.hooks.fire(
                        HookEvent.ON_PLAN_COMPLETE, HookContext(event=HookEvent.ON_PLAN_COMPLETE)
                    )
                else:
                    self.hooks.fire(
                        HookEvent.ON_TEST_FAIL, HookContext(event=HookEvent.ON_TEST_FAIL)
                    )
            except (OSError, ValueError) as exc:
                logger.warning("Plan-completion verification failed: %s", exc)

        return content or "", accumulated_events

    def _run_two_node_turn(
        self, user_input: str, analysis: dict, emit_ui: bool = True
    ) -> tuple[str, list[dict]]:
        """Run a hosted-model turn through Ceiling planning and Nova Intern execution."""
        from nexus.two_node_backend import TwoNodeBackend

        events: list[dict] = []
        self.messages.append({"role": "user", "content": user_input})

        backend = TwoNodeBackend(
            client=self.client,
            ceiling_model_id=self.model_cfg["id"],
            ceiling_model_name=self.model_cfg["name"],
            working_dir=self.working_dir,
            intern_model=self.model_cfg.get("intern_model", "nova_codex"),
            run_ledger=self.run_ledger,
        )

        try:
            if emit_ui:
                live = ui.LiveStatus()
                live.start("Preparing task graph...")
                try:
                    result = backend.run(user_input, planner_analysis=analysis)
                finally:
                    live.stop()
            else:
                result = backend.run(user_input, planner_analysis=analysis)
        except (OSError, RuntimeError) as e:
            if emit_ui:
                ui.print_warning(f"Two-node backend error: {e}")
            if self.messages and self.messages[-1]["role"] == "user":
                self.messages.pop()
            raise RuntimeError(f"Two-node backend failed: {e}") from e

        def record_result(candidate, phase: str) -> None:
            if candidate.execution_plan is not None:
                self._active_plan = candidate.execution_plan
                self.planner.current_plan = candidate.execution_plan
            self.run_ledger.append_model_call(
                role=f"ceiling_{phase}",
                model=self.model_cfg["id"],
                status=("verified" if candidate.review_approved else "failed"),
                usage=self.budget.snapshot().get("usage", {}),
                detail=candidate.review_summary,
            )
            self.evidence.append(
                kind="independent_review",
                claim=f"independent reviewer evaluated {phase} candidate",
                status="verified" if candidate.review_approved else "failed",
                raw_output=candidate.review_summary,
                metadata={"findings": candidate.review_findings},
            )
            for execution in candidate.executions:
                if execution.node.startswith("Nova") and not execution.escalated:
                    self.routing_stats["nova_tasks"] += 1
                else:
                    self.routing_stats["ceiling_tasks"] += 1
                self.routing_stats["nova_retries"] += max(0, execution.attempts - 1)
                if execution.escalated:
                    self.routing_stats["escalations"] += 1
                self.evidence.append(
                    kind="routing",
                    claim=f"subtask {execution.task.id} routed to {execution.node}",
                    status="verified" if execution.proposals else "failed",
                    raw_output=(
                        execution.guardrail_log + "\n\n[RAW MODEL OUTPUT]\n" + execution.raw_output
                    ).strip(),
                    metadata={
                        "reason": execution.route_reason,
                        "attempts": execution.attempts,
                        "verdict": execution.verdict,
                        "escalated": execution.escalated,
                        "failure_kind": execution.failure_kind,
                    },
                )

        def apply_result(candidate, phase: str) -> list[str]:
            changed: list[str] = []
            for proposal in candidate.proposals:
                args = dict(proposal.args)
                display_args = {
                    key: value for key, value in args.items() if key != "_nova_guardrail"
                }
                if emit_ui:
                    ui.print_tool_call(proposal.name, display_args)
                tool_result, success = self._tool_controller.execute(proposal.name, args)
                if emit_ui:
                    ui.print_tool_result(tool_result, success)
                events.append(
                    {
                        "type": "tool_call",
                        "name": proposal.name,
                        "args": display_args,
                        "result": tool_result,
                        "success": success,
                        "node": phase,
                        "guardrail": proposal.guardrail_summary,
                    }
                )
                if success:
                    path = str(display_args.get("path", ""))
                    if path:
                        changed.append(path)
            return changed

        breakdowns = [result.format_breakdown()]
        record_result(result, "initial")

        if not result.review_approved and result.review_findings:
            repair_analysis = {
                key: value for key, value in analysis.items() if key != "resume_plan"
            }
            focused_request = (
                f"{user_input}\n\nIndependent review rejected the candidate. "
                "Produce the smallest complete repair addressing only these findings:\n"
                + "\n".join(f"- {item}" for item in result.review_findings)
            )
            repair_result = backend.run(
                focused_request,
                planner_analysis=repair_analysis,
            )
            record_result(repair_result, "review_repair")
            breakdowns.append(repair_result.format_breakdown())
            result = repair_result

        changed_paths = apply_result(result, "two-node")
        applied = bool(changed_paths) and all(
            event.get("success", False) for event in events if event.get("type") == "tool_call"
        )
        recovered_without_edits = bool(
            result.review_approved
            and not result.proposals
            and result.execution_plan is not None
            and result.execution_plan.steps
            and all(step.status == TaskStatus.COMPLETED for step in result.execution_plan.steps)
            and any(
                execution.route_reason == "recovered verified checkpoint"
                for execution in result.executions
            )
        )
        if applied:
            security_result, security_ok = self._tool_controller.execute(
                "security_scan",
                {"paths": changed_paths},
            )
            events.append(
                {
                    "type": "tool_call",
                    "name": "security_scan",
                    "args": {"paths": changed_paths},
                    "result": security_result,
                    "success": security_ok,
                    "node": "nexus-verifier",
                }
            )
        if applied or recovered_without_edits:
            verification_report = self._record_verification_report(self._run_verification_suite())
            if emit_ui:
                ui.console.print(verification_report.format_report())
            if not verification_report.all_passed:
                repair_analysis = {
                    key: value for key, value in analysis.items() if key != "resume_plan"
                }
                focused_request = (
                    f"{user_input}\n\nThe candidate was applied in an isolated workspace, "
                    "but deterministic verification failed. Repair only the failing checks "
                    "and preserve already passing behavior.\n\n"
                    f"{verification_report.format_report()}"
                )
                repair_result = backend.run(
                    focused_request,
                    planner_analysis=repair_analysis,
                )
                record_result(repair_result, "verification_repair")
                breakdowns.append(repair_result.format_breakdown())
                if repair_result.review_approved:
                    apply_result(repair_result, "two-node-repair")
                    rerun = self._record_verification_report(self._run_verification_suite())
                    if emit_ui:
                        ui.console.print(rerun.format_report())

        breakdown = "\n\n".join(breakdowns)
        if emit_ui:
            ui.console.print(breakdown)
        breakdown = self._guard_completion_claims(breakdown)
        self.messages.append({"role": "assistant", "content": breakdown})
        self._auto_save()
        return breakdown, events

    def _run_nova_turn(self, user_input: str, emit_ui: bool = True) -> tuple[str, list[dict]]:
        """Run one turn through the local Nova pipeline backend."""
        from nexus.nova_backend import NovaBackendError, NovaPipelineBackend

        events: list[dict] = []
        self._load_rules_and_preferences()
        self.messages.append({"role": "user", "content": user_input})

        backend = NovaPipelineBackend(
            model=self.model_cfg.get("ollama_model", "nova_codex"),
            working_dir=self.working_dir,
        )

        try:
            if emit_ui:
                live = ui.LiveStatus()
                live.start("Running local worker...")
                try:
                    nova_result = backend.run(user_input)
                finally:
                    live.stop()
            else:
                nova_result = backend.run(user_input)
        except NovaBackendError as e:
            content = f"Nova guardrails blocked the output: {e}"
            if emit_ui:
                ui.print_error(content)
            if self.messages and self.messages[-1].get("role") == "user":
                self.messages.pop()
            raise RuntimeError(content) from e
        except (LookupError, OSError, RuntimeError) as e:
            content = f"Nova backend error: {e}"
            if emit_ui:
                ui.print_error(content)
            if self.messages and self.messages[-1].get("role") == "user":
                self.messages.pop()
            raise RuntimeError(content) from e

        self.routing_stats["nova_tasks"] += 1
        self.run_ledger.append_model_call(
            role="intern",
            model=self.model_cfg.get("ollama_model", "nova_codex"),
            status="completed" if nova_result.raw_output else "failed",
            detail=(
                f"guarded proposals={len(nova_result.proposals)}; "
                f"declared_test={bool(nova_result.test_command)}"
            ),
        )

        if emit_ui and nova_result.raw_output:
            ui.console.print(nova_result.raw_output)
        if emit_ui and nova_result.guardrail_output:
            ui.print_info("Nova guardrail verdicts:")
            ui.console.print(nova_result.guardrail_output)

        # Structured/headless callers receive the same complete model and
        # guardrail transcript that interactive users see.  This is evidence,
        # not a shortened summary, so rejected generations remain auditable.
        events.append(
            {
                "type": "model_trace",
                "node": "nova",
                "raw_output": nova_result.raw_output,
                "guardrail_output": nova_result.guardrail_output,
            }
        )
        events.append(
            {
                "type": "model_turn",
                "node": "nova",
                "proposals": len(nova_result.proposals),
                "declared_test": bool(nova_result.test_command),
            }
        )

        mutated = False
        proposal_failed = False
        for proposal in nova_result.proposals:
            args = dict(proposal.args)
            display_args = {k: v for k, v in args.items() if k != "_nova_guardrail"}
            if emit_ui:
                ui.print_tool_call(proposal.name, display_args)
            result, success = self._tool_controller.execute(proposal.name, args)
            if emit_ui:
                ui.print_tool_result(result, success)
            events.append(
                {
                    "type": "tool_call",
                    "name": proposal.name,
                    "args": display_args,
                    "result": result,
                    "success": success,
                    "nova_guardrail": proposal.guardrail_summary,
                }
            )
            if success and proposal.name in {
                "write_file",
                "edit_file",
                "patch_file",
                "multi_edit",
                "replace_file_content",
                "multi_replace_file_content",
                "write_to_file",
            }:
                mutated = True
            if not success:
                proposal_failed = True

        test_failed = False
        if mutated and not proposal_failed and nova_result.test_command:
            test_result, test_success, evidence_id = self._run_declared_test_command(
                nova_result.test_command,
                source="nova",
                emit_ui=emit_ui,
            )
            test_failed = not test_success
            events.append(
                {
                    "type": "tool_call",
                    "name": "run_command",
                    "args": {"command": nova_result.test_command},
                    "result": test_result,
                    "success": test_success,
                    "node": "nova-declared-test",
                    "evidence_id": evidence_id,
                }
            )

        final_text = nova_result.assistant_text
        if proposal_failed:
            final_text += (
                "\n\nOne or more guarded file operations failed; completion is unverified."
            )
        if test_failed:
            final_text += "\n\nThe model-declared acceptance test failed; completion is unverified."
        final_content = self._guard_completion_claims(final_text)
        if emit_ui:
            ui.print_response_complete()
        self.messages.append({"role": "assistant", "content": final_content})
        self._auto_save()
        return final_content, events

