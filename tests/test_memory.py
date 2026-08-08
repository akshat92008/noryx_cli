"""
Tests for the memory module — conversation persistence and compaction.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from nexus.memory import ConversationMemory, _get_preview, compact_messages


def test_save_and_load_conversation():
    """Should save and load a conversation."""
    memory = ConversationMemory()
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!"},
    ]
    conv_id = memory.save_conversation(
        messages=messages,
        model_name="Test Model",
        model_id="test/model",
        working_dir="/tmp",
    )
    assert conv_id

    loaded = memory.load_conversation(conv_id)
    assert loaded is not None
    assert loaded["model_name"] == "Test Model"
    assert len(loaded["messages"]) == 2
    assert loaded["messages"][0]["content"] == "Hello"

    # Cleanup
    memory.delete_conversation(conv_id)


def test_load_nonexistent():
    """Loading a nonexistent conversation should return None."""
    memory = ConversationMemory()
    result = memory.load_conversation("nonexistent_id_12345")
    assert result is None


def test_list_conversations():
    """Should list conversations with metadata."""
    memory = ConversationMemory()
    convs = memory.list_conversations(limit=5)
    assert isinstance(convs, list)
    for conv in convs:
        assert "id" in conv
        assert "model_name" in conv
        assert "message_count" in conv
        assert "preview" in conv


def test_delete_conversation():
    """Should delete a conversation."""
    memory = ConversationMemory()
    messages = [{"role": "user", "content": "test"}]
    conv_id = memory.save_conversation(messages, "Test", "test/id", "/tmp")
    assert memory.delete_conversation(conv_id)
    assert not memory.delete_conversation(conv_id)  # Already deleted


def test_auto_save_skips_short():
    """auto_save should skip conversations with < 2 messages."""
    memory = ConversationMemory()
    # Should not raise
    memory.auto_save(
        messages=[{"role": "user", "content": "hi"}],
        model_name="Test",
        model_id="test/id",
        working_dir="/tmp",
        conv_id="test_skip",
    )
    # Should not create a file for 1 message
    result = memory.load_conversation("test_skip")
    assert result is None


def test_compact_messages():
    """Should compact long conversations."""
    messages = []
    for i in range(20):
        messages.append({"role": "user", "content": f"question {i}"})
        messages.append({"role": "assistant", "content": f"answer {i}"})

    compacted = compact_messages(messages, keep_recent=4)
    assert len(compacted) < len(messages)
    assert len(compacted) >= 4  # At least the recent ones

    # The compacted version should start with a summary
    assert "CONVERSATION SUMMARY" in compacted[0]["content"]


def test_compact_noop():
    """Compacting a short conversation should be a no-op."""
    messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    result = compact_messages(messages, keep_recent=10)
    assert result == messages


def test_get_preview():
    """Should extract preview from first user message."""
    messages = [
        {"role": "system", "content": "system prompt"},
        {"role": "user", "content": "This is a very long user message that should be truncated"},
    ]
    preview = _get_preview(messages, max_len=20)
    assert len(preview) <= 23  # 20 + "..."
    assert "This is" in preview


def test_get_preview_empty():
    """Should handle empty conversations."""
    preview = _get_preview([], max_len=80)
    assert "empty" in preview.lower()
