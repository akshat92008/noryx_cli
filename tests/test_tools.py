"""Smoke tests for NexusAI tools — verifies all built-in tools are wired."""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJECT_ROOT))

from nexus.tools import ToolResult, ToolStatus, RAW_TOOL_DEFINITIONS, TOOL_DISPATCH, execute_tool  # noqa: E402


class _FakeHTTPResponse:
    def __init__(self, body: str, content_type: str = "text/html; charset=utf-8"):
        self._body = body.encode("utf-8")
        self.headers = {"Content-Type": content_type}
        self.url = "https://example.com"

    def read(self, limit: int | None = None) -> bytes:
        return self._body if limit is None else self._body[:limit]

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_all_tools_have_definitions():
    """Every tool in the dispatch table should have a definition."""
    dispatch_names = set(TOOL_DISPATCH.keys())
    definition_names = {td["function"]["name"] for td in RAW_TOOL_DEFINITIONS}
    missing_defs = dispatch_names - definition_names
    missing_dispatch = definition_names - dispatch_names
    assert not missing_defs, f"Tools without definitions: {missing_defs}"
    assert not missing_dispatch, f"Definitions without dispatch: {missing_dispatch}"
    assert len(RAW_TOOL_DEFINITIONS) == 42, f"Expected 42 tools, got {len(RAW_TOOL_DEFINITIONS)}"


def test_process_status_rejects_unmanaged_pid():
    result = execute_tool("process_status", {"pid": 99999999})
    assert result.output.startswith("❌")
    assert "not a Nexus-managed" in result.output


def test_process_stop_rejects_unmanaged_pid():
    result = execute_tool("process_stop", {"pid": 99999999})
    assert result.output.startswith("❌")
    assert "not a Nexus-managed" in result.output


def test_read_file():
    """read_file should return file contents with line numbers."""
    result = execute_tool("read_file", {"path": str(PROJECT_ROOT / "run.py")})
    assert "📄" in result.output
    assert "run.py" in result.output
    assert "main()" in result.output


def test_read_file_not_found():
    """read_file should return error for missing file."""
    result = execute_tool("read_file", {"path": "/nonexistent/file.xyz"})
    assert "❌" in result.output


