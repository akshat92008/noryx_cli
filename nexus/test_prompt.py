"""Manual prompt-toolkit smoke test.

This module is safe to import; the interactive demo runs only when executed as a
script so release import-all qualification cannot block on stdin.
"""
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.history import InMemoryHistory
from prompt_toolkit.key_binding import KeyBindings


def build_key_bindings() -> KeyBindings:
    bindings = KeyBindings()

    @bindings.add("enter")
    def _submit(event):
        event.current_buffer.validate_and_handle()

    @bindings.add("escape", "enter")
    def _newline(event):
        event.current_buffer.insert_text("\n")

    return bindings


def main() -> int:
    session = PromptSession(history=InMemoryHistory())
    try:
        print("Type something and press Enter:")
        result = session.prompt(HTML("<ansicyan> ❯ </ansicyan>"), key_bindings=build_key_bindings())
        print("Result:", repr(result))
        return 0
    except (EOFError, KeyboardInterrupt, OSError, ValueError) as exc:
        print("Error:", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
