from __future__ import annotations

import json
from pathlib import Path

from nexus.agent import Agent
from nexus.multifile.persistence import ChangeSetPersistence
from nexus.policy import get_mode_policy


def _agent(tmp_path: Path, monkeypatch) -> tuple[Agent, Path]:
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    monkeypatch.setenv("NEXUS_HOME", str(tmp_path / "nexus-home"))
    monkeypatch.setenv("HOME", str(tmp_path / "user-home"))
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.py").write_text("A = 1\n", encoding="utf-8")
    (root / "b.py").write_text("B = 1\n", encoding="utf-8")
    policy = get_mode_policy("quality")
    policy.require_os_isolation = False
    agent = Agent(
        working_dir=str(root),
        permission_mode="acceptEdits",
        mode_policy=policy,
        workspace_isolation=False,
    )
    agent.run_ledger.begin("Modify a.py and b.py atomically")
    return agent, root


def _multi_edit(agent: Agent):
    return agent._execute_tool_with_safety(
        "multi_edit",
        {
            "edits": [
                {"path": "a.py", "old_text": "A = 1", "new_text": "A = 2"},
                {"path": "b.py", "old_text": "B = 1", "new_text": "B = 2"},
            ]
        },
        _user_initiated=True,
        _edit_confirmed=True,
    )


def test_multi_edit_persists_change_set_before_and_after_mutation(tmp_path: Path, monkeypatch):
    agent, root = _agent(tmp_path, monkeypatch)
    output, success = _multi_edit(agent)
    assert success, output
    persisted = agent.run_ledger.turn_dir / "change-set" / "change-set.json"
    data = json.loads(persisted.read_text(encoding="utf-8"))
    assert data["repository_snapshot_id"]
    assert data["applied_file_paths"] == ["a.py", "b.py"]
    assert [item["path"] for item in data["file_changes"]] == ["a.py", "b.py"]
    assert (root / "a.py").read_text(encoding="utf-8") == "A = 2\n"
    assert (root / "b.py").read_text(encoding="utf-8") == "B = 2\n"


def test_change_set_finalization_failure_rolls_back_all_files(tmp_path: Path, monkeypatch):
    agent, root = _agent(tmp_path, monkeypatch)
    original = ChangeSetPersistence.save_change_set
    calls = {"count": 0}

    def fail_second_save(self, change_set):
        calls["count"] += 1
        if calls["count"] == 2:
            raise OSError("simulated finalization failure")
        return original(self, change_set)

    monkeypatch.setattr(ChangeSetPersistence, "save_change_set", fail_second_save)
    output, success = _multi_edit(agent)
    assert not success
    assert "Rollback succeeded" in output
    assert (root / "a.py").read_text(encoding="utf-8") == "A = 1\n"
    assert (root / "b.py").read_text(encoding="utf-8") == "B = 1\n"
    assert agent.history.changes == []


def test_authenticated_state_failure_rolls_back_verified_edit(tmp_path: Path, monkeypatch):
    agent, root = _agent(tmp_path, monkeypatch)

    def reject_state(_changes):
        raise OSError("simulated authenticated state failure")

    monkeypatch.setattr(agent.engineering_brain, "record_changes", reject_state)
    output, success = agent._execute_tool_with_safety(
        "edit_file",
        {"path": "a.py", "old_text": "A = 1", "new_text": "A = 2"},
        _user_initiated=True,
        _edit_confirmed=True,
    )
    assert not success
    assert "Rollback succeeded" in output
    assert (root / "a.py").read_text(encoding="utf-8") == "A = 1\n"
    assert agent.history.changes == []
