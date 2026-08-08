import pytest
import os
from nexus.session import AgentSession
from nexus.context_selector import ContextSelector
from nexus.mutation import MutationController
from nexus.events import EventBus, EventType
from nexus.config.core import NexusConfig, get_config
from nexus.providers.base import ModelProvider

def test_config_system():
    config = get_config()
    assert isinstance(config, NexusConfig)
    
def test_event_system():
    events_received = []
    def handler(event):
        events_received.append(event)
    
    EventBus.subscribe(EventType.TASK_STARTED, handler)
    EventBus.publish(EventType.TASK_STARTED, "test-id", "test-component")
    
    assert len(events_received) == 1
    assert events_received[0].run_id == "test-id"

def test_controllers_exist():
    session = AgentSession("test task")
    assert session.status.name == "RUNNING"
    
    context = ContextSelector()
    assert context.gather_context("task")["workspace_status"] in ("Clean", "Modified")
    
    mutator = MutationController(".")
    assert hasattr(mutator, "write_file")

def test_agent_no_god_object():
    with open("nexus/agent/core.py", "r") as f:
        content = f.read()
        assert "subprocess.Popen" not in content, "Agent must not contain subprocess logic"
        assert "from nexus.two_node_backend" not in content, "Agent must not contain provider-specific code"
        assert "from nexus.nova_backend" not in content, "Agent must not contain provider-specific code"

