"""ContextSelector — connected to RepositoryIntelligence Engine (Sprint 5)."""

from __future__ import annotations

from typing import Any, Dict
from pathlib import Path
from nexus.events import EventBus, EventType
from nexus.intelligence.repository.engine import RepositoryIntelligence


class ContextSelector:
    """Selects relevant repository context using the unified RepositoryIntelligence engine."""

    def __init__(self, workspace: Any = None):
        self.workspace = workspace
        root = getattr(workspace, "working_dir", getattr(workspace, "root", Path.cwd()))
        self.engine = RepositoryIntelligence(root)

    def gather_context(self, task: str) -> Dict[str, Any]:
        """Gather typed context for the given task description."""
        bundle = self.engine.context_bundle(task)

        context_data = {
            "task": task,
            "intent": bundle.task_intent.value,
            "relevant_files": [f.path for f in bundle.files],
            "symbols": [s.name for s in bundle.symbols],
            "tests": [t.test_file for t in bundle.tests],
            "workspace_status": "Clean" if not self.engine.git_changed_files() else "Modified",
            "formatted_prompt": bundle.to_formatted_prompt(),
        }

        EventBus.publish(
            EventType.CONTEXT_SELECTED,
            run_id="global",
            component="ContextSelector",
            metadata={"items_found": len(context_data["relevant_files"]), "intent": bundle.task_intent.value},
        )
        return context_data
