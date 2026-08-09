import pytest
import os
from pathlib import Path
from nexus.tools.tools_impl import tool_edit_file, tool_write_file, tool_read_file, tool_context

def test_file_mutation_correctness(tmp_path):
    # Empty file
    empty_file = tmp_path / "empty.txt"
    empty_file.write_text("")
    with tool_context(tmp_path):
        res = tool_edit_file(str(empty_file), "", "new")
        assert "✅" in res
        assert empty_file.read_text() == "new"

    # No trailing newline
    no_newline = tmp_path / "no_newline.txt"
    no_newline.write_text("line1\nline2")
    with tool_context(tmp_path):
        res = tool_edit_file(str(no_newline), "line2", "line2\nline3")
        assert "✅" in res
        assert no_newline.read_text() == "line1\nline2\nline3"

def test_binary_file_safety(tmp_path):
    binary_file = tmp_path / "image.png"
    binary_file.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00")
    
    with tool_context(tmp_path):
        # Noryx must fail cleanly when attempting to edit a binary file
        res = tool_edit_file(str(binary_file), "IHDR", "IHDR2")
        assert "❌" in res
        # Must not be corrupted
        assert binary_file.read_bytes() == b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00"

def test_special_filenames(tmp_path):
    # Spaces, unicode, emoji, leading dash
    names = [
        "file with spaces.txt",
        "ünicöde.txt",
        "🚀.txt",
        "-leading-dash.txt",
        "quotes\"'.txt"
    ]
    
    for name in names:
        p = tmp_path / name
        with tool_context(tmp_path):
            tool_write_file(str(p), "hello")
            assert p.read_text() == "hello"
            
            res = tool_edit_file(str(p), "hello", "world")
            assert "✅" in res
            assert p.read_text() == "world"
