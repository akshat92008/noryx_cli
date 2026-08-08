"""End-to-End workflow test covering prompt -> model -> tools -> verification -> final result."""

import json
import subprocess
import sys

from nexus.agent import Agent
from nexus.policy import get_mode_policy
from nexus.providers.base import Provider


class FullFakeProvider(Provider):
    def __init__(self, responses):
        self.responses = responses
        self.call_count = 0

    @property
    def id(self):
        return "fake_e2e"

    @property
    def name(self):
        return "Fake E2E Provider"

    def count_tokens(self, text):
        return len(text)

    def chat(
        self,
        model_id,
        messages,
        tools=None,
        stream=False,
        max_tokens=None,
        temperature=None,
        **kwargs,
    ):
        if self.call_count >= len(self.responses):

            class DummyMessage:
                content = "I am done."
                tool_calls = []

            class DummyChoice:
                message = DummyMessage()

            class DummyResponse:
                choices = [DummyChoice()]

            if stream:

                def _stream():
                    class DummyChunk:
                        choices = [
                            type(
                                "Choice",
                                (),
                                {
                                    "delta": type(
                                        "Delta", (), {"content": "I am done.", "tool_calls": []}
                                    )()
                                },
                            )()
                        ]

                    yield DummyChunk()

                return _stream()
            return DummyResponse()

        resp = self.responses[self.call_count]
        self.call_count += 1

        if stream:

            def _stream():
                class DummyChunk:
                    choices = [
                        type(
                            "Choice",
                            (),
                            {
                                "delta": type(
                                    "Delta",
                                    (),
                                    {
                                        "content": resp.get("content", ""),
                                        "tool_calls": resp.get("tool_calls", []),
                                    },
                                )()
                            },
                        )()
                    ]

                yield DummyChunk()

            return _stream()
        else:

            class DummyMessage:
                content = resp.get("content", "")
                tool_calls = resp.get("tool_calls", [])

            class DummyChoice:
                message = DummyMessage()

            class DummyResponse:
                choices = [DummyChoice()]

            return DummyResponse()

    def chat_sync(
        self, model_id, messages, tools=None, max_tokens=None, temperature=None, **kwargs
    ):
        if tools is None and any(
            "independent, read-only senior code reviewer" in str(message.get("content", ""))
            for message in messages
        ):
            class ReviewMessage:
                content = (
                    '{"approved": true, "summary": "The targeted fix is correct and the '
                    'full test suite passes.", "findings": []}'
                )
                tool_calls = []

            class ReviewChoice:
                message = ReviewMessage()

            class ReviewResponse:
                choices = [ReviewChoice()]

            return ReviewResponse()
        return self.chat(
            model_id,
            messages,
            tools,
            stream=False,
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )


class DummyToolCall:
    def __init__(self, name, arguments):
        self.id = f"call_{name}_{id(self)}"
        self.index = 0
        self.function = type("Func", (), {"name": name, "arguments": arguments})()


def test_full_autonomous_agent_workflow(tmp_path, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "fake_key")
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()

    (repo_dir / "pyproject.toml").write_text(
        "[project]\nname='e2e-math'\nversion='0.0.0'\n"
        "[tool.pytest.ini_options]\ntestpaths=['.']\n",
        encoding="utf-8",
    )

    # Create flawed script
    math_py = repo_dir / "my_math.py"
    math_py.write_text("def add(a, b):\n    return a - b\n")

    # Create failing test
    test_math_py = repo_dir / "test_math.py"
    test_math_py.write_text(
        "import my_math\n\ndef test_add():\n    assert my_math.add(2, 2) == 4\n"
    )

    # Initialize Git repository
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=repo_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True, capture_output=True
    )
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"], cwd=repo_dir, check=True, capture_output=True
    )

    # Define tool calls
    read_call = DummyToolCall("read_file", json.dumps({"path": "my_math.py"}))
    edit_call = DummyToolCall(
        "multi_edit",
        json.dumps(
            {
                "edits": [
                    {
                        "path": "my_math.py",
                        "old_text": "def add(a, b):\n    return a - b\n",
                        "new_text": "def add(a, b):\n    return a + b\n",
                    }
                ]
            }
        ),
    )
    reproduce_call = DummyToolCall(
        "run_process",
        json.dumps(
            {
                "argv": [sys.executable, "-m", "pytest", "-q"],
                "cwd": ".",
            }
        ),
    )
    test_call = DummyToolCall(
        "run_process",
        json.dumps(
            {
                "argv": [sys.executable, "-m", "pytest", "-q"],
                "cwd": ".",
            }
        ),
    )

    # Sequence of responses simulating a real model session
    fake_provider = FullFakeProvider(
        [
            {"tool_calls": [read_call]},
            {"tool_calls": [reproduce_call]},
            {"tool_calls": [edit_call]},
            {"tool_calls": [test_call]},
            {"content": "I have fixed the math function and the full test suite now passes."},
        ]
    )

    # Create autonomous agent with workspace isolation. The test explicitly
    # disables the kernel-sandbox requirement because the CI host may not have
    # bubblewrap; containment is covered by dedicated sandbox tests.
    test_policy = get_mode_policy("autonomous")
    test_policy.require_os_isolation = False
    test_policy.allow_shell_command = False
    agent = Agent(
        working_dir=str(repo_dir),
        permission_mode="acceptEdits",
        mode_policy=test_policy,
        workspace_isolation=True,
        max_turns=10,
        allow_unisolated_host_process=True,
    )

    agent._should_use_two_node = lambda analysis: False
    agent.client = fake_provider

    prompt = (
        "Fix the bug in my_math.py where add is subtracting. "
        "Then verify it by running pytest test_math.py."
    )
    content, events = agent.run_non_interactive(prompt)
    report = agent.export_final_report()

    print("EVENTS:", events)
    print("REPORT:", report)
    assert report["status"] == "VERIFIED", report
    assert any(
        event.get("type") == "tool_call"
        and event.get("name") == "multi_edit"
        and event.get("success")
        for event in events
    )
    assert any(
        event.get("type") == "tool_call"
        and event.get("name") == "run_process"
        and event.get("success")
        for event in events
    )
    assert "fixed" in content.lower()

    # VERIFIED completion applies the isolated worktree through the normal
    # product path. The source repository must contain the tested mutation.
    assert "return a + b" in math_py.read_text(encoding="utf-8")

