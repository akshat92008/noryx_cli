"""
Rich terminal UI — beautiful output for the coding agent.
"""

import os

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table

from nexus.models import list_models

console = Console()

# ── Color palette ────────────────────────────────────────────────────────────

CYAN = "#00f0ff"  # Neon Electric Cyan
MAGENTA = "#d946ef"  # Deep Fuchsia
GREEN = "#10b981"  # Emerald Mint Green
ORANGE = "#f59e0b"  # Warm Amber Orange
RED = "#f43f5e"  # Rose Coral Red
DIM = "#64748b"  # Slate Grey
WHITE = "#f8fafc"  # Off-White
GOLD = "#fbbf24"  # Gold Yellow
PURPLE = "#8b5cf6"  # Electric Violet


def print_banner():
    """Print the gorgeous animated startup banner."""
    import time

    from rich.align import Align
    from rich.live import Live
    from rich.text import Text

    frames = [
        # Frame 1 (eyes open)
        r"""
  [bold #d946ef]▀▄   ▄▀[/]  
 [bold #00f0ff]▄█▀███▀█▄[/] 
[bold #00f0ff]█▀███████▀█[/]
[bold #00f0ff]█ [bold #fbbf24]█▀▀▀▀▀█[/] █[/]
  [bold #d946ef]▀▀   ▀▀[/]  
""",
        # Frame 2 (eyes closed)
        r"""
  [bold #d946ef]▀▄   ▄▀[/]  
 [bold #00f0ff]▄█▀███▀█▄[/] 
[bold #00f0ff]█▀███████▀█[/]
[bold #00f0ff]█ [bold #fbbf24]█▀   ▀█[/] █[/]
  [bold #d946ef]▀▀   ▀▀[/]  
"""
    ]
    
    welcome_text = "Welcome to Nexus!"
    
    with Live(refresh_per_second=15, transient=False, console=console) as live:
        for i in range(12):
            frame_idx = i % 2
            
            # Typewriter effect for welcome text
            text_len = min(len(welcome_text), int((i / 8) * len(welcome_text)))
            current_text = welcome_text[:text_len]
            
            output = frames[frame_idx] + f"\n[bold white]{current_text}[/]"
            live.update(Align.center(Text.from_markup(output)))
            time.sleep(0.06)
            
        # Final state
        final_output = (
            frames[0] + 
            f"\n[bold white]{welcome_text}[/]\n\n"
            f"[dim]Run /help for commands. /status for setup info.[/]\n"
        )
        live.update(Align.center(Text.from_markup(final_output)))
    console.print()


def print_model_info(model_key: str, model_cfg: dict):
    """Print current model info inside a premium rounded panel."""
    tools_str = (
        f"[bold {GREEN}]Enabled[/]"
        if model_cfg.get("supports_tools")
        else f"[bold {RED}]Disabled[/]"
    )
    content = (
        f"  [bold {WHITE}]{model_cfg['name']}[/]  [dim]•[/]  [italic {DIM}]{model_cfg['id']}[/]\n"
        f"  [bold {DIM}]Desc:[/] {model_cfg['description']}\n\n"
        f"  [bold {CYAN}]Context Limit:[/] [bold {WHITE}]{model_cfg['context']:,}[/] tokens    "
        f"[bold {CYAN}]Tool Execution:[/] {tools_str}"
    )
    console.print(
        Panel(
            content,
            title=f" [bold {PURPLE}]✦ active model configuration ✦[/] ",
            title_align="left",
            border_style=DIM,
            box=box.ROUNDED,
            padding=(1, 2),
        )
    )


