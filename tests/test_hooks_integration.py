from nexus.hooks.base import BaseHook, HookContext, HookEvent, HookFailurePolicy, HookType
from nexus.hooks.runner import HookRunner


class TouchFileHook(BaseHook):
    def __init__(self, name, target_event, touch_path):
        super().__init__()
        self.name = name
        self.hook_type = HookType.SHELL
        self.events = [target_event]
        self.priority = 50
        self.failure_policy = HookFailurePolicy.BLOCK
        self.touch_path = touch_path

    def get_command(self, context: HookContext) -> list[str]:
        return ["touch", self.touch_path]


def test_hook_actual_shell_execution(tmp_path):
    runner = HookRunner(str(tmp_path))
    touch_file = tmp_path / "touched_by_hook.txt"

    hook = TouchFileHook("touch_hook", HookEvent.AFTER_FILE_EDIT, str(touch_file))
    runner.register(hook)

    ctx = HookContext(event=HookEvent.AFTER_FILE_EDIT, file_path=str(tmp_path / "file.py"))
    results = runner.fire(HookEvent.AFTER_FILE_EDIT, ctx)

    assert len(results) == 1
    assert results[0].success is True

    # Verify the file was ACTUALLY created by the shell hook!
    assert touch_file.exists()
