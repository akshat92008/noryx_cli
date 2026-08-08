import json
import logging
import re
import shlex
from typing import Any

from nexus import ui
from nexus.capabilities import (
    ToolCapability,
    ToolCapabilityDeclaration,
)

# Phase 3: Hooks, MCP & Plugins
# Phase 1: Core Engine Imports
from nexus.planner import TaskStatus
from nexus.reflection import ReflectionVerdict

# Phase 2: Skills & Subagents
from nexus.tools import TOOL_DEFINITIONS

logger = logging.getLogger(__name__)



class ToolManagerMixin:
    @staticmethod
    def _coerce_capabilities(values: Any) -> frozenset[ToolCapability]:
        aliases = {
            "filesystem_read": ToolCapability.FS_READ,
            "filesystem_write": ToolCapability.FS_WRITE,
            "process": ToolCapability.CMD_EXEC,
            "command": ToolCapability.CMD_EXEC,
            "git": ToolCapability.GIT_MUTATION,
        }
        capabilities: set[ToolCapability] = set()
        for raw in values or ():
            normalized = str(raw).strip().lower().replace("-", "_")
            if normalized in aliases:
                capabilities.add(aliases[normalized])
                continue
            try:
                capabilities.add(ToolCapability(normalized))
            except ValueError:
                logger.warning("Ignoring unknown tool capability declaration: %s", raw)
        return frozenset(capabilities)

    def _register_tool_capability(
        self,
        name: str,
        capabilities: frozenset[ToolCapability],
        description: str = "",
    ) -> None:
        existing = self._tool_capabilities.get(name)
        declaration = ToolCapabilityDeclaration(name, capabilities, description)
        if existing is not None and existing != declaration:
            raise ValueError(
                f"Tool capability conflict for {name}: existing={existing.to_dict()} "
                f"new={declaration.to_dict()}"
            )
        self._tool_capabilities[name] = declaration

    @staticmethod
    def _filesystem_argument_names(tool: Any) -> tuple[str, ...]:
        contract = getattr(tool, "filesystem", None) or getattr(
            tool, "filesystem_capabilities", None
        )
        if not isinstance(contract, dict):
            return ()
        values: list[str] = []
        for key in ("read_arguments", "write_arguments"):
            raw = contract.get(key, [])
            if isinstance(raw, str):
                raw = [raw]
            if isinstance(raw, (list, tuple, set)):
                values.extend(str(item).strip() for item in raw if str(item).strip())
        return tuple(dict.fromkeys(values))

    def _register_external_tool_capabilities(self) -> None:
        """Register capability contracts before external tools are exposed."""
        for plugin in self.plugin_loader.plugins.values():
            manifest = getattr(plugin, "_manifest", None)
            declared = self._coerce_capabilities(getattr(manifest, "capabilities", ()))
            for definition in plugin.get_tools():
                function = definition.get("function", definition) if isinstance(definition, dict) else {}
                name = str(function.get("name", "")) if isinstance(function, dict) else ""
                if not name:
                    continue
                if not declared:
                    logger.warning(
                        "Plugin tool %s is hidden because plugin %s declares no capabilities",
                        name,
                        getattr(plugin, "name", "unknown"),
                    )
                    continue
                self._register_tool_capability(name, declared, str(function.get("description", "")))

        for extension_tool in self.extensions.loaded("tools"):
            declared = self._coerce_capabilities(getattr(extension_tool, "capabilities", ()))
            if not declared:
                logger.warning(
                    "Extension tool %s is hidden because it declares no capabilities",
                    getattr(extension_tool, "name", "unknown"),
                )
                continue
            path_arguments = self._filesystem_argument_names(extension_tool)
            if declared & {ToolCapability.FS_READ, ToolCapability.FS_WRITE} and not path_arguments:
                logger.warning(
                    "Extension tool %s is hidden because filesystem capabilities require "
                    "a filesystem={read_arguments, write_arguments} contract",
                    getattr(extension_tool, "name", "unknown"),
                )
                continue
            self._register_tool_capability(
                extension_tool.name,
                declared,
                getattr(extension_tool, "description", ""),
            )
            if path_arguments:
                self._external_tool_path_arguments[extension_tool.name] = path_arguments

        try:
            for definition in self.mcp.get_all_tool_definitions():
                function = definition.get("function", {})
                name = str(function.get("name", ""))
                if name:
                    self._register_tool_capability(
                        name,
                        frozenset(
                            {
                                ToolCapability.NETWORK,
                                ToolCapability.EXTERNAL_EFFECTS,
                                ToolCapability.CONFIRMATION_REQUIRED,
                            }
                        ),
                        str(function.get("description", "")),
                    )
        except (LookupError, OSError, RuntimeError, TypeError, ValueError) as exc:
            logger.debug("MCP capability registration unavailable: %s", exc)

    def _get_tools(self) -> list[dict] | None:
        """Get tool definitions including built-in, plugin, and MCP tools."""
        if not self.model_cfg.get("supports_tools"):
            return None

        tools = list(TOOL_DEFINITIONS)

        # Plugin tools
        for plugin in self.plugin_loader.plugins.values():
            tools.extend(plugin.get_tools())

        for extension_tool in self.extensions.loaded("tools"):
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": extension_tool.name,
                        "description": extension_tool.description,
                        "parameters": extension_tool.input_schema,
                    },
                }
            )

        # MCP tools
        try:
            tools.extend(self.mcp.get_all_tool_definitions())
        except (OSError, ValueError) as exc:
            logger.debug("MCP tool definitions unavailable: %s", exc)

        configured_allowlist = set(self.allowed_tools)
        step_allowlist: set[str] = set()
        if self._active_plan is not None:
            current = next(
                (step for step in self._active_plan.steps if step.status == TaskStatus.IN_PROGRESS),
                None,
            )
            if current:
                step_allowlist.update(current.tools_needed)
                step_allowlist.update(
                    {
                        "read_file",
                        "search_code",
                        "list_directory",
                        "find_files",
                        "get_project_structure",
                        "repo_context",
                        "repo_symbols",
                        "repo_impact",
                    }
                )
        effective_allowlist = (
            configured_allowlist & step_allowlist
            if configured_allowlist and step_allowlist
            else configured_allowlist or step_allowlist
        )

        def permitted(definition: dict[str, Any]) -> bool:
            name = str(definition.get("function", {}).get("name", ""))
            return bool(
                name
                and name in self._tool_capabilities
                and (self.mode_policy.allow_shell_command or name != "run_command")
                and name not in self.disallowed_tools
                and (not effective_allowlist or name in effective_allowlist)
            )

        return [definition for definition in tools if permitted(definition)]

    def _format_live_tool_status(self, tool_calls_accum: dict[int, dict]) -> str:
        """Format real-time status message with line counts & byte counters while tool JSON streams."""
        if not tool_calls_accum:
            return f"[bold {ui.CYAN}]Thinking & Reasoning with {self.model_cfg['name']}...[/]"

        last_idx = max(tool_calls_accum.keys())
        tc = tool_calls_accum[last_idx]
        name = tc.get("name", "")
        raw_args = tc.get("arguments", "")


        m_path = re.search(r'"(?:path|file_path|file)"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)', raw_args)
        path_str = m_path.group(1) if m_path else ""

        m_cmd = re.search(r'"command"\s*:\s*"([^"\\]*(?:\\.[^"\\]*)*)', raw_args)
        cmd_str = m_cmd.group(1) if m_cmd else ""

        lines = raw_args.count("\n") + raw_args.count("\\n")
        chars = len(raw_args)

        if name in ("write_file", "create_file"):
            if path_str:
                return f"[bold {ui.ORANGE}]⚡ Stream-Drafting File:[/] [bold {ui.CYAN}]{path_str}[/] [bold {ui.GOLD}]({lines} lines / {chars:,} bytes)[/]"
            return f"[bold {ui.ORANGE}]⚡ Stream-Drafting Code File...[/] [bold {ui.GOLD}]({lines} lines / {chars:,} bytes)[/]"

        elif name in ("edit_file", "patch_file", "multi_edit"):
            if path_str:
                return f"[bold {ui.ORANGE}]⚡ Surgical Code Edit:[/] [bold {ui.CYAN}]{path_str}[/] [bold {ui.GOLD}]({lines} lines / {chars:,} bytes)[/]"
            return f"[bold {ui.ORANGE}]⚡ Preparing Surgical Code Edit...[/] [bold {ui.GOLD}]({chars:,} bytes)[/]"

        elif name in ("run_command", "run_process", "process_run"):
            if cmd_str:
                clean_cmd = cmd_str.replace("\\n", " ").replace("\n", " ")
                return f"[bold {ui.ORANGE}]⚡ Guarded Shell Execution:[/] [bold {ui.WHITE}]{clean_cmd[:65]}[/]"
            return f"[bold {ui.ORANGE}]⚡ Preparing Guarded Shell Execution...[/]"

        elif name:
            return f"[bold {ui.ORANGE}]⚡ Executing Tool Matrix:[/] [bold {ui.CYAN}]{name}[/] [bold {ui.GOLD}]({chars:,} bytes)[/]"

        return f"[bold {ui.CYAN}]Thinking & Reasoning with {self.model_cfg['name']}...[/]"

    def _handle_tool_calls_interactive(
        self, tool_calls: list[dict], *, emit_ui: bool = True
    ) -> tuple[list[dict], list[bool]]:
        """Execute tool calls, optionally rendering interactive progress."""
        results = []
        successes = []
        for tc in tool_calls:
            name = tc["name"]
            try:
                args = json.loads(tc["arguments"])
            except json.JSONDecodeError:
                args = {}

            if emit_ui:
                ui.print_tool_call(name, args)

            exec_msg = f"Executing {name}..."
            if name == "write_file":
                path_val = args.get("path", "")
                content_val = args.get("content", "") or ""
                lines_cnt = content_val.count("\n") + 1 if content_val else 0
                exec_msg = f"Writing {lines_cnt} lines to {path_val}..."
            elif name in ("edit_file", "patch_file"):
                path_val = args.get("path", "")
                exec_msg = f"Applying edit to {path_val}..."
            elif name in ("run_command", "run_process", "process_run"):
                cmd_val = args.get("command", "")
                if name == "run_process":
                    cmd_val = shlex.join(str(item) for item in args.get("argv", []))
                exec_msg = f"Running command: {cmd_val[:60]}..."

            if emit_ui:
                with ui.console.status(
                    f"[bold {ui.ORANGE}]⚡ {exec_msg}[/]", spinner="bouncingBar"
                ):
                    result, success = self._tool_controller.execute(name, args)
            else:
                result, success = self._tool_controller.execute(name, args)

            if emit_ui:
                ui.print_tool_result(result, success)

            # Reflection
            verdict = self.reflector.reflect(name, args, result)
            if emit_ui and verdict.verdict == ReflectionVerdict.ESCALATE:
                ui.print_warning(f"⚠ Reflection: {verdict.suggestion}")

            # Cap tool content for context memory efficiency
            truncated_res = result
            if len(result) > 6000:
                truncated_res = (
                    result[:3000]
                    + f"\n\n... [truncated {len(result) - 6000} chars for context efficiency] ...\n\n"
                    + result[-3000:]
                )

            results.append(
                {
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "content": truncated_res,
                }
            )
            successes.append(success)

        return results, successes