def print_help():
    """Print command help in a premium, clean table format."""
    table = Table(
        show_header=True,
        header_style=f"bold {CYAN}",
        border_style=DIM,
        box=box.SIMPLE_HEAD,
        title=f"[bold {GOLD}]✦ Available CLI Commands[/]",
    )
    table.add_column("Command", style=f"bold {PURPLE}", min_width=22)
    table.add_column("Description", style=WHITE)

    commands = [
        ("/help", "Show this command helper menu"),
        ("/models", "List available Ceiling models and the local Nova Intern option"),
        (
            "/model <name>",
            "Switch the active Ceiling model (e.g. /model kimi; /model nova_codex for local-only Nova)",
        ),
        ("/tools", "List all 38 built-in developer tools"),
        ("/clear", "Clear session conversation history"),
        ("/reset", "Reset the conversation session and clear terminal"),
        ("/project", "Print structural tree of the working directory"),
        ("/git", "Check the repository Git status"),
        ("/undo [N]", "Revert the last N file operations"),
        ("/rewind [N]", "Rewind file state by N tracked operations"),
        ("/diff", "View the unified diff of the last change"),
        ("/changes", "Summary list of all files changed in session"),
        ("/pending", "List file diffs awaiting approval"),
        ("/apply <id>", "Apply an exact pending file diff"),
        ("/reject <id>", "Reject a pending file diff without changing disk"),
        ("/edit-pending <id> <file>", "Replace pending content from a reviewed local file"),
        ("/confirm <id>", "Explicitly execute the exact pending dangerous operation"),
        ("/cancel <id>", "Cancel a pending dangerous operation without executing it"),
        ("/history", "Browse saved local conversation logs"),
        ("/resume <id>", "Load and resume a previous conversation"),
        ("/compact", "Compress history context length to save tokens"),
        ("/cost", "Report token usage statistics for the session"),
        ("/run-status", "Show the durable run, checkpoint, report, and worktree state"),
        ("/rollback-run", "Roll back every file change made by the current run"),
        ("/system <prompt>", "Override the agent's base system prompt"),
        ("/save <file>", "Export conversation history as JSON"),
        ("/multi", "Open multi-line text input mode (end with Ctrl+D)"),
        ("/skills", "Show active functional automation skills"),
        ("/hooks", "List active workspace lifecycle hooks"),
        ("/subagent <temp> <task>", "Spawn a specialized agent to run isolated work"),
        ("/verify [N]", "Re-read artifacts for the last N evidence-backed claims"),
        ("/verify project", "Run real workspace lint/test/build commands"),
        ("/permissions <mode>", "Set default, acceptEdits, or read-only plan mode"),
        ("/trust [approve|reject] <path>", "Review and approve an exact config digest"),
        ("/init", "Create a project NEXUS.md instructions file"),
        ("/context", "Show architecture and active context summaries"),
        ("/plan", "Enter read-only plan mode"),
        ("/mcp", "List connected Model Context Protocol servers"),
        ("/plugins", "List loaded plugins and extensions"),
        ("/rules", "Display project instructions loaded from NEXUS.md"),
        ("/exit, /quit", "Close session and exit NexusAI"),
    ]
    for cmd, desc in commands:
        table.add_row(cmd, desc)
    console.print(table)
    console.print(
        "The Ceiling model plans and handles complex or ambiguous work. Nova 3B handles "
        "well-specified subtasks fast and free, locally. A guardrail layer automatically "
        "catches and either corrects or escalates Nova's known failure modes.",
        style=DIM,
    )
    console.print()


