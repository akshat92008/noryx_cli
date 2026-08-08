"""Cross-platform contracts for subprocess pipe and environment handling."""

from __future__ import annotations

from nexus.process_io import filtered_subprocess_env


def test_filtered_subprocess_env_keeps_windows_bootstrap_without_secrets(monkeypatch):
    monkeypatch.setenv("SYSTEMROOT", r"C:\\Windows")
    monkeypatch.setenv("COMSPEC", r"C:\\Windows\\System32\\cmd.exe")
    monkeypatch.setenv("PYTHONPATH", "unsafe-import-path")
    monkeypatch.setenv("NEXUS_TEST_SECRET", "do-not-inherit")

    env = filtered_subprocess_env()

    assert env["SYSTEMROOT"] == r"C:\\Windows"
    assert env["COMSPEC"] == r"C:\\Windows\\System32\\cmd.exe"
    assert "PYTHONPATH" not in env
    assert "NEXUS_TEST_SECRET" not in env


def test_filtered_subprocess_env_allows_explicit_configuration(monkeypatch):
    monkeypatch.setenv("PLUGIN_SETTING", "approved")

    env = filtered_subprocess_env(
        allowed_names=("plugin_setting",),
        overrides={"SERVER_SETTING": "configured"},
    )

    assert env["PLUGIN_SETTING"] == "approved"
    assert env["SERVER_SETTING"] == "configured"
