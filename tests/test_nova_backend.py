"""Focused tests for Nexus' Nova adapter."""

import json

from nexus.nova_backend import (
    PROMPT_PATH,
    NovaBackendResult,
    NovaPipelineBackend,
    NovaToolProposal,
)
from nexus.nova_runtime import NovaOutputParser, extract_prompt_paths
from nexus.providers.nova import NovaProvider
from nexus.two_node_backend import TwoNodeBackend


def test_prompt_path_accepts_ceiling_punctuation():
    text = "MODIFY src/app.py: then update tests/test_app.py; verify src/worker.py)."
    assert PROMPT_PATH.findall(text) == [
        "src/app.py",
        "tests/test_app.py",
        "src/worker.py",
    ]


def test_prompt_path_ignores_framework_names():
    text = "Build a Next.js app with Node.js, Express.js, Vue.js, and Chart.js."
    assert extract_prompt_paths(text) == []


def test_prompt_path_keeps_explicit_repository_files():
    text = "Modify src/app.py and tests/test_app.py."
    assert extract_prompt_paths(text) == ["src/app.py", "tests/test_app.py"]


def test_verified_model_is_the_backend_default():
    assert NovaPipelineBackend().model == "nova_codex"


def test_nova_parser_accepts_versioned_json_patch_protocol():
    raw = json.dumps(
        {
            "schema": "nova.patch.v1",
            "thinking": "Implement the requested entrypoint.",
            "files": [
                {
                    "path": "src/main.py",
                    "action": "CREATE",
                    "language": "python",
                    "content": "def main():\n    print('ok')\n",
                }
            ],
            "test_command": "python src/main.py",
        }
    )
    parsed = NovaOutputParser().parse(raw)
    assert parsed.is_valid
    assert parsed.files[0].path == "src/main.py"
    assert parsed.files[0].action == "CREATE"
    assert parsed.test_command == "python src/main.py"
    assert NovaOutputParser.count_file_declarations(raw) == 1


def test_file_action_modify_without_patch_blocks_falls_back_to_write_file():
    from nexus.nova_runtime import FileAction

    backend = NovaPipelineBackend()
    action = FileAction(path="src/app.js", action="MODIFY", content="console.log('hello');")
    proposals = backend._file_action_to_tool_calls(action, "test guardrail summary")
    assert len(proposals) == 1
    assert proposals[0].name == "write_file"
    assert proposals[0].args["path"] == "src/app.js"
    assert proposals[0].args["content"] == "console.log('hello');"


def test_file_action_unified_diff_converts_to_edit_file():
    from nexus.nova_runtime import FileAction

    backend = NovaPipelineBackend()
    diff_content = (
        "--- a/todo.html\n"
        "+++ b/todo.html\n"
        "@@ -10,3 +10,4 @@\n"
        " <body>\n"
        "+    <h1>Title</h1>\n"
        " </body>\n"
    )
    action = FileAction(path="todo.html", action="MODIFY", content=diff_content)
    proposals = backend._file_action_to_tool_calls(action, "test summary")
    assert len(proposals) == 1
    assert proposals[0].name == "edit_file"
    assert proposals[0].args["path"] == "todo.html"
    assert proposals[0].args["old_text"] == "<body>\n</body>"
    assert proposals[0].args["new_text"] == "<body>\n    <h1>Title</h1>\n</body>"


def test_file_action_unclosed_create_diff_header_stripping():
    from nexus.nova_runtime import FileAction

    backend = NovaPipelineBackend()
    content = "<<<<<<<\n# \n=======\nclass Node:\n    pass\n"
    action = FileAction(path="lru_cache.py", action="CREATE", content=content)
    proposals = backend._file_action_to_tool_calls(action, "test summary")
    assert len(proposals) == 1
    assert proposals[0].name == "write_file"
    assert proposals[0].args["path"] == "lru_cache.py"
    assert proposals[0].args["content"] == "class Node:\n    pass"