def print_tools_table():
    """Print all available agent tools in a clean layout."""
    from nexus.tools import TOOL_DEFINITIONS

    table = Table(
        show_header=True,
        header_style=f"bold {CYAN}",
        border_style=DIM,
        box=box.SIMPLE_HEAD,
        title=f"[bold {GOLD}]✦ Integrated Agent Tools ({len(TOOL_DEFINITIONS)})[/]",
    )
    table.add_column("Tool Name", style=f"bold {WHITE}", min_width=20)
    table.add_column("Layer/Category", style=PURPLE)
    table.add_column("Action & Purpose", style=DIM)

    tools = [
        ("read_file", "File IO", "Retrieve file content with exact line numbers"),
        ("write_file", "File IO", "Create or completely overwrite a file (undoable)"),
        ("edit_file", "File IO", "Apply surgical find-and-replace edits (undoable)"),
        ("patch_file", "File IO", "Edit specific line range sequences (undoable)"),
        ("multi_edit", "File IO", "Apply batch edits across multiple files (undoable)"),
        ("file_info", "File IO", "Read size, permissions, line count, and MD5"),
        ("diff_files", "File IO", "Calculate unified diff between two files"),
        ("search_code", "Search", "Perform regex patterns search across codebase"),
        ("list_directory", "Search", "Retrieve folder listing, recursively if wanted"),
        ("find_files", "Search", "Find files using shell-like glob patterns"),
        ("get_project_structure", "Search", "Print hierarchical directory structures"),
        ("run_command", "Sandbox", "Execute blocking shell commands inside workspace"),
        ("process_run", "Sandbox", "Run background processes and return PID"),
        ("git_status", "Version Control", "Check repository status (branch, modified)"),
        ("git_diff", "Version Control", "Review current workspace/index/commit diffs"),
        ("git_commit", "Version Control", "Add changes and commit with descriptive messages"),
        ("git_log", "Version Control", "Browse repository commit logs"),
        ("git_branch", "Version Control", "List, create, or delete Git branches"),
        ("web_fetch", "Web Client", "Fetch external URL content and convert to markdown"),
        ("web_search", "Web Client", "Search the web anonymously via DuckDuckGo"),
        ("repo_index", "RepoGraph", "Incrementally index repository structure and symbols"),
        ("repo_symbols", "RepoGraph", "Find declarations, callers, and impacted tests"),
        ("repo_impact", "RepoGraph", "Map dependency and test impact for changed files"),
        ("repo_context", "RepoGraph", "Rank task-relevant files using repository evidence"),
        ("repo_routes", "RepoGraph", "Discover API and UI routes"),
        ("repo_models", "RepoGraph", "Discover database and ORM models"),
        ("repo_navigate", "Language Intel", "Use LSP, Tree-sitter, or RepoGraph navigation"),
        ("run_process", "Sandbox", "Run a typed argv command without a shell"),
        ("process_status", "Sandbox", "Poll a Nexus-managed background process"),
        ("process_stop", "Sandbox", "Stop a Nexus-managed background process"),
        ("api_check", "Verification", "Validate a local HTTP API contract"),
        ("database_check", "Verification", "Validate SQLite integrity and foreign keys"),
        ("security_scan", "Verification", "Run bounded deterministic security checks"),
        ("browser_check", "Verification", "Run an optional Playwright workflow"),
    ]

    category_colors = {
        "File IO": GREEN,
        "Search": CYAN,
        "Sandbox": ORANGE,
        "Version Control": MAGENTA,
        "Web Client": PURPLE,
        "RepoGraph": CYAN,
        "Language Intel": CYAN,
        "Verification": GOLD,
    }

    for name, cat, desc in tools:
        color = category_colors.get(cat, WHITE)
        table.add_row(name, f"[{color}]{cat}[/]", desc)
    console.print(table)
    console.print()


def print_models_table():
    """Print all available models in a beautiful, narrow-terminal-friendly table."""
    import shutil

    term_width = shutil.get_terminal_size((120, 40)).columns
    narrow = term_width < 100

    table = Table(
        show_header=True,
        header_style=f"bold {CYAN}",
        border_style=DIM,
        box=box.SIMPLE_HEAD,
        title=f"[bold {GOLD}]✦ Available Models — Hosted + Local Catalog[/]",
        width=min(term_width - 2, 140),
    )
    table.add_column("Key", style=f"bold {PURPLE}", min_width=14, no_wrap=True)
    table.add_column("Model Name", style=f"bold {WHITE}", no_wrap=narrow)
    table.add_column("Category", style=CYAN, no_wrap=True)
    table.add_column("Context", style=ORANGE, justify="right", no_wrap=True)
    table.add_column("Tools", justify="center", no_wrap=True)
    # Sandbox indicator: 🔒 means the mode requires OS isolation (bubblewrap / sandbox-exec)
    table.add_column("Sandbox", justify="center", no_wrap=True)
    if not narrow:
        table.add_column("Description", style=DIM, max_width=50)

    category_colors = {
        "reasoning": MAGENTA,
        "coding": GREEN,
        "general": CYAN,
        "local": ORANGE,
    }
    # Models that can be used without a native sandbox (plan/review mode)
    _no_sandbox_models = frozenset({"glm-5.2", "kimi", "deepseek", "custom"})

    for m in list_models():
        cat_color = category_colors.get(m["category"], WHITE)
        tools = f"[bold {GREEN}]✓[/]" if m.get("supports_tools") else f"[{RED}]✗[/]"
        model_key = m["key"]
        needs_sandbox = model_key not in _no_sandbox_models and m.get("category") != "local"
        sandbox_icon = f"[{DIM}]opt[/]" if not needs_sandbox else f"[{ORANGE}]🔒[/]"
        if m.get("category") == "local":
            sandbox_icon = f"[{GREEN}]✓[/]"
        row = [
            model_key,
            m["name"],
            f"[{cat_color}]{m['category']}[/]",
            f"{m['context']:,}",
            tools,
            sandbox_icon,
        ]
        if not narrow:
            row.append(m["description"])
        table.add_row(*row)

    console.print(table)
    if narrow:
        console.print(
            f"  [dim]Terminal is narrow ({term_width} cols) — description column hidden. "
            "Resize or run in a wider terminal to see full details.[/]"
        )
    console.print(
        f"  [{DIM}]🔒 = native sandbox required (bubblewrap on Linux / sandbox-exec on macOS). "
        "Run [bold]nexus --doctor[/] to check your sandbox status.[/]"
    )
    console.print()