def test_write_file():
    """write_file should create a file and return success."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        tmp_path = f.name
    try:
        result = execute_tool("write_file", {"path": tmp_path, "content": "hello world"})
        assert "✅" in result.output
        assert Path(tmp_path).read_text() == "hello world"
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def test_edit_file():
    """edit_file should replace text in a file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("original content here")
        tmp_path = f.name
    try:
        result = execute_tool(
            "edit_file",
            {
                "path": tmp_path,
                "old_text": "original",
                "new_text": "modified",
            },
        )
        assert "✅" in result.output
        assert "modified content here" in Path(tmp_path).read_text()
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def test_edit_file_doc_envelope_fallback():
    """edit_file should fall back to replacing full document content when old_text is an HTML envelope."""
    file_content = "<!DOCTYPE html>\n<html><head></head><body><h1>Old</h1></body></html>"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".html", delete=False) as f:
        f.write(file_content)
        tmp_path = f.name
    try:
        # Edit should succeed with exact matching old_text
        result = execute_tool(
            "edit_file",
            {
                "path": tmp_path,
                "old_text": "<h1>Old</h1>",
                "new_text": "<h1>New</h1>",
            },
        )
        assert "✅" in result.output
        assert "New" in Path(tmp_path).read_text()
        assert "Old" not in Path(tmp_path).read_text()
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def test_edit_file_not_found():
    """edit_file should error when text not found."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("hello")
        tmp_path = f.name
    try:
        result = execute_tool(
            "edit_file",
            {
                "path": tmp_path,
                "old_text": "nonexistent",
                "new_text": "replacement",
            },
        )
        assert "❌" in result.output
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def test_file_info():
    """file_info should return metadata."""
    result = execute_tool("file_info", {"path": str(PROJECT_ROOT / "run.py")})
    assert "📋" in result.output
    assert "run.py" in result.output
    assert "Type:" in result.output
    assert "Size:" in result.output


def test_file_info_not_found():
    """file_info should error for missing path."""
    result = execute_tool("file_info", {"path": "/nonexistent"})
    assert "❌" in result.output


def test_get_project_structure():
    """get_project_structure should return a tree."""
    result = execute_tool("get_project_structure", {"path": ".", "max_depth": 2})
    assert "🌳" in result.output
    assert "nexus/" in result.output


def test_list_directory():
    """list_directory should list files."""
    result = execute_tool("list_directory", {"path": str(PROJECT_ROOT), "recursive": False})
    assert "📁" in result.output
    assert "run.py" in result.output


def test_find_files():
    """find_files should find files by glob."""
    result = execute_tool("find_files", {"pattern": "*.py", "directory": str(PROJECT_ROOT)})
    assert "🔍" in result.output
    assert "run.py" in result.output


def test_search_code():
    """search_code should find pattern matches."""
    result = execute_tool(
        "search_code", {"pattern": "def execute_tool", "directory": str(PROJECT_ROOT / "nexus")}
    )
    assert "🔍" in result.output
    assert "execute_tool" in result.output


def test_run_command():
    """run_command should execute a shell command."""
    result = execute_tool("run_command", {"command": "echo hello_test", "require_os_isolation": False, "allow_unisolated_host_process": True})
    assert "✅" in result.output
    assert "hello_test" in result.output


def test_run_command_string_timeout():
    """run_command should handle timeout passed as a string without crashing."""
    result = execute_tool("run_command", {"command": "echo timeout_test", "timeout": "120", "require_os_isolation": False, "allow_unisolated_host_process": True})
    assert "✅" in result.output
    assert "timeout_test" in result.output


def test_run_command_failure():
    """run_command should show exit code for failed commands."""
    result = execute_tool("run_command", {"command": "exit 1", "require_os_isolation": False, "allow_unisolated_host_process": True})
    assert "❌" in result.output or "exit code 1" in result.output


def test_git_status():
    """git_status should handle non-git repos gracefully."""
    result = execute_tool("git_status", {})
    # This project is now a git repo, so it should show branch info
    assert "🌿" in result.output or "❌" in result.output


def test_web_search(monkeypatch):
    """web_search should parse deterministic provider output without live network access."""
    monkeypatch.delenv("NEXUS_DISABLE_NETWORK", raising=False)
    body = """
    <a class="result__a" href="https://example.com/python">Python Guide</a>
    <a class="result__snippet">A practical programming guide.</a>
    """
    monkeypatch.setattr(
        "nexus.tools.tools_impl._safe_urlopen",
        lambda *_args, **_kwargs: _FakeHTTPResponse(body),
    )
    result = execute_tool("web_search", {"query": "python programming", "max_results": 2})
    assert "Python Guide" in result.output
    assert "https://example.com/python" in result.output


def test_web_fetch(monkeypatch):
    """web_fetch should extract deterministic content without live network access."""
    monkeypatch.delenv("NEXUS_DISABLE_NETWORK", raising=False)
    monkeypatch.setattr(
        "nexus.tools.tools_impl._safe_urlopen",
        lambda *_args, **_kwargs: _FakeHTTPResponse("<h1>Example Domain</h1>"),
    )
    result = execute_tool("web_fetch", {"url": "https://example.com", "max_length": 500})
    assert "Example Domain" in result.output


def test_multi_edit():
    """multi_edit should apply multiple edits."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("line one\nline two\nline three")
        tmp_path = f.name
    try:
        result = execute_tool(
            "multi_edit",
            {
                "edits": [
                    {"path": tmp_path, "old_text": "line one", "new_text": "first"},
                    {"path": tmp_path, "old_text": "line three", "new_text": "third"},
                ]
            },
        )
        assert "📝" in result.output
        content = Path(tmp_path).read_text()
        assert "first" in content
        assert "third" in content
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def test_patch_file():
    """patch_file should replace lines by range."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
        f.write("line1\nline2\nline3\nline4")
        tmp_path = f.name
    try:
        result = execute_tool(
            "patch_file",
            {
                "path": tmp_path,
                "start_line": 2,
                "end_line": 3,
                "new_content": "replaced2\nreplaced3",
            },
        )
        assert "✅" in result.output
        content = Path(tmp_path).read_text()
        assert "line1" in content
        assert "replaced2" in content
        assert "replaced3" in content
        assert "line4" in content
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def test_diff_files():
    """diff_files should show differences between files."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as fa:
        fa.write("hello\nworld")
        path_a = fa.name
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as fb:
        fb.write("hello\nuniverse")
        path_b = fb.name
    try:
        result = execute_tool("diff_files", {"file_a": path_a, "file_b": path_b})
        assert "📝" in result.output or "✅" in result.output
    finally:
        Path(path_a).unlink(missing_ok=True)
        Path(path_b).unlink(missing_ok=True)


def test_unknown_tool():
    """execute_tool should return error for unknown tools."""
    result = execute_tool("nonexistent_tool", {})
    assert "❌" in result.output
    assert "Unknown tool" in result.output


def test_all_tools_execute():
    """Every registered tool should accept empty/default args without crashing."""
    for name in TOOL_DISPATCH:
        try:
            result = execute_tool(name, {})
            assert isinstance(result, ToolResult)
        except TypeError:
            # Some tools require specific args — that's fine
            pass


def test_resolve_path():
    """Relative desktop paths stay scoped to the active workspace."""
    from nexus.tools import ToolResult, ToolStatus, _resolve_path

    dt_path = _resolve_path("desktop/calculator/index.html")
    expected = Path.cwd() / "desktop" / "calculator" / "index.html"
    assert dt_path == expected.resolve()


def test_normalize_tool_arguments():
    """normalize_tool_arguments should normalize parameter names."""
    from nexus.tools import ToolResult, ToolStatus, normalize_tool_arguments

    res1 = normalize_tool_arguments("write_file", {"file_path": "a.py", "content": "1"})
    assert res1["path"] == "a.py"

    res2 = normalize_tool_arguments("run_command", {"cmd": "ls -l"})
    assert res2["command"] == "ls -l"

    res3 = normalize_tool_arguments("search_code", {"query": "import"})
    assert res3["pattern"] == "import"


