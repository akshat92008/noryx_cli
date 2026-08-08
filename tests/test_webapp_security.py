from pathlib import Path

import pytest

import nexus.webapp.server as server


def test_workspace_path(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(server, "_working_dir", str(tmp_path))

    # Valid paths
    assert server._workspace_path("file.txt").name == "file.txt"
    assert server._workspace_path(str(tmp_path / "folder" / "file.txt")).name == "file.txt"

    # Invalid paths (traversal)
    with pytest.raises(PermissionError):
        server._workspace_path("../outside.txt")

    with pytest.raises(PermissionError):
        server._workspace_path("/etc/passwd")


def test_is_sensitive_path():
    assert server._is_sensitive_path(Path(".env"))
    assert server._is_sensitive_path(Path(".env.local"))
    assert server._is_sensitive_path(Path(".git/config"))
    assert server._is_sensitive_path(Path(".ssh/id_rsa"))
    assert server._is_sensitive_path(Path("id_ed25519"))
    assert server._is_sensitive_path(Path("credentials.json"))

    # Valid paths
    assert not server._is_sensitive_path(Path("main.py"))
    assert not server._is_sensitive_path(Path("config.json"))
    assert not server._is_sensitive_path(Path("src/app.ts"))


def test_is_allowed_web_origin():
    # Allowed
    assert server._is_allowed_web_origin(None)
    assert server._is_allowed_web_origin("http://127.0.0.1:8000")
    assert server._is_allowed_web_origin("http://localhost:3000")

    # Blocked
    assert not server._is_allowed_web_origin("https://evil.com")
    assert not server._is_allowed_web_origin("http://192.168.1.5")
    assert not server._is_allowed_web_origin("http://0.0.0.0")