def print_conversation_list(conversations: list[dict]):
    """Print a list of saved conversations."""
    table = Table(
        show_header=True,
        header_style=f"bold {CYAN}",
        border_style=DIM,
        box=box.SIMPLE_HEAD,
        title=f"[bold {GOLD}]✦ Saved Conversations[/]",
    )
    table.add_column("Conversation ID", style=f"bold {GREEN}", min_width=22)
    table.add_column("Model Name", style=PURPLE)
    table.add_column("Messages", style=ORANGE, justify="right")
    table.add_column("Preview Snippet", style=WHITE, max_width=60)

    for conv in conversations:
        table.add_row(
            conv["id"],
            conv.get("model_name", "unknown"),
            str(conv.get("message_count", 0)),
            conv.get("preview", "")[:60],
        )
    console.print(table)
    console.print(f"  [{DIM}]Use /resume <id> to load a previous session[/]")
    console.print()


class LiveStatus:
    """Stream-safe live status manager to show active model thinking & tool drafting."""

    def __init__(self, console_obj=None):
        self.console = console_obj or console
        self._status = None
        self._is_active = False

    def start(self, message: str = "Thinking..."):
        if not self._is_active:
            try:
                self._status = self.console.status(f"[{DIM}]{message}[/]", spinner="bouncingBar")
                self._status.start()
                self._is_active = True
            except LookupError:
                pass

    def update(self, message: str):
        if self._is_active and self._status:
            try:
                self._status.update(f"[{DIM}]{message}[/]")
            except LookupError:
                pass
        else:
            self.start(message)

    def stop(self):
        if self._is_active and self._status:
            try:
                self._status.stop()
            except (OSError, ValueError):
                pass
            self._status = None
            self._is_active = False


def print_tool_call(name: str, args: dict):
    """Print a tool call in a clean, unified modern format."""
    args_list = []
    for k, v in args.items():
        if k in ("content", "old_text", "new_text", "new_content") and len(str(v)) > 80:
            v_display = str(v)[:80].replace("\n", "\\n") + f"... ({len(str(v))} chars)"
        elif k == "edits" and isinstance(v, list):
            v_display = f"[{len(v)} edits]"
        else:
            v_display = str(v).replace("\n", "\\n")
        v_display = escape(v_display)
        args_list.append(f"[bold {DIM}]{k}=[/][#bbf7ff]{v_display}[/]")

    extra = ""
    if name in ("write_file", "edit_file", "patch_file"):
        content = args.get("content", "") or args.get("new_text", "") or args.get("new_content", "")
        if content:
            lines = content.count("\n") + 1
            extra = f" [{GOLD}]({lines} lines, {len(content):,} chars)[/]"

    args_str = ", ".join(args_list)
    console.print(f"  [bold {ORANGE}]⚙ Calling tool:[/] [bold {WHITE}]{name}[/]({args_str}){extra}")


def print_tool_result(result: str, success: bool = True):
    """Print a tool execution result cleanly with minimal spacing and formatting."""
    if success:
        display = str(result).strip()
        if not display:
            console.print(f"    [bold {GREEN}]✓ Success[/] [dim](no output returned)[/]")
            return

        if (
            display.startswith("✅ Wrote")
            or display.startswith("✅ Edited")
            or display.startswith("✅ Patched")
        ):
            console.print(
                f"    [bold {GREEN}]✓ Success:[/] [bold {WHITE}]{escape(display.replace('✅ ', ''))}[/]"
            )
            return

        lines = display.splitlines()
        if len(lines) <= 4:
            console.print(f"    [bold {GREEN}]✓ Success:[/] [dim]{escape(display)}[/]")
        else:
            preview = "\n".join(lines[:8])
            if len(lines) > 8:
                preview += f"\n[bold {DIM}]... ({len(lines) - 8} more lines of output) ...[/]"

            console.print(
                Panel(
                    escape(preview),
                    border_style=DIM,
                    box=box.MINIMAL,
                    padding=(0, 2),
                )
            )
    else:
        console.print(
            f"    [bold {RED}]✗ Tool Error:[/] [italic {RED}]{escape(str(result).strip())}[/]"
        )