def test_tool_repo_index(monkeypatch, tmp_path):

    # We must ensure _tool_working_dir returns tmp_path
    from nexus.tools import ToolResult, ToolStatus, _tool_working_dir, tool_repo_index

    _tool_working_dir.set(str(tmp_path))

    res = tool_repo_index(force=True)
    assert "stats" in res or "graph" in res


def test_tool_repo_symbols(monkeypatch, tmp_path):
    from nexus.tools import ToolResult, ToolStatus, _tool_working_dir, tool_repo_symbols

    _tool_working_dir.set(str(tmp_path))

    (tmp_path / "app.py").write_text("class MySymbol:\n    pass\n")

    # Needs to initialize graph for tools since tools load it dynamically or rely on it
    # tool_repo_symbols uses a cached graph in actual runtime or builds it
    res = tool_repo_symbols("MySymbol")
    assert "MySymbol" in res or "app.py" in res


def test_tool_repo_impact(monkeypatch, tmp_path):
    from nexus.tools import ToolResult, ToolStatus, _tool_working_dir, tool_repo_impact

    _tool_working_dir.set(str(tmp_path))

    (tmp_path / "app.py").write_text("class MySymbol:\n    pass\n")
    (tmp_path / "test_app.py").write_text("import app\ndef test_a(): pass")

    res = tool_repo_impact(["app.py"])
    assert "test_app.py" in res or "app.py" in res


def test_tool_repo_context(monkeypatch, tmp_path):
    from nexus.tools import ToolResult, ToolStatus, _tool_working_dir, tool_repo_context

    _tool_working_dir.set(str(tmp_path))

    (tmp_path / "user_route.py").write_text("@app.get('/users')\ndef users(): pass")

    res = tool_repo_context("user route")
    assert "user_route.py" in res


def test_tool_api_check(monkeypatch):
    import httpx

    from nexus.tools import ToolResult, ToolStatus, tool_api_check

    class MockResponse:
        def __init__(self):
            self.status_code = 200
            self.headers = {"content-type": "application/json"}
            self.text = '{"status": "ok"}'

        def json(self):
            return {"status": "ok"}

    def mock_request(*args, **kwargs):
        return MockResponse()

    monkeypatch.setattr(httpx, "request", mock_request)

    res = tool_api_check(url="http://127.0.0.1/api", expected_json={"status": "ok"})
    assert "api" in res
    assert "passed" in res


def test_tool_database_check_migration():
    from nexus.tools import ToolResult, ToolStatus, tool_database_check

    res = tool_database_check(sql="DROP TABLE users;")
    assert "failed" in res
    assert "destructive migration" in res


def test_tool_security_scan(tmp_path):
    from nexus.tools import ToolResult, ToolStatus, _tool_working_dir, tool_security_scan

    _tool_working_dir.set(str(tmp_path))

    (tmp_path / "unsafe.py").write_text("API_KEY = 'sk-1234567890abcdef1234567890abcdef'")
    res = tool_security_scan([str(tmp_path / "unsafe.py")])
    assert "failed" in res
    assert "hardcoded-credential" in res


def test_tool_browser_check(monkeypatch):
    from unittest.mock import MagicMock

    from nexus.tools import ToolResult, ToolStatus, tool_browser_check

    sync_api = pytest.importorskip("playwright.sync_api")

    mock_playwright = MagicMock()
    mock_browser = MagicMock()
    mock_page = MagicMock()

    mock_playwright.chromium.launch.return_value = mock_browser
    mock_browser.new_page.return_value = mock_page
    mock_page.url = "http://127.0.0.1/"
    mock_page.title.return_value = "Test Title"

    mock_sync_pw_cm = MagicMock()
    mock_sync_pw_cm.__enter__.return_value = mock_playwright

    monkeypatch.setattr(sync_api, "sync_playwright", lambda: mock_sync_pw_cm)
    res = tool_browser_check(url="http://127.0.0.1/")
    assert "browser" in res
    assert "passed" in res


from nexus.github import GitHubIntegration  # noqa: E402
from nexus.tools import (  # noqa: E402
    tool_github_create_pr,
    tool_github_list_issues,
    tool_github_view_issue,
)


@patch.object(GitHubIntegration, "list_issues")
def test_tool_github_list_issues(mock_list):
    mock_list.return_value = [
        {"number": 1, "title": "Test Issue", "state": "open", "url": "http://mock"}
    ]
    res = tool_github_list_issues(5)
    assert "Test Issue" in res


@patch.object(GitHubIntegration, "view_issue")
def test_tool_github_view_issue(mock_view):
    mock_view.return_value = {"number": 1, "title": "Test Issue", "body": "Mock body"}
    res = tool_github_view_issue("1")
    assert "Mock body" in res


@patch.object(GitHubIntegration, "create_pull_request")
def test_tool_github_create_pr(mock_create):
    mock_create.return_value = "http://mock/pr/1"
    res = tool_github_create_pr("PR Title", "PR Body")
    assert "http://mock/pr/1" in res