def test_file_action_deduplicates_repetitive_loop_blocks():
    from nexus.nova_runtime import FileAction

    backend = NovaPipelineBackend()
    content = (
        "```python\n# File: api_client.py\n<<<<<<<\n# \n=======\nimport json\nclass APIClient:\n    pass\n>>>>># File: api_client.py\n"
        * 3
    )
    action = FileAction(path="api_client.py", action="MODIFY", content=content)
    proposals = backend._file_action_to_tool_calls(action, "test summary")
    assert len(proposals) == 1
    assert proposals[0].name == "write_file"
    assert proposals[0].args["path"] == "api_client.py"
    assert proposals[0].args["content"] == "import json\nclass APIClient:\n    pass"


def test_file_action_ignores_trailing_filename_label_artifacts():
    from nexus.nova_runtime import FileAction

    backend = NovaPipelineBackend()
    content = "<<<<<<<\n=======\nconst task = 1;\n>>>>>># File: task_queue.js\n task_queue.js"
    action = FileAction(path="task_queue.js", action="MODIFY", content=content)
    proposals = backend._file_action_to_tool_calls(action, "test summary")
    assert len(proposals) == 1
    assert proposals[0].name == "write_file"
    assert proposals[0].args["path"] == "task_queue.js"
    assert proposals[0].args["content"] == "const task = 1;"


def test_cleaned_protocol_is_materialized_before_compilation(tmp_path):
    from nexus.nova_runtime import FileAction

    backend = NovaPipelineBackend()
    action = FileAction(
        path="hello.py",
        action="CREATE",
        content=(
            "<<<<<<<\n# template placeholder\n=======\n"
            "def main():\n    print('hello')\n\n"
            "if __name__ == '__main__':\n    main()\n>>>>>>>"
        ),
    )
    proposals = backend._file_action_to_tool_calls(action, "guarded")
    paths, error = backend._materialize_proposals(proposals, tmp_path)
    assert not error
    assert paths == ["hello.py"]
    assert (tmp_path / "hello.py").read_text().startswith("def main")


def test_ceiling_nested_language_fence_is_recovered():
    backend = object.__new__(TwoNodeBackend)
    from nexus.nova_runtime import NovaOutputParser

    backend.parser = NovaOutputParser()
    parsed = backend._parse_ceiling_response(
        "```language\n# filepath: path/to/main.cpp\n# action: CREATE\n"
        "```cpp\nint main() { return 0; }\n```"
    )
    assert parsed.is_valid
    assert parsed.files[0].path == "main.cpp"
    assert parsed.files[0].content == "int main() { return 0; }"


def test_nova_provider_exposes_common_response_contract_and_stops_after_tools(tmp_path):
    provider = NovaProvider("nova_codex", str(tmp_path))
    backend_result = NovaBackendResult(
        raw_output="raw",
        assistant_text="prepared guarded edit",
        guardrail_output="validated",
        test_command="python verify.py",
        proposals=[
            NovaToolProposal(
                name="write_file",
                args={"path": "answer.py", "content": "ANSWER = 42\n"},
                source_path="answer.py",
                guardrail_summary="validated",
            )
        ],
    )
    provider._backend.run = lambda _prompt: backend_result

    response = provider.chat_sync("local/nova", [{"role": "user", "content": "build"}])
    assert response.choices[0].message.content == "prepared guarded edit"
    calls = response.choices[0].message.tool_calls
    assert [item.function.name for item in calls] == ["write_file", "run_command"]
    assert json.loads(calls[1].function.arguments)["command"] == "python verify.py"

    follow_up = provider.chat_sync(
        "local/nova",
        [
            {"role": "user", "content": "build"},
            {"role": "tool", "tool_call_id": "nova-call-0", "content": "ok"},
        ],
    )
    assert follow_up.choices[0].message.tool_calls == []

    chunk = next(provider.chat("local/nova", [{"role": "user", "content": "build"}], stream=True))
    assert chunk.choices[0].delta.content == "prepared guarded edit"
    assert chunk.choices[0].delta.tool_calls