def print_streaming_start():
    """Print a divider before streaming starts."""
    console.print(Rule(style=DIM))


def print_response_complete():
    """Print a divider after response is complete."""
    console.print()


def print_error(message: str):
    """Print an error message."""
    console.print(f"  [bold {RED}]✗[/] [{WHITE}]{escape(str(message))}[/]")


def print_info(message: str):
    """Print an info message."""
    console.print(f"  [{CYAN}]✦[/] [{WHITE}]{escape(str(message))}[/]")


def print_success(message: str):
    """Print a success message."""
    console.print(f"  [{GREEN}]✓[/] [{WHITE}]{escape(str(message))}[/]")


def print_warning(message: str):
    """Print a warning."""
    console.print(f"  [{ORANGE}]⚠[/] [{WHITE}]{escape(str(message))}[/]")


def print_token_usage(prompt_tokens: int, completion_tokens: int, total_tokens: int):
    """Print token usage stats in a clean panel."""
    table = Table(
        show_header=False,
        border_style=DIM,
        box=box.SIMPLE,
        padding=(0, 2),
    )
    table.add_column("Metric", style=f"bold {CYAN}")
    table.add_column("Value", style=WHITE, justify="right")
    table.add_row("Prompt tokens", f"{prompt_tokens:,}")
    table.add_row("Completion tokens", f"{completion_tokens:,}")
    table.add_row("Total tokens", f"[bold {GREEN}]{total_tokens:,}[/]")
    console.print(
        Panel(
            table,
            title=f" [bold {GOLD}]📊 Session Token Usage[/] ",
            border_style=DIM,
            padding=(0, 1),
        )
    )


_prompt_session = None


def get_prompt(model_name: str) -> str:
    """Get user input with a styled prompt supporting persistent history."""
    global _prompt_session
    try:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.formatted_text import HTML
        from prompt_toolkit.history import FileHistory

        if _prompt_session is None:
            # Save command history to a hidden file in user's home directory
            history_file = os.path.expanduser("~/.nexus_cli_history")
            _prompt_session = PromptSession(history=FileHistory(history_file))

        console.print()
        pwd = os.path.basename(os.getcwd()) or "workspace"
        console.print(
            f" [bold {CYAN}]nexusai[/] [dim]•[/] [bold {PURPLE}]{model_name}[/] [dim]•[/] [bold {GREEN}]{pwd}[/]"
        )

        # Use HTML formatting in prompt_toolkit for consistent cyan color
        return _prompt_session.prompt(HTML("<ansicyan> ❯ </ansicyan>"))

    except ImportError:
        # Fallback to standard input if prompt_toolkit isn't loaded
        try:
            console.print()
            pwd = os.path.basename(os.getcwd()) or "workspace"
            console.print(
                f" [bold {CYAN}]nexusai[/] [dim]•[/] [bold {PURPLE}]{model_name}[/] [dim]•[/] [bold {GREEN}]{pwd}[/]"
            )
            return console.input(f" [bold {CYAN}]❯ [/]")
        except (EOFError, KeyboardInterrupt):
            return "/exit"
    except (KeyboardInterrupt, EOFError):
        # Handle Ctrl+C and Ctrl+D gracefully from prompt_toolkit
        return "/exit"


def get_multiline_input() -> str:
    """Get multi-line input from user (end with Ctrl+D)."""
    console.print(f"  [{DIM}]Enter multi-line input (Ctrl+D to submit, Ctrl+C to cancel):[/]")
    lines = []
    try:
        while True:
            line = console.input(f"  [{DIM}]… [/]")
            lines.append(line)
    except EOFError:
        return "\n".join(lines)
    except KeyboardInterrupt:
        return ""
